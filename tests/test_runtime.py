"""Lifecycle, bounded workers, failure isolation, config and real-process operations."""

import asyncio
import json
import signal
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from jarvis.core.task_records import TaskStep
from jarvis.identity import IdentityError
from jarvis.local_api import create_api
from jarvis.local_credentials import (
    LocalDevelopmentIdentityRepository,
    export_curl_configuration,
    read_private_json,
    revoke_credential,
    write_private_json,
)
from jarvis.runtime_cli import doctor, initialize_development
from jarvis.runtime_config import RuntimeSettings
from jarvis.runtime_demo import _environment, _wait, api_data, running_runtime, settle_action
from jarvis.runtime_service import RuntimeService
from jarvis.storage.errors import StorageError
from jarvis.storage.sqlite import MIGRATIONS, SCHEMA_VERSION, SQLiteDatabase
from jarvis.tasks import TaskBusyError


@pytest.fixture
def configured(tmp_path):
    manifest = initialize_development(
        RuntimeSettings(data_dir=tmp_path / "dev", model_provider="fake")
    )
    settings = RuntimeSettings.load(Path(manifest["config"])).model_copy(
        update={
            "initiative_interval_seconds": 0.05,
            "notification_interval_seconds": 0.05,
            "recovery_interval_seconds": 0.05,
            "maintenance_interval_seconds": 0.05,
            "worker_batch_size": 1,
        }
    )
    return settings, manifest


def test_single_composition_start_stop_and_runtime_claim(configured):
    settings, _ = configured

    async def run():
        first, second = RuntimeService(settings), RuntimeService(settings)
        await first.start()
        try:
            assert first.state == "READY" and first.public_health()["ready"]
            app = first.application
            assert app.identities is app.tasks.identities is app.memory._identities
            assert app.models is app.coordinator.models and app.executor is app.tasks.executor
            with pytest.raises(TaskBusyError):
                await second.start()
            assert second.state == "FAILED"
        finally:
            await first.stop()
        assert first.state == "STOPPED"
        reopened = RuntimeService(settings)
        await reopened.start()
        await reopened.stop()

    asyncio.run(run())


def test_demo_waits_for_durable_scheduler_result_and_replays_without_new_effect(configured):
    settings, manifest = configured
    runtime = RuntimeService(settings)
    token = read_private_json(Path(manifest["credentials"]["owner"]["path"]))["token"]
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_api(runtime), base_url="http://127.0.0.1:8765") as client:
        runtime.application.coordinator.defer_actions = True
        conversation = api_data(
            client.post("/api/v1/conversations", headers=headers, json={"schema_version": 1})
        )
        path = f"/api/v1/conversations/{conversation['conversation_id']}/messages"
        body = {"schema_version": 1, "request_id": str(uuid4()), "text": "Set lights to 25"}
        initial = api_data(client.post(path, headers=headers, json=body))
        assert initial["kind"] == "pending"
        result = settle_action(client, path, body, headers, initial)
        assert result["kind"] == "action" and result["task_state"] == "COMPLETED"
        assert result["task_id"] == initial["task_id"]
        assert result["execution"]["output"] == {"brightness": 25, "simulated": True}
        assert api_data(client.post(path, headers=headers, json=body)) == result
        assert runtime.application.lighting.calls == [25]


def test_startup_cancellation_drains_live_construction_and_releases_claim(configured):
    settings, _ = configured

    async def run():
        runtime = RuntimeService(settings)
        started, release = threading.Event(), threading.Event()
        original = runtime._construct

        def slow(path):
            started.set()
            release.wait(5)
            original(path)

        runtime._construct = slow
        task = asyncio.create_task(runtime.start())
        while not started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done() and runtime._claim is not None
        release.set()
        with pytest.raises(StorageError, match="runtime_startup_interrupted"):
            await task
        assert runtime.state == "STOPPED" and runtime._claim is None

    asyncio.run(run())


