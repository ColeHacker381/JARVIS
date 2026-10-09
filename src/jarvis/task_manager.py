"""Small sequential runner: current authority, durable checkpoints, conservative recovery."""

import logging
from collections.abc import Callable
from typing import Literal
from uuid import UUID, uuid4

from jarvis.core.contracts import (
    ActionProposal,
    AuditRecord,
    Contract,
    ErrorDetail,
    Event,
    PolicyDecision,
)
from jarvis.core.task_records import TERMINAL_STATES, StepAttempt, TaskRecord, TaskStep
from jarvis.core.time import utc_now
from jarvis.execution import CapabilityExecutor, InvocationContext, ReplayProfile
from jarvis.identity import IdentityError, IdentityRepository
from jarvis.observability import AuditSink, AuditWriteError, write_audit
from jarvis.storage.errors import StorageError
from jarvis.tasks import TaskBusyError, TaskStore


class RunResult(Contract):
    task_id: UUID
    status: Literal["ok", "storage_failure", "authorization_failure", "busy"]
    task: TaskRecord | None = None
    error: ErrorDetail | None = None


def _error(code: str, *, retryable: bool = False) -> ErrorDetail:
    return ErrorDetail(code=code, message=code, retryable=retryable)


def _replace_step(task: TaskRecord, index: int, step: TaskStep) -> tuple[TaskStep, ...]:
    return (*task.steps[:index], step, *task.steps[index + 1 :])


