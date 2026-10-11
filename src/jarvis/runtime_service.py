"""Small single-host scheduler around finite, currently authorized domain operations."""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from jarvis.application import build_application
from jarvis.core.time import utc_now
from jarvis.identity import IdentityError
from jarvis.local_credentials import LocalDevelopmentIdentityRepository, read_private_json
from jarvis.observability import AuditWriteError
from jarvis.storage.errors import StorageError
from jarvis.storage.sqlite import SCHEMA_VERSION, _file_lock

STATES = {"STARTING", "READY", "DEGRADED", "STOPPING", "STOPPED", "FAILED"}


def authoritative_failure(exc):
    code = getattr(exc, "code", getattr(exc, "reason_code", ""))
    return isinstance(exc, (StorageError, AuditWriteError)) or (
        code in {"storage_unavailable", "audit_unavailable", "record_corrupt"}
        or code.startswith(("security_", "database_"))
        or code.endswith("_corrupt")
    )


@dataclass
class JobStatus:
    running: bool = False
    passes: int = 0
    failures: int = 0
    last_success: str | None = None
    duration_ms: float = 0
    error: str | None = None


class RuntimeService:
    def __init__(self, settings, *, application=None):
        self.settings, self.application = settings, application
        self.state = "STOPPED"
        self.database_available = False
        self.model_available = False
        self.startup_ms = 0
        self.jobs = {
            name: JobStatus()
            for name in (
                "initiatives",
                "initiative_recovery",
                "notification_bridge",
                "notifications",
                "notification_recovery",
                "tasks",
                "maintenance",
                "probe",
            )
        }
        self.worker_tokens = ()
        self._stop = asyncio.Event()
        self._loop_task = None
        self._claim = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-scheduler")
        self._request_executor = ThreadPoolExecutor(
            max_workers=settings.max_requests, thread_name_prefix="jarvis-request"
        )
        self._active_requests = set()
        self._active_jobs = set()
        self._startup_task = None
        self._task_cursors = {}
        self._maintenance_cursors = {}
        self.drain_timed_out = False
        self._shutdown_deadline = None
        self.log = logging.getLogger("jarvis")

    def _workers(self):
        path = self.settings.worker_credentials_file
        if path is None:
            return ()
        data = read_private_json(path)
        if (
            not isinstance(data, dict)
            or set(data) != {"tokens"}
            or not isinstance(data["tokens"], list)
            or not 1 <= len(data["tokens"]) <= self.settings.max_worker_principals
        ):
            raise ValueError("worker_credentials_invalid")
        tokens, principals = [], set()
        for token in data["tokens"]:
            session = self.application.identities.resolve_kind(token, "worker")
            if session.principal_id in principals:
                raise ValueError("worker_principal_duplicate")
            principals.add(session.principal_id)
            tokens.append(token)
        return tuple(tokens)

    async def start(self):
        self._startup_task = asyncio.current_task()
        try:
            async with asyncio.timeout(self.settings.startup_timeout_seconds):
                await self._start()
        except TimeoutError:
            self.drain_timed_out = True
            raise StorageError("runtime_startup_timeout") from None
        except asyncio.CancelledError:
            await self.stop()
            raise StorageError("runtime_startup_interrupted") from None
        finally:
            self._startup_task = None

    def _construct(self, path):
        self.application = self.application or build_application(
            path,
            provider=self.settings.model_provider,
            endpoint=self.settings.local_model_endpoint,
            model=self.settings.local_model_name,
            timeout=self.settings.model_timeout_seconds,
            identities_factory=LocalDevelopmentIdentityRepository,
            initialize=False,
            notification_channels=None if self.settings.simulated_notification_channels else (),
        )
        self.application.identities.validate_authority()
        self.worker_tokens = self._workers()

    async def _start(self):
        started = time.monotonic()
        self.state = "STARTING"
        try:
            path = Path(self.settings.resolved_runtime_database_path).expanduser().resolve()
            # No directory/db creation, migration or fixture fallback on serve.
            if not path.is_file():
                raise StorageError("runtime_database_missing")
            self._claim = _file_lock(path.with_name(path.name + ".runtime.lock"))
            self._claim.__enter__()
            await self.call(self._construct, path)
            self.database_available = True
            # One bounded initial pass before readiness; no independent privileged runner.
            for name in ("tasks", "initiative_recovery", "notification_recovery"):
                await self.run_job(name)
                if self.state == "FAILED":
                    raise StorageError("runtime_recovery_failed")
            await self.run_job("probe")
            self.startup_ms = (time.monotonic() - started) * 1000
            self.application.metrics.record("runtime.startup_ms", self.startup_ms)
            self._refresh_state()
            self._loop_task = asyncio.create_task(self._schedule(), name="jarvis-scheduler")
            self._loop_task.add_done_callback(self._scheduler_done)
            self.log.info("runtime.started", extra={"reason_code": self.state.lower()})
        except BaseException as exc:
            self.state = "FAILED"
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._request_executor.shutdown(wait=False, cancel_futures=True)
            if isinstance(exc, asyncio.CancelledError):
                self._stop.set()
            else:
                self._release()
            raise

    def _release(self):
        if self._claim is not None:
            claim, self._claim = self._claim, None
            claim.__exit__(None, None, None)

    async def call(self, operation, *args, **kwargs):
        if self.state in {"STOPPING", "STOPPED", "FAILED"}:
            raise RuntimeError("runtime_not_ready")
        if len(self._active_requests) >= self.settings.max_requests:
            raise RuntimeError("runtime_busy")
        future = asyncio.get_running_loop().run_in_executor(
            self._request_executor, partial(operation, *args, **kwargs)
        )
        self._active_requests.add(future)
        future.add_done_callback(self._active_requests.discard)
        # HTTP cancellation must not erase an in-flight thread/effect from drain tracking.
        return await asyncio.shield(future)

    async def call_timed(self, metric, operation, *args, **kwargs):
        started = time.monotonic()
        try:
            return await self.call(operation, *args, **kwargs)
        finally:
            self.application.metrics.record(metric, (time.monotonic() - started) * 1000)

    def begin_shutdown(self):
        if self._shutdown_deadline is None:
            self._shutdown_deadline = time.monotonic() + self.settings.shutdown_timeout_seconds
        self.state = "STOPPING"
        self._stop.set()

    def _scheduler_done(self, task):
        if not self._stop.is_set() and self.state not in {"STOPPING", "STOPPED", "FAILED"}:
            self.state = "FAILED"
            self._stop.set()
            self.log.error(
                "runtime.scheduler_stopped", extra={"reason_code": "scheduler_unavailable"}
            )

    def _refresh_state(self):
        if self.state in {"STOPPING", "STOPPED", "FAILED"}:
            return
        self.state = (
            "DEGRADED"
            if (
                not self.model_available
                or not self.worker_tokens
                or any(j.error for j in self.jobs.values())
            )
            else "READY"
        )

    def public_health(self):
        scheduler_running = self._loop_task is not None and not self._loop_task.done()
        return {
            "schema_version": 1,
            "state": self.state,
            "live": self.state != "STOPPED",
            "ready": self.state in {"READY", "DEGRADED"}
            and self.database_available
            and scheduler_running,
            "database_available": self.database_available,
            "schema_compatible": self.database_available,
            "model_available": self.model_available,
            "scheduler_running": scheduler_running,
            "last_worker_success": max(
                (j.last_success for k, j in self.jobs.items() if k != "probe" and j.last_success),
                default=None,
            ),
        }

    def diagnostics(self):
        return {
            **self.public_health(),
            "storage_schema": SCHEMA_VERSION,
            "startup_ms": self.startup_ms,
            "workers": len(self.worker_tokens),
            "jobs": {k: vars(v).copy() for k, v in self.jobs.items()},
            "metrics": self.application.metrics.samples,
            "simulated_invocations": {
                "lighting": len(self.application.lighting.calls),
                "notice": len(self.application.notices.calls),
            },
        }

    def operational_summary(self, token):
        tasks = self.application.tasks.list(session_token=token, limit=100, unfinished=True)
        notices, _ = self.application.notifications.list(session_token=token, limit=100)
        pending_deliveries = 0
        for notice in notices:
            pending_deliveries += sum(
                d.state in {"PENDING", "READY", "SENDING", "RETRY_WAIT"}
                for d in self.application.notifications.deliveries(
                    notice.notification_id, session_token=token
                )
            )
        return {
            **self.diagnostics(),
            "owned_backlog_sample": {
                "unfinished_tasks": len(tasks),
                "pending_deliveries": pending_deliveries,
                "task_sample_may_be_truncated": len(tasks) == 100,
                "notification_sample_may_be_truncated": len(notices) == 100,
                "sample_limit": 100,
            },
        }

    def _pass(self, name):
        app, size = self.application, self.settings.worker_batch_size
        # Validate authoritative storage every pass. Fail closed if it is unavailable.
        with app.database.transaction():
            _ = app.identities.owner_id
        failures = []
        for token in self.worker_tokens:
            if self.state in {"STOPPING", "FAILED"}:
                break
            try:
                session = app.identities.resolve_kind(token, "worker")
                if name == "initiatives":
                    app.initiatives.process(session_token=token, limit=size)
                elif name == "initiative_recovery":
                    app.initiatives.recover(session_token=token, limit=size)
                elif name == "notification_bridge":
                    app.notifications.bridge(session_token=token, limit=size)
                elif name in {"notifications", "notification_recovery"}:
                    operation = (
                        app.notifications.recover
                        if name.endswith("recovery")
                        else app.notifications.process
                    )
                    operation(session_token=token, limit=size)
                elif name == "tasks":
                    cursor = self._task_cursors.get(session.principal_id)
                    records = app.tasks.list(
                        session_token=token, limit=size, after=cursor, unfinished=True
                    )
                    self._task_cursors[session.principal_id] = (
                        records[-1].task_id if records else None
                    )
                    for task in records:
                        if self.state in {"STOPPING", "FAILED"}:
                            break
                        # Domain-specific guards own admission, rates and quiet hours.
                        # Their recovery jobs finalize linked work; do not run them early here.
                        if task.execution_guard_ref is None:
                            result = app.tasks.run(task.task_id, session_token=token, recovery=True)
                            if result.status == "storage_failure":
                                raise StorageError(result.error.code)
                elif name == "maintenance":
                    app.world.query(session_token=token, limit=min(size, 20))
                    cursor = self._maintenance_cursors.get(session.principal_id, 0)
                    records, after = app.notifications.list(
                        session_token=token, limit=size, after=cursor
                    )
                    self._maintenance_cursors[session.principal_id] = after if records else 0
                    for record in records:
                        if record.expires_at <= utc_now() and record.inbox_state != "EXPIRED":
                            app.notifications.interact(
                                record.notification_id,
                                "expire",
                                expected_revision=record.revision,
                                session_token=token,
                            )
            except StorageError:
                raise
            except IdentityError as exc:
                if exc.reason_code.startswith("security_"):
                    raise
                failures.append("worker_authority_unavailable")
            except Exception as exc:
                if authoritative_failure(exc):
                    raise StorageError("authoritative_state_unavailable") from None
                # Codes only, no provider/credential/content exception text.
                failures.append("worker_operation_failed")
        if failures:
            raise RuntimeError(failures[0])

    async def _probe(self):
        await self.call(lambda: self.application.identities.owner_id)
        self.database_available = True
        if self.settings.model_provider == "fake":
            self.model_available = True
            return
        self.model_available = False
        self.model_available = await self.application.models._providers["local"].available()

    async def run_job(self, name):
        status, started = self.jobs[name], time.monotonic()
        if status.running:
            return
        status.running = True
        try:
            if name == "probe":
                await self._probe()
            else:
                future = asyncio.get_running_loop().run_in_executor(
                    self._executor, self._pass, name
                )
                self._active_jobs.add(future)
                future.add_done_callback(self._active_jobs.discard)
                await asyncio.shield(future)
            status.passes += 1
            status.last_success, status.error = utc_now().isoformat(), None
        except (StorageError, IdentityError):
            status.failures += 1
            status.error = "authoritative_state_unavailable"
            self.database_available = False
            self.state = "FAILED"
            self._stop.set()
        except Exception:
            status.failures += 1
            status.error = "worker_operation_failed"
            self.log.warning("runtime.worker_failed", extra={"reason_code": name})
        finally:
            status.running = False
            status.duration_ms = (time.monotonic() - started) * 1000
            self.application.metrics.record("runtime.scheduler_pass_ms", status.duration_ms)
            if name in {"notifications", "notification_recovery"}:
                self.application.metrics.record("notification.process_ms", status.duration_ms)
            self.log.debug("runtime.worker_pass", extra={"reason_code": name})
            self._refresh_state()

    async def _schedule(self):
        due = dict.fromkeys(self.jobs, 0.0)
        intervals = {
            "initiatives": self.settings.initiative_interval_seconds,
            "initiative_recovery": self.settings.recovery_interval_seconds,
            "notification_bridge": self.settings.notification_interval_seconds,
            "notifications": self.settings.notification_interval_seconds,
            "notification_recovery": self.settings.recovery_interval_seconds,
            "tasks": self.settings.recovery_interval_seconds,
            "maintenance": self.settings.maintenance_interval_seconds,
            "probe": self.settings.recovery_interval_seconds,
        }
        while not self._stop.is_set():
            for name in self.jobs:
                if self._stop.is_set():
                    break
                if time.monotonic() >= due[name]:
                    await self.run_job(name)
                    due[name] = time.monotonic() + intervals[name]
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=0.05)
            except TimeoutError:
                pass

    async def stop(self):
        self.begin_shutdown()
        deadline = self._shutdown_deadline
        if self._loop_task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._loop_task), timeout=max(0, deadline - time.monotonic())
                )
            except TimeoutError:
                self.drain_timed_out = True
                self.state = "FAILED"
        active = self._active_requests | self._active_jobs
        if active:
            _, pending = await asyncio.wait(active, timeout=max(0, deadline - time.monotonic()))
            if pending:
                self.drain_timed_out, self.state = True, "FAILED"
        self._executor.shutdown(wait=not self.drain_timed_out, cancel_futures=True)
        self._request_executor.shutdown(wait=not self.drain_timed_out, cancel_futures=True)
        if not self.drain_timed_out:
            self._release()
            self.state = "STOPPED"
        self.log.info("runtime.stopped", extra={"reason_code": self.state.lower()})