def test_startup_deadline_keeps_claim_until_live_construction_exits(configured):
    settings, _ = configured

    async def run():
        runtime = RuntimeService(settings.model_copy(update={"startup_timeout_seconds": 1}))
        release = threading.Event()
        original = runtime._construct

        def slow(path):
            release.wait(5)
            original(path)

        runtime._construct = slow
        try:
            with pytest.raises(StorageError, match="runtime_startup_timeout"):
                await runtime.start()
            assert runtime.state == "FAILED" and runtime.drain_timed_out
            assert runtime._claim is not None and runtime._active_requests
            other = RuntimeService(settings)
            with pytest.raises(TaskBusyError):
                await other.start()
        finally:
            release.set()
            if runtime._active_requests:
                await asyncio.gather(*runtime._active_requests)
            runtime._release()

    asyncio.run(run())


def test_missing_or_future_storage_never_bootstraps(tmp_path):
    missing = tmp_path / "absent" / "db.sqlite3"

    async def run():
        runtime = RuntimeService(RuntimeSettings(database_path=missing, model_provider="fake"))
        with pytest.raises(StorageError, match="runtime_database_missing"):
            await runtime.start()
        assert not missing.exists() and not missing.parent.exists()

    asyncio.run(run())
    database = SQLiteDatabase(tmp_path / "future.sqlite3")
    database.initialize()
    with sqlite3.connect(database.path) as c:
        c.execute("PRAGMA user_version=999")

    async def future():
        runtime = RuntimeService(
            RuntimeSettings(database_path=database.path, model_provider="fake")
        )
        with pytest.raises((IdentityError, StorageError)):
            await runtime.start()
        assert runtime.state == "FAILED"

    asyncio.run(future())
    with sqlite3.connect(database.path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 999
        assert c.execute("SELECT count(*) FROM security_authority").fetchone()[0] == 0


def test_uninitialized_security_or_corrupt_grants_prevent_startup(configured, tmp_path):
    settings, _ = configured
    empty = SQLiteDatabase(tmp_path / "empty.sqlite3")
    empty.initialize()

    async def reject(path):
        runtime = RuntimeService(settings.model_copy(update={"database_path": path}))
        with pytest.raises((IdentityError, StorageError)):
            await runtime.start()
        assert runtime.state == "FAILED"

    asyncio.run(reject(empty.path))
    with sqlite3.connect(settings.resolved_runtime_database_path) as c:
        # Simulate OS-owner damage outside the application boundary in this isolated store.
        c.execute("DROP TRIGGER security_grants_no_resurrection")
        c.execute("UPDATE security_grants SET data='{}' WHERE permission='memory.read'")
    asyncio.run(reject(settings.resolved_runtime_database_path))


def test_no_worker_credentials_or_model_outage_is_explicit_degradation(configured):
    settings, _ = configured
    missing_workers = settings.model_copy(update={"worker_credentials_file": None})

    async def run():
        runtime = RuntimeService(missing_workers)
        await runtime.start()
        assert runtime.state == "DEGRADED" and runtime.public_health()["ready"]
        assert runtime.worker_tokens == ()
        await runtime.stop()

    asyncio.run(run())
    local = settings.model_copy(
        update={"model_provider": "local", "local_model_endpoint": "http://127.0.0.1:1/v1"}
    )
    runtime = RuntimeService(local)
    with TestClient(create_api(runtime), base_url="http://127.0.0.1:8765") as client:
        reply = client.get("/health/ready").json()
        assert reply["state"] == "DEGRADED" and not reply["model_available"] and reply["ready"]
        assert runtime.jobs["tasks"].passes >= 1


@pytest.mark.parametrize("problem", ["mode", "duplicate_principal"])
def test_invalid_worker_file_fails_closed(configured, tmp_path, problem):
    settings, _ = configured
    if problem == "mode":
        settings.worker_credentials_file.chmod(0o644)
        code = "credential_file_unsafe"
    else:
        token = read_private_json(settings.worker_credentials_file)["tokens"][0]
        path = tmp_path / "duplicate-workers.json"
        write_private_json(path, {"tokens": [token, token]})
        settings = settings.model_copy(update={"worker_credentials_file": path})
        code = "worker_principal_duplicate"
    result = asyncio.run(doctor(settings))
    assert not result["workers"] and result["status"] == "degraded"

    async def run():
        runtime = RuntimeService(settings)
        with pytest.raises(ValueError, match=code):
            await runtime.start()
        assert runtime.state == "FAILED"

    asyncio.run(run())


def test_worker_revocation_remains_effective_after_restart(configured):
    settings, manifest = configured
    revoke_credential(
        SQLiteDatabase(settings.resolved_runtime_database_path),
        UUID(manifest["worker_credential_id"]),
    )

    async def run():
        runtime = RuntimeService(settings)
        with pytest.raises((IdentityError, StorageError)):
            await runtime.start()
        assert runtime.state == "FAILED"

    asyncio.run(run())


def test_bounded_task_recovery_current_revocation_and_fair_selection(configured):
    settings, manifest = configured

    async def run():
        runtime = RuntimeService(settings)
        await runtime.start()
        runtime._stop.set()
        await runtime._loop_task
        token = read_private_json(Path(manifest["credentials"]["owner"]["path"]))["token"]
        app = runtime.application
        records = [
            app.tasks.submit(
                (
                    TaskStep(
                        step_id="echo",
                        capability_id="development.echo",
                        arguments={"text": "bounded"},
                    ),
                ),
                session_token=token,
            )
            for _ in range(3)
        ]
        before = runtime.jobs["tasks"].passes
        for index in range(1, 4):
            await runtime.run_job("tasks")
            completed = sum(
                app.tasks.get(r.task_id, session_token=token).state == "COMPLETED" for r in records
            )
            assert completed == index
        assert runtime.jobs["tasks"].passes == before + 3
        record = app.tasks.submit(
            (
                TaskStep(
                    step_id="echo", capability_id="development.echo", arguments={"text": "revoked"}
                ),
            ),
            session_token=token,
        )
        app.permissions.revoke(
            token, app.identities.owner_id, "development.echo.invoke", correlation_id=uuid4()
        )
        # A cursor wrap may require one empty pass, but cannot restore the revoked grant.
        await runtime.run_job("tasks")
        await runtime.run_job("tasks")
        result = app.tasks.get(record.task_id, session_token=token)
        assert result.state == "FAILED"
        await runtime.stop()

    asyncio.run(run())


def test_same_job_cannot_overlap_and_failures_are_isolated(configured, monkeypatch):
    settings, _ = configured

    async def run():
        runtime = RuntimeService(settings)
        await runtime.start()
        runtime._stop.set()
        await runtime._loop_task
        entered, release = threading.Event(), threading.Event()
        calls = []
        original = runtime._pass

        def blocking(name):
            calls.append(name)
            entered.set()
            assert release.wait(timeout=5)
            raise RuntimeError("synthetic worker failure")

        monkeypatch.setattr(runtime, "_pass", blocking)
        task = asyncio.create_task(runtime.run_job("initiatives"))
        while not entered.is_set():
            await asyncio.sleep(0)
        await runtime.run_job("initiatives")
        assert calls == ["initiatives"]
        release.set()
        await task
        assert runtime.state == "DEGRADED" and runtime.jobs["initiatives"].failures == 1
        monkeypatch.setattr(runtime, "_pass", original)
        await runtime.run_job("notification_bridge")
        assert runtime.jobs["notification_bridge"].passes > 0
        await runtime.stop()

    asyncio.run(run())


def test_cancelled_http_work_remains_bounded_and_is_drained(configured):
    settings, _ = configured

    async def run():
        runtime = RuntimeService(settings.model_copy(update={"max_requests": 1}))
        await runtime.start()
        entered, release = threading.Event(), threading.Event()

        def action():
            entered.set()
            assert release.wait(timeout=5)
            return "done"

        request = asyncio.create_task(runtime.call(action))
        while not entered.is_set():
            await asyncio.sleep(0)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert len(runtime._active_requests) == 1
        with pytest.raises(RuntimeError, match="runtime_busy"):
            await runtime.call(lambda: "not admitted")
        stopping = asyncio.create_task(runtime.stop())
        await asyncio.sleep(0)
        assert runtime.state == "STOPPING"
        release.set()
        await stopping
        assert runtime.state == "STOPPED" and not runtime._active_requests

    asyncio.run(run())


def test_shutdown_timeout_preserves_unknown_active_work(configured):
    settings, _ = configured

    async def run():
        runtime = RuntimeService(settings.model_copy(update={"shutdown_timeout_seconds": 1}))
        await runtime.start()
        entered, release = threading.Event(), threading.Event()

        def blocked():
            entered.set()
            assert release.wait(timeout=5)

        task = asyncio.create_task(runtime.call(blocked))
        while not entered.is_set():
            await asyncio.sleep(0)
        try:
            await runtime.stop()
            assert runtime.drain_timed_out and runtime.state == "FAILED"
            assert runtime._claim is not None  # Held until actual exit, not a timed lease.
        finally:
            release.set()
            await task
            runtime._release()  # Test owns the live process; production exits with code 2.

    asyncio.run(run())


def test_schema_7_migration_preserves_data_and_does_not_issue_credentials(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as c:
        for version in range(1, 8):
            for sql in MIGRATIONS[version]:
                c.execute(sql)
        c.execute("PRAGMA user_version=7")
    database = SQLiteDatabase(path)
    database.initialize()
    database.initialize()
    database.verify()
    with database.transaction() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert c.execute("SELECT count(*) FROM local_api_credentials").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM security_authority").fetchone()[0] == 0


def test_backup_restore_verifies_authority_without_regranting(configured, tmp_path):
    settings, manifest = configured
    database = SQLiteDatabase(settings.resolved_runtime_database_path)
    credential = read_private_json(Path(manifest["credentials"]["guest"]["path"]))
    revoke_credential(database, UUID(credential["credential_id"]))
    database.backup(tmp_path / "backup.sqlite3")
    backup = SQLiteDatabase(tmp_path / "backup.sqlite3")
    backup.verify()
    backup.backup(tmp_path / "restored.sqlite3")
    restored = SQLiteDatabase(tmp_path / "restored.sqlite3")
    restored.verify()
    repo = LocalDevelopmentIdentityRepository(restored)
    repo.validate_authority()
    with pytest.raises(IdentityError):
        repo.resolve_kind(credential["token"], "client")
    with pytest.raises(StorageError, match="backup_destination_exists"):
        database.backup(tmp_path / "backup.sqlite3")


def test_doctor_is_read_only_and_secret_files_are_private(configured, tmp_path):
    settings, manifest = configured
    database = SQLiteDatabase(settings.resolved_runtime_database_path)

    def counts():
        with database.transaction() as c:
            return tuple(
                c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("security_sessions", "security_grants", "audits", "tasks")
            )

    before = counts()
    result = asyncio.run(doctor(settings))
    assert result["status"] == "ready" and counts() == before
    credential = Path(manifest["credentials"]["owner"]["path"])
    assert credential.stat().st_mode & 0o777 == 0o600
    alias = tmp_path / "alias"
    alias.symlink_to(credential)
    with pytest.raises(OSError):
        read_private_json(alias)
    output = tmp_path / "owner.curl"
    export_curl_configuration(credential, output)
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        export_curl_configuration(credential, output)


def test_explicit_init_never_overwrites_existing_store(configured):
    settings, _ = configured
    with pytest.raises(ValueError):
        initialize_development(settings)
    database = SQLiteDatabase(settings.resolved_runtime_database_path)
    database.verify()


@pytest.mark.parametrize(
    "change",
    [
        {"runtime_host": "0.0.0.0"},
        {"runtime_port": 0},
        {"worker_batch_size": 101},
        {"max_workers": 2},
        {"mode": "production"},
        {"cloud_ai_enabled": True},
        {"model_timeout_seconds": float("nan")},
        {"local_model_endpoint": "http://example.com/v1"},
        {"local_model_endpoint": "http://127.0.0.1:11434/v1?secret=x"},
    ],
)
def test_config_rejects_unsafe_or_unbounded_choices(change):
    with pytest.raises(ValueError):
        RuntimeSettings(**change)


def test_configuration_precedence_and_unknown_keys(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"model_provider": "fake", "runtime_port": 9000}))
    monkeypatch.setenv("JARVIS_RUNTIME_PORT", "9001")
    assert RuntimeSettings.load(config).runtime_port == 9001
    assert RuntimeSettings.load(config, runtime_port=9002).runtime_port == 9002
    config.write_text('{"unknown_setting": 1}')
    with pytest.raises(ValueError):
        RuntimeSettings.load(config)