class TaskManager:
    def __init__(
        self,
        store: TaskStore,
        identities: IdentityRepository,
        executor: CapabilityExecutor,
        audit: AuditSink,
    ) -> None:
        self.store = store
        self.identities = identities
        self.executor = executor
        self.audit = audit

    def _owner_session(self, token: str, task: TaskRecord | None = None):
        session = self.identities.resolve_session(token)
        if session.principal_id is None or session.authentication_ref is None:
            raise IdentityError("principal_unknown")
        if task is not None and session.principal_id != task.owner_id:
            raise IdentityError("task_owner_mismatch")
        return session

    @staticmethod
    def _records(
        task: TaskRecord,
        action: str,
        code: str,
        *,
        step_id: str | None = None,
        attempt: StepAttempt | None = None,
        outcome: str = "succeeded",
    ):
        # Only stable IDs/states/codes: capability input/output stays in restricted task storage.
        payload = {
            "task_id": str(task.task_id),
            "state": task.state,
            "revision": task.revision,
            "reason_code": code,
        }
        if step_id is not None:
            payload["step_id"] = step_id
        if attempt is not None:
            payload["attempt_id"] = str(attempt.attempt_id)
        event = Event(
            event_type=action,
            principal_id=task.owner_id,
            correlation_id=task.correlation_id,
            causation_id=attempt.proposal_id if attempt else task.task_id,
            source="jarvis.tasks",
            payload=payload,
        )
        audit = AuditRecord(
            principal_id=task.owner_id,
            task_id=task.task_id,
            session_id=attempt.session_id if attempt else None,
            proposal_id=attempt.proposal_id if attempt else None,
            correlation_id=task.correlation_id,
            action=action,
            outcome=outcome,
            reason_code=code,
            policy_refs=attempt.policy_refs if attempt else (),
        )
        return event, audit

    def _commit(
        self,
        task: TaskRecord,
        state: str,
        action: str,
        code: str,
        *,
        step_id: str | None = None,
        execution_attempt: StepAttempt | None = None,
        outcome: str = "succeeded",
        **changes,
    ) -> TaskRecord:
        updated = task.transition(state, **changes)
        event, audit = self._records(
            updated, action, code, step_id=step_id, attempt=execution_attempt, outcome=outcome
        )
        self.store.save(updated, expected_revision=task.revision, event=event, audit=audit)
        logging.getLogger("jarvis").info(
            action,
            extra={
                "task_id": task.task_id,
                "correlation_id": task.correlation_id,
                "reason_code": code,
            },
        )
        return updated

    def submit(
        self,
        steps: tuple[TaskStep, ...],
        *,
        session_token: str,
        idempotency_key: str | None = None,
        correlation_id: UUID | None = None,
    ) -> TaskRecord:
        session = self._owner_session(session_token)
        if any(step.state != "NOT_STARTED" or step.attempts for step in steps):
            raise ValueError("submission cannot import execution history or authorization")
        task = TaskRecord(
            owner_id=session.principal_id,
            correlation_id=correlation_id or uuid4(),
            steps=steps,
            idempotency_key=idempotency_key,
            authority_refs=(f"principal:{session.principal_id}",),
        )
        event, audit = self._records(task, "task.created", "created")
        task = self.store.create(task, event, audit)
        if task.state == "CREATED":
            try:
                with self.store.claim(task.task_id):
                    task = self.store.get(task.task_id)
                    if task.state == "CREATED":
                        task = self._commit(task, "READY", "task.ready", "ready")
            except TaskBusyError:
                task = self.store.get(task.task_id)
        return task

    def cancel(self, task_id: UUID, *, session_token: str) -> TaskRecord:
        task = self.store.get(task_id)
        if task is None:
            raise ValueError("task_unknown")
        self._owner_session(session_token, task)
        if task.state in TERMINAL_STATES:
            return task
        self.store.request_cancel(task_id)
        try:
            with self.store.claim(task_id):
                task = self.store.get(task_id)
                if task.state in TERMINAL_STATES:
                    return task
                # An interrupted operation has to be classified before cancellation can finish.
                if any(step.state in {"RUNNING", "OUTCOME_UNKNOWN"} for step in task.steps):
                    return task
                return self._commit(
                    task,
                    "CANCELLED",
                    "task.cancelled",
                    "cancelled",
                    error=None,
                    cancel_requested=True,
                    outcome="cancelled",
                )
        except TaskBusyError:
            # Cooperative cancellation stops after the in-flight invocation is checkpointed.
            return task

    def run(
        self,
        task_id: UUID,
        *,
        session_token: str,
        recovery: bool = False,
        after_checkpoint: Callable[[TaskRecord], None] | None = None,
    ) -> RunResult:
        """Fresh credentials are supplied by trusted runtime; never recovered from the journal.

        after_checkpoint is a trusted test hook for deterministic process interruption.
        """
        task = None
        try:
            task = self.store.get(task_id)
            if task is None:
                raise ValueError("task_unknown")
            self._owner_session(session_token, task)
            with self.store.claim(task_id):
                task = self.store.get(task_id)
                if task.state in TERMINAL_STATES:
                    return RunResult(task_id=task_id, status="ok", task=task)
                task = self._run_locked(task, session_token, recovery, after_checkpoint)
            return RunResult(task_id=task_id, status="ok", task=task)
        except IdentityError as exc:
            if task is not None:
                try:
                    actor = self.identities.resolve_session(session_token)
                except IdentityError:
                    actor = None
                try:
                    write_audit(
                        self.audit,
                        AuditRecord(
                            principal_id=actor.principal_id if actor else None,
                            session_id=actor.session_id if actor else None,
                            task_id=task_id,
                            correlation_id=task.correlation_id,
                            action="task.recovery_blocked"
                            if recovery
                            else "task.execution_blocked",
                            outcome="denied",
                            reason_code=exc.reason_code,
                        ),
                    )
                except AuditWriteError:
                    return RunResult(
                        task_id=task_id, status="storage_failure", error=_error("audit_unavailable")
                    )
            return RunResult(
                task_id=task_id,
                status="authorization_failure",
                error=_error(exc.reason_code),
            )
        except TaskBusyError:
            return RunResult(
                task_id=task_id, status="busy", task=task, error=_error("task_busy", retryable=True)
            )
        except StorageError as exc:
            # Existing DB checkpoints remain authoritative. A result cannot claim rollback.
            logging.getLogger("jarvis").error(
                "task.persistence_failed",
                extra={
                    "task_id": task_id,
                    "correlation_id": task.correlation_id if task else None,
                    "reason_code": exc.code,
                },
            )
            return RunResult(task_id=task_id, status="storage_failure", error=_error(exc.code))

    def recover(self, *, session_token: str) -> tuple[RunResult, ...]:
        session = self._owner_session(session_token)
        return tuple(
            self.run(task.task_id, session_token=session_token, recovery=True)
            for task in self.store.unfinished()
            if task.owner_id == session.principal_id
        )

    def _run_locked(self, task, token, recovery, hook):
        if task.state == "CREATED":
            task = self._commit(task, "READY", "task.ready", "ready")
        action = (
            "task.recovery_started"
            if recovery or task.state in {"RUNNING", "WAITING"}
            else "task.started"
        )
        # Clear an old WAITING error, but retain immutable attempt history.
        task = self._commit(
            task,
            "RUNNING",
            action,
            "recovery" if action.endswith("recovery_started") else "started",
            error=None,
        )
        for index, step in enumerate(task.steps):
            if step.state == "COMPLETED":
                continue
            if step.state in {"RUNNING", "OUTCOME_UNKNOWN", "FAILED"}:
                last = step.attempts[-1]
                if step.state == "RUNNING":
                    last = StepAttempt.model_validate(
                        {**last.model_dump(), "state": "OUTCOME_UNKNOWN", "finished_at": utc_now()}
                    )
                    step = TaskStep.model_validate(
                        {
                            **step.model_dump(),
                            "state": "OUTCOME_UNKNOWN",
                            "attempts": (*step.attempts[:-1], last),
                        }
                    )
                    task = self._commit(
                        task,
                        "RUNNING",
                        "task.step_failed",
                        "outcome_unknown",
                        step_id=step.step_id,
                        execution_attempt=last,
                        outcome="failed",
                        steps=_replace_step(task, index, step),
                    )
                try:
                    profile = self.executor.replay_profile(step.capability_id)
                    safe = (
                        last.retry_safe
                        and profile.retry_safe
                        and last.capability_fingerprint == profile.fingerprint
                    )
                except Exception:
                    safe = False
                if not safe:
                    return self._commit(
                        task,
                        "NEEDS_REVIEW",
                        "task.recovery_blocked",
                        "recovery_requires_review",
                        error=_error("unknown_external_outcome"),
                        step_id=step.step_id,
                        execution_attempt=last,
                        outcome="failed",
                    )
            if self.store.cancellation_requested(task.task_id):
                return self._commit(
                    task,
                    "CANCELLED",
                    "task.cancelled",
                    "cancelled",
                    error=None,
                    cancel_requested=True,
                    outcome="cancelled",
                )
            # The current executor validates inputs and rechecks grants before this callback.
            session = self._owner_session(token, task)
            proposal = ActionProposal(
                session_id=session.session_id,
                task_id=task.task_id,
                capability_id=step.capability_id,
                arguments=step.arguments,
                correlation_id=task.correlation_id,
                origin="rule",
                source_ref="jarvis.tasks",
            )
            key = f"task:{task.task_id}/step:{step.step_id}"
            attempt_id = uuid4()
            attempt_started_at = utc_now()
            attempt = None
            checkpoint_error = None

            def checkpoint(
                decision: PolicyDecision,
                profile: ReplayProfile,
                *,
                attempt_id=attempt_id,
                proposal=proposal,
                attempt_started_at=attempt_started_at,
                session=session,
                key=key,
                index=index,
            ):
                nonlocal task, step, attempt, checkpoint_error
                candidate = StepAttempt(
                    attempt_id=attempt_id,
                    proposal_id=proposal.proposal_id,
                    started_at=attempt_started_at,
                    session_id=session.session_id,
                    idempotency_key=key,
                    retry_safe=profile.retry_safe,
                    capability_fingerprint=profile.fingerprint,
                    policy_refs=decision.policy_refs,
                )
                started = TaskStep.model_validate(
                    {
                        **step.model_dump(),
                        "state": "RUNNING",
                        "result": None,
                        "attempts": (*step.attempts, candidate),
                    }
                )
                try:
                    task = self._commit(
                        task,
                        "RUNNING",
                        "task.step_started",
                        "authorized",
                        step_id=step.step_id,
                        execution_attempt=candidate,
                        steps=_replace_step(task, index, started),
                        current_step=step.step_id,
                        attempt=task.attempt + 1,
                        authority_refs=tuple(
                            dict.fromkeys((*task.authority_refs, *decision.policy_refs))
                        ),
                    )
                    step = started
                    attempt = candidate
                except Exception as exc:
                    checkpoint_error = (
                        exc
                        if isinstance(exc, StorageError)
                        else StorageError("checkpoint_unavailable")
                    )
                    raise

            result = self.executor.execute(
                proposal,
                session_token=token,
                context=InvocationContext(task.task_id, step.step_id, attempt_id, key),
                before_invoke=checkpoint,
            )
            if checkpoint_error is not None:
                raise checkpoint_error
            invoked = attempt is not None
            if attempt is None:
                # Denied/invalid intent never reached provider: retain a known failed attempt.
                attempt = StepAttempt(
                    attempt_id=attempt_id,
                    proposal_id=proposal.proposal_id,
                    started_at=attempt_started_at,
                    session_id=session.session_id,
                    idempotency_key=key,
                    retry_safe=False,
                    capability_fingerprint="not_invoked",
                    state="FAILED",
                    finished_at=utc_now(),
                    result=result,
                )
                attempts = (*step.attempts, attempt)
            else:
                unknown = result.status != "success" and not attempt.retry_safe
                attempt = StepAttempt.model_validate(
                    {
                        **attempt.model_dump(),
                        "state": "COMPLETED"
                        if result.status == "success"
                        else "OUTCOME_UNKNOWN"
                        if unknown
                        else "FAILED",
                        "finished_at": utc_now(),
                        "result": result,
                    }
                )
                attempts = (*step.attempts[:-1], attempt)
            step = TaskStep.model_validate(
                {
                    **step.model_dump(),
                    "state": attempt.state,
                    "attempts": attempts,
                    "result": result,
                }
            )
            task = self._commit(
                task,
                "RUNNING",
                "task.step_completed" if result.status == "success" else "task.step_failed",
                "executed" if result.status == "success" else result.error.code,
                step_id=step.step_id,
                execution_attempt=attempt,
                outcome="succeeded"
                if result.status == "success"
                else "denied"
                if result.status == "denied"
                else "failed",
                steps=_replace_step(task, index, step),
                current_step=step.step_id,
                attempt=task.attempt + (0 if invoked else 1),
            )
            if hook is not None:
                hook(task)
            if result.status != "success":
                if step.state == "OUTCOME_UNKNOWN":
                    return self._commit(
                        task,
                        "NEEDS_REVIEW",
                        "task.recovery_blocked",
                        "recovery_requires_review",
                        error=_error("unknown_external_outcome"),
                        outcome="failed",
                        step_id=step.step_id,
                        execution_attempt=attempt,
                    )
                # Retry is explicit, and only supported when replay cannot duplicate effects.
                retryable = (
                    invoked
                    and attempt.retry_safe
                    and (result.status == "timeout" or result.error.code == "audit_unavailable")
                )
                return self._commit(
                    task,
                    "WAITING" if retryable else "FAILED",
                    "task.waiting" if retryable else "task.failed",
                    "retryable_failure" if retryable else result.error.code,
                    error=_error(result.error.code, retryable=retryable),
                    outcome="failed",
                    step_id=step.step_id,
                    execution_attempt=attempt,
                )
        if self.store.cancellation_requested(task.task_id):
            return self._commit(
                task,
                "CANCELLED",
                "task.cancelled",
                "cancelled",
                cancel_requested=True,
                outcome="cancelled",
            )
        return self._commit(task, "COMPLETED", "task.completed", "completed", current_step=None)