@pytest.mark.parametrize("stop_signal", [signal.SIGKILL, signal.SIGINT])
def test_real_process_signal_releases_runtime_claim_and_preserves_credentials(
    configured, tmp_path, stop_signal
):
    settings, manifest = configured
    config = tmp_path / "runtime.json"
    write_private_json(config, settings.model_dump(mode="json"))
    with running_runtime(
        config,
        None,
        tmp_path / "log.txt",
        expected_exit_codes=(-signal.SIGKILL,) if stop_signal == signal.SIGKILL else (0,),
        require_graceful=stop_signal != signal.SIGKILL,
    ) as (client, process):
        token = read_private_json(Path(manifest["credentials"]["owner"]["path"]))["token"]
        headers = {"Authorization": "Bearer " + token}
        conversation = api_data(
            client.post("/api/v1/conversations", headers=headers, json={"schema_version": 1})
        )
        process.send_signal(stop_signal)
        process.wait(timeout=10)
        assert process.returncode == (-signal.SIGKILL if stop_signal == signal.SIGKILL else 0)
    with running_runtime(config, None, tmp_path / "reopened.log") as (client, _):
        reply = client.get(
            f"/api/v1/conversations/{conversation['conversation_id']}/history", headers=headers
        )
        assert api_data(reply) == []


def test_installed_operator_cli_does_not_print_tokens(configured, tmp_path):
    settings, manifest = configured
    config = Path(manifest["config"])
    owner = read_private_json(Path(manifest["credentials"]["owner"]["path"]))["token"]
    result = subprocess.run(
        [sys.executable, "-m", "jarvis", "doctor", "--config", str(config)],
        text=True,
        capture_output=True,
        check=True,
        env=_environment(),
    )
    assert (
        owner not in result.stdout + result.stderr
        and json.loads(result.stdout)["status"] == "ready"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "jarvis",
            "credentials",
            "issue",
            "--config",
            str(config),
            "--identity",
            "guest",
            "--output",
            str(tmp_path / "guest.json"),
        ],
        text=True,
        capture_output=True,
        check=True,
        env=_environment(),
    )
    issued = read_private_json(tmp_path / "guest.json")
    assert issued["token"] not in result.stdout + result.stderr


def test_runtime_recovers_actual_crashed_pure_attempt_but_reviews_ambiguous_lighting(
    configured, tmp_path
):
    settings, manifest = configured
    worker = Path(__file__).parent / "helpers" / "runtime_worker.py"
    ids = {}
    for capability in ("echo", "lighting"):
        output = tmp_path / (capability + ".json")
        result = subprocess.run(
            [sys.executable, str(worker), manifest["config"], capability, str(output)],
            capture_output=True,
            text=True,
            timeout=20,
            env=_environment(),
        )
        assert result.returncode == 31
        ids[capability] = read_private_json(output)["task_id"]
    config = tmp_path / "recovery-runtime.json"
    write_private_json(config, settings.model_dump(mode="json"))
    token = read_private_json(Path(manifest["credentials"]["owner"]["path"]))["token"]
    with running_runtime(config, None, tmp_path / "recovery.log") as (client, _):
        headers = {"Authorization": "Bearer " + token}

        def finished_echo():
            record = api_data(client.get("/api/v1/tasks/" + ids["echo"], headers=headers))
            return record if record["state"] == "COMPLETED" else None

        echo = _wait(finished_echo)

        def reviewed_lighting():
            record = api_data(client.get("/api/v1/tasks/" + ids["lighting"], headers=headers))
            return record if record["state"] == "NEEDS_REVIEW" else None

        lighting = _wait(reviewed_lighting)
        assert echo["state"] == "COMPLETED" and lighting["state"] == "NEEDS_REVIEW"
        status = api_data(client.get("/api/v1/status", headers=headers))
        assert status["simulated_invocations"]["lighting"] == 0


def test_http_diagnostics_report_status_and_code_without_private_body():
    import httpx

    reply = httpx.Response(401, json={"error": {"code": "invalid_session"}, "secret": "private"})
    with pytest.raises(AssertionError, match="HTTP 401.*invalid_session") as error:
        api_data(reply)
    assert "private" not in str(error.value)


def test_cleanup_preserves_primary_assertion_and_notes_owned_child_failure(configured, tmp_path):
    _, manifest = configured
    with pytest.raises(AssertionError, match="original_failure") as error:
        with running_runtime(
            Path(manifest["config"]), None, tmp_path / "primary.log", expected_exit_codes=(99,)
        ):
            raise AssertionError("original_failure")
    assert any(
        "Owned child cleanup: runtime_demo_process_failed" in n for n in error.value.__notes__
    )
    with pytest.raises(RuntimeError, match="runtime_demo_process_failed"):
        with running_runtime(
            Path(manifest["config"]), None, tmp_path / "cleanup.log", expected_exit_codes=(99,)
        ):
            pass


@pytest.mark.parametrize("stop_signal", [signal.SIGKILL, signal.SIGINT])
def test_real_process_checks_with_unrelated_default_port_listener(
    configured, tmp_path, stop_signal
):
    import errno
    import socket

    import httpx

    # Own only the listener we create. If a developer already occupies 8765,
    # exercise isolation against that process without touching its lifecycle.
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    unrelated = None
    try:
        try:
            listener.bind(("127.0.0.1", 8765))
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
        else:
            listener.listen(8)
            script = """
import json, socket, sys
from http.server import HTTPServer, BaseHTTPRequestHandler
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        body = json.dumps({'ready':True}).encode()
        self.send_response(200); self.send_header('Content-Length',str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        body = b'{"error":{"code":"unrelated_invalid_session"}}'
        self.send_response(401); self.send_header('Content-Length',str(len(body)))
        self.end_headers(); self.wfile.write(body)
server = HTTPServer(('127.0.0.1',8765), Handler, bind_and_activate=False)
server.socket.close(); server.socket=socket.socket(fileno=int(sys.argv[1]))
server.serve_forever()
"""
            unrelated = subprocess.Popen(
                [sys.executable, "-c", script, str(listener.fileno())],
                pass_fds=(listener.fileno(),),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with httpx.Client(trust_env=False, timeout=5) as probe:
                assert probe.get("http://127.0.0.1:8765/health/ready").json()["ready"]
        test_real_process_signal_releases_runtime_claim_and_preserves_credentials(
            configured, tmp_path, stop_signal
        )
        test_runtime_recovers_actual_crashed_pure_attempt_but_reviews_ambiguous_lighting(
            configured, tmp_path
        )
        if unrelated is not None:
            assert unrelated.poll() is None
    finally:
        if unrelated is not None:
            unrelated.terminate()
            unrelated.wait(timeout=10)
        listener.close()


def test_authority_storage_diagnostic_is_sanitized_and_fail_closed(configured, monkeypatch):
    settings, _ = configured
    repo = LocalDevelopmentIdentityRepository(
        SQLiteDatabase(settings.resolved_runtime_database_path)
    )

    def unavailable(*args, **kwargs):
        raise StorageError("database_locked")

    monkeypatch.setattr(repo.database, "transaction", unavailable)
    with pytest.raises(IdentityError) as failure:
        repo.validate_authority()
    assert failure.value.reason_code == "security_state_unavailable"
    assert failure.value.storage_code == "database_locked"
    assert str(failure.value) == "security_state_unavailable"
    monkeypatch.setattr(SQLiteDatabase, "transaction", unavailable)
    result = asyncio.run(doctor(settings))
    assert result["authority"] is False
    assert "database_locked" in result["errors"]


def test_existing_v9_authority_migrates_and_runtime_restarts(configured):
    settings, manifest = configured
    path = settings.resolved_runtime_database_path
    token = read_private_json(Path(manifest["credentials"]["owner"]["path"]))["token"]
    with sqlite3.connect(path) as connection:
        tables = (
            "security_authority",
            "security_principals",
            "security_sessions",
            "security_grants",
            "local_api_credentials",
            "events",
            "tasks",
            "memories",
        )
        before = {
            t: connection.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables
        }
        connection.execute("PRAGMA user_version=9")
    database = SQLiteDatabase(path)
    with pytest.raises(IdentityError) as failure:
        LocalDevelopmentIdentityRepository(database).validate_authority()
    assert failure.value.storage_code == "database_version_unsupported"
    database.initialize()
    database.verify()
    with sqlite3.connect(path) as connection:
        assert {
            t: connection.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables
        } == before
    repo = LocalDevelopmentIdentityRepository(database)
    repo.validate_authority()
    repo.resolve_kind(token, "client")

    async def restart():
        for _ in range(2):
            runtime = RuntimeService(settings)
            await runtime.start()
            await runtime.stop()

    asyncio.run(restart())
