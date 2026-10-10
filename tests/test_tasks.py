import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event as ThreadEvent
from uuid import uuid4

import pytest
from pydantic import ValidationError

from jarvis.authorization import DeterministicPolicy
from jarvis.capabilities import InMemoryCapabilityRegistry
from jarvis.core.contracts import Capability, Task, decode_contract
from jarvis.core.task_records import TaskRecord, TaskStep, decode_task
from jarvis.execution import CapabilityExecutor
from jarvis.identity import IdentityError, InMemoryIdentityService
from jarvis.permissions import InMemoryPermissionStore
from jarvis.storage.errors import StorageError
from jarvis.storage.sqlite import (
    IdempotencyConflictError,
    SQLiteAuditSink,
    SQLiteDatabase,
    SQLiteEventStore,
    SQLiteTaskStore,
)
from jarvis.task_manager import TaskManager
from jarvis.tasks import TaskConflictError


class Interrupted(BaseException):
    """Supplement subprocess coverage with precise local crash-window fixtures."""


@dataclass
class Runtime:
    manager: TaskManager
    database: SQLiteDatabase
    store: SQLiteTaskStore
    audit: SQLiteAuditSink
    identities: InMemoryIdentityService
    permissions: InMemoryPermissionStore
    registry: InMemoryCapabilityRegistry
    owner: object
    friend: object
    owner_session: object
    friend_session: object
    calls: list
    effects: dict

    def grant(self):
        return self.permissions.grant(
            self.owner_session.token,
            self.friend.principal_id,
            "tests.invoke",
            correlation_id=uuid4(),
        )

    def submit(self, steps=None, **kwargs):
        return self.manager.submit(
            steps or plan(), session_token=self.friend_session.token, **kwargs
        )

    def run(self, task, **kwargs):
        return self.manager.run(task.task_id, session_token=self.friend_session.token, **kwargs)


def plan(*, capability_id="tests.safe", count=3):
    return tuple(
        TaskStep(
            step_id=f"step-{index}",
            capability_id=capability_id,
            arguments={"value": index},
            depends_on=() if not index else (f"step-{index - 1}",),
        )
        for index in range(count)
    )


@pytest.fixture
def runtime(tmp_path):
    database = SQLiteDatabase(tmp_path / "journal.sqlite3")
    database.initialize()
    audit = SQLiteAuditSink(database)
    identities = InMemoryIdentityService()
    owner = identities.bootstrap_owner_for_testing()
    friend = identities.create_principal_for_testing("Friend", relationship_ref="best_friend")
    owner_session = identities.establish_session_for_testing(owner.principal_id)
    friend_session = identities.establish_session_for_testing(friend.principal_id)
    permissions = InMemoryPermissionStore(identities, audit)
    registry = InMemoryCapabilityRegistry()
    calls, effects = [], {}

    def safe(arguments, *, idempotency_key):
        calls.append((arguments["value"], idempotency_key))
        effects.setdefault(idempotency_key, arguments.copy())
        return effects[idempotency_key]

    def unsafe(arguments):
        calls.append((arguments["value"], None))
        return arguments

    for cap_id, provider, idempotent in (
        ("tests.safe", safe, True),
        ("tests.unsafe", unsafe, False),
        ("tests.pure", unsafe, False),
    ):
        registry.register(
            Capability(
                capability_id=cap_id,
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                required_permissions=("tests.invoke",),
                side_effect="none" if cap_id == "tests.pure" else "local",
                adapter_ref=cap_id,
            ),
            provider,
            supports_idempotency=idempotent,
        )
    executor = CapabilityExecutor(
        identities, registry, DeterministicPolicy(identities, permissions, audit), audit
    )
    store = SQLiteTaskStore(database)
    manager = TaskManager(store, identities, executor, audit)
    return Runtime(
        manager,
        database,
        store,
        audit,
        identities,
        permissions,
        registry,
        owner,
        friend,
        owner_session,
        friend_session,
        calls,
        effects,
    )


def test_task_v2_roundtrip_keeps_v1_readable(runtime):
    task = runtime.submit()
    assert task.schema_version == 2
    assert decode_contract(task.model_dump_json()) == task
    assert decode_task(task.model_dump_json()) == task
    assert task.authority_refs == (f"principal:{runtime.friend.principal_id}",)
    legacy = Task(owner_id=runtime.friend.principal_id, correlation_id=uuid4())
    assert decode_contract(legacy.model_dump_json()) == legacy
    with pytest.raises(ValueError):
        decode_task(legacy.model_dump_json())
    assert "token" not in task.model_dump_json()
    with pytest.raises(ValidationError):
        TaskRecord.model_validate({**task.model_dump(), "schema_version": True})


def test_complete_sequential_work_uses_executor_and_audits_each_step(runtime, monkeypatch):
    runtime.grant()
    task = runtime.submit()
    original = runtime.manager.executor.execute
    seen = []

    def execute(proposal, **kwargs):
        seen.append(proposal)
        return original(proposal, **kwargs)

    monkeypatch.setattr(runtime.manager.executor, "execute", execute)
    result = runtime.run(task)
    assert result.status == "ok" and result.task.state == "COMPLETED"
    assert [value for value, _ in runtime.calls] == [0, 1, 2]
    assert len(seen) == 3
    assert all(p.task_id == task.task_id and p.correlation_id == task.correlation_id for p in seen)
    assert all(s.attempts[0].policy_refs for s in result.task.steps)
    assert len({s.attempts[0].attempt_id for s in result.task.steps}) == 3
    records = runtime.audit.records(correlation_id=task.correlation_id)
    assert [a.outcome for a in records if a.action == "tests.safe"] == ["allowed", "succeeded"] * 3
    assert {
        "task.created",
        "task.started",
        "task.step_started",
        "task.step_completed",
        "task.completed",
    } <= {a.action for a in records}
    events = SQLiteEventStore(runtime.database).by_correlation(task.correlation_id)
    assert all(e.principal_id == runtime.friend.principal_id and e.causation_id for e in events)
    assert all("arguments" not in e.payload and "output" not in e.payload for e in events)
    assert all(a.principal_id == runtime.friend.principal_id for a in records)


@pytest.mark.parametrize("target", ["CREATED", "WAITING", "COMPLETED", "FAILED", "NEEDS_REVIEW"])
def test_invalid_ready_transitions_rejected(runtime, target):
    with pytest.raises(ValueError):
        runtime.submit().transition(target)


@pytest.mark.parametrize("state", ["COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"])
def test_terminal_tasks_cannot_resume(runtime, state):
    runtime.grant()
    task = runtime.submit(plan(count=1))
    if state == "COMPLETED":
        terminal = runtime.run(task).task
    elif state == "FAILED":
        runtime.permissions.revoke(
            runtime.owner_session.token,
            runtime.friend.principal_id,
            "tests.invoke",
            correlation_id=task.correlation_id,
        )
        terminal = runtime.run(task).task
    elif state == "CANCELLED":
        terminal = runtime.manager.cancel(task.task_id, session_token=runtime.friend_session.token)
    else:
        task = runtime.submit(plan(capability_id="tests.unsafe", count=1))
        registration = runtime.registry._capabilities["tests.unsafe"]
        from dataclasses import replace

        runtime.registry._capabilities["tests.unsafe"] = replace(
            registration, provider=lambda _: (_ for _ in ()).throw(TimeoutError())
        )
        terminal = runtime.run(task).task
    before = len(runtime.calls)
    assert terminal.state == state
    assert runtime.run(terminal, recovery=True).task == terminal
    assert len(runtime.calls) == before
    with pytest.raises(ValueError):
        terminal.transition("RUNNING")
    assert terminal.task_id not in {t.task_id for t in runtime.store.unfinished()}


def test_concurrent_revision_update_is_not_silently_lost(runtime):
    task = runtime.submit()
    running = task.transition("RUNNING")
    event, audit = runtime.manager._records(running, "task.started", "started")
    runtime.store.save(running, expected_revision=task.revision, event=event, audit=audit)
    with pytest.raises(TaskConflictError):
        runtime.store.save(running, expected_revision=task.revision, event=event, audit=audit)
    assert runtime.store.get(task.task_id) == running
    assert len(runtime.store.history(task.task_id)) == 3


def test_checkpoint_event_audit_failure_rolls_back_entire_transition(runtime, monkeypatch):
    from jarvis.storage import sqlite as adapter

    task = runtime.submit()
    running = task.transition("RUNNING")
    event, audit = runtime.manager._records(running, "task.started", "started")
    original = adapter._append_audit

    def unavailable(*_):
        raise sqlite_error()

    monkeypatch.setattr(adapter, "_append_audit", unavailable)
    with pytest.raises(StorageError):
        runtime.store.save(running, expected_revision=task.revision, event=event, audit=audit)
    monkeypatch.setattr(adapter, "_append_audit", original)
    assert runtime.store.get(task.task_id) == task
    assert len(runtime.store.history(task.task_id)) == 2
    assert not SQLiteEventStore(runtime.database).read(event_type="task.started")


def sqlite_error():
    import sqlite3

    return sqlite3.OperationalError("private storage detail must not be reported")


def test_restart_inspection_and_history_in_new_process(runtime):
    runtime.grant()
    task = runtime.submit()

    def interrupt(checkpoint):
        if checkpoint.steps[0].state == "COMPLETED":
            raise Interrupted()

    with pytest.raises(Interrupted):
        runtime.run(task, after_checkpoint=interrupt)
    code = """
import json,sys
from uuid import UUID
from jarvis.storage.sqlite import SQLiteDatabase,SQLiteTaskStore
store=SQLiteTaskStore(SQLiteDatabase(sys.argv[1]))
task=store.get(UUID(sys.argv[2]))
print(json.dumps({'state':task.state,'steps':[s.state for s in task.steps],
 'history':[t.revision for t in store.history(task.task_id)],'unfinished':len(store.unfinished())}))
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(runtime.database.path), str(task.task_id)],
        text=True,
        capture_output=True,
    )
    assert process.returncode == 0, process.stderr
    data = json.loads(process.stdout)
    assert data["state"] == "RUNNING" and data["steps"] == [
        "COMPLETED",
        "NOT_STARTED",
        "NOT_STARTED",
    ]
    assert data["history"] == list(range(5)) and data["unfinished"] == 1
    assert runtime.run(task, recovery=True).task.state == "COMPLETED"
    assert [value for value, _ in runtime.calls] == [0, 1, 2]


def test_repeated_submission_scoped_to_owner_and_plan(runtime):
    task = runtime.submit(idempotency_key="same-plan")
    assert runtime.submit(idempotency_key="same-plan") == task
    assert len(runtime.store.all()) == 1
    with pytest.raises(IdempotencyConflictError):
        runtime.submit(plan(count=2), idempotency_key="same-plan")
    owner_task = runtime.manager.submit(
        plan(), session_token=runtime.owner_session.token, idempotency_key="same-plan"
    )
    assert owner_task.task_id != task.task_id


def test_repeated_recovery_does_not_duplicate_work(runtime):
    runtime.grant()
    task = runtime.submit(idempotency_key="recover-many")
    assert runtime.run(task).task.state == "COMPLETED"
    for _ in range(3):
        assert runtime.manager.recover(session_token=runtime.friend_session.token) == ()
        assert runtime.run(task, recovery=True).task.state == "COMPLETED"
    assert runtime.submit(idempotency_key="recover-many").state == "COMPLETED"
    assert len(runtime.calls) == len(runtime.effects) == 3


def test_parallel_process_cannot_claim_running_task(runtime):
    runtime.grant()
    task = runtime.submit(plan(count=1))
    started, release = ThreadEvent(), ThreadEvent()
    registration = runtime.registry._capabilities["tests.safe"]
    from dataclasses import replace

    def provider(arguments, *, idempotency_key):
        started.set()
        assert release.wait(10)
        return registration.provider(arguments, idempotency_key=idempotency_key)

    runtime.registry._capabilities["tests.safe"] = replace(registration, provider=provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        active = pool.submit(runtime.run, task)
        assert started.wait(10)
        try:
            code = """
import sys
from uuid import UUID
from jarvis.storage.sqlite import SQLiteDatabase,SQLiteTaskStore
from jarvis.tasks import TaskBusyError
try:
 with SQLiteTaskStore(SQLiteDatabase(sys.argv[1])).claim(UUID(sys.argv[2])): sys.exit(1)
except TaskBusyError: sys.exit(0)
"""
            process = subprocess.run(
                [sys.executable, "-c", code, str(runtime.database.path), str(task.task_id)],
                capture_output=True,
            )
            assert process.returncode == 0
            # A second process cannot claim the running task.
            assert runtime.store.get(task.task_id).steps[0].state == "RUNNING"
        finally:
            release.set()
        assert active.result().task.state == "COMPLETED"
    assert len(runtime.calls) == 1


def test_cancellation_before_run_prevents_all_steps(runtime):
    runtime.grant()
    task = runtime.submit()
    cancelled = runtime.manager.cancel(task.task_id, session_token=runtime.friend_session.token)
    assert cancelled.state == "CANCELLED" and cancelled.cancel_requested
    assert runtime.run(task).task == cancelled
    assert runtime.calls == []
    assert runtime.audit.records(correlation_id=task.correlation_id)[-1].action == "task.cancelled"


def test_cooperative_cancellation_stops_subsequent_steps(runtime):
    runtime.grant()
    task = runtime.submit()

    # Cancellation after a committed step while the task's execution lock is still held.
    def cancel(checkpoint):
        if checkpoint.steps[0].state == "COMPLETED":
            runtime.manager.cancel(task.task_id, session_token=runtime.friend_session.token)

    result = runtime.run(task, after_checkpoint=cancel)
    assert result.task.state == "CANCELLED"
    assert [value for value, _ in runtime.calls] == [0]
    assert result.task.steps[0].state == "COMPLETED"
    assert result.task.steps[1].state == "NOT_STARTED"


@pytest.mark.parametrize("capability_id", ["tests.safe", "tests.pure", "tests.unsafe"])
def test_interrupted_provider_requires_actual_retry_contract(runtime, capability_id):
    from dataclasses import replace

    runtime.grant()
    task = runtime.submit(plan(capability_id=capability_id, count=1))
    registration = runtime.registry._capabilities[capability_id]

    def crash(*args, **kwargs):
        registration.provider(*args, **kwargs)
        raise Interrupted()

    runtime.registry._capabilities[capability_id] = replace(registration, provider=crash)
    with pytest.raises(Interrupted):
        runtime.run(task)
    interrupted = runtime.store.get(task.task_id)
    assert interrupted.steps[0].state == "RUNNING"
    first_id = interrupted.steps[0].attempts[0].attempt_id
    runtime.registry._capabilities[capability_id] = registration
    recovered = runtime.run(task, recovery=True).task
    if capability_id == "tests.unsafe":
        assert recovered.state == "NEEDS_REVIEW"
        assert len(runtime.calls) == 1
        assert recovered.steps[0].state == "OUTCOME_UNKNOWN"
    else:
        assert recovered.state == "COMPLETED" and len(runtime.calls) == 2
        assert len(recovered.steps[0].attempts) == 2
        assert recovered.steps[0].attempts[-1].attempt_id != first_id
        assert recovered.steps[0].attempts[0].state == "OUTCOME_UNKNOWN"
        assert (
            recovered.steps[0].attempts[0].idempotency_key
            == recovered.steps[0].attempts[1].idempotency_key
        )
        if capability_id == "tests.safe":
            assert len(runtime.effects) == 1


def test_safe_timeout_can_wait_and_resume_with_new_attempt(runtime):
    from dataclasses import replace

    runtime.grant()
    task = runtime.submit(plan(count=1))
    registration = runtime.registry._capabilities["tests.safe"]
    runtime.registry._capabilities["tests.safe"] = replace(
        registration,
        provider=lambda *args, **kwargs: (_ for _ in ()).throw(
            TimeoutError("private provider detail")
        ),
    )
    waiting = runtime.run(task).task
    assert waiting.state == "WAITING" and waiting.error.retryable
    assert waiting.error.code == "provider_timeout"
    runtime.registry._capabilities["tests.safe"] = registration
    completed = runtime.run(task, recovery=True).task
    assert completed.state == "COMPLETED" and len(completed.steps[0].attempts) == 2
    assert completed.steps[0].attempts[0].state == "FAILED"


def test_changed_provider_metadata_blocks_unknown_replay(runtime):
    from dataclasses import replace

    runtime.grant()
    task = runtime.submit(plan(count=1))
    registration = runtime.registry._capabilities["tests.safe"]
    runtime.registry._capabilities["tests.safe"] = replace(
        registration, provider=lambda *a, **k: (_ for _ in ()).throw(Interrupted())
    )
    with pytest.raises(Interrupted):
        runtime.run(task)
    changed = registration.capability.model_copy(update={"adapter_ref": "different-provider"})
    runtime.registry._capabilities["tests.safe"] = replace(registration, capability=changed)
    assert runtime.run(task, recovery=True).task.state == "NEEDS_REVIEW"
    assert runtime.calls == []


def test_missing_permissions_and_invalid_inputs_never_reach_provider(runtime):
    task = runtime.submit()
    result = runtime.run(task).task
    assert result.state == "FAILED" and result.error.code == "permission_missing"
    assert not runtime.calls
    assert result.steps[0].attempts[0].policy_refs == ()
    records = runtime.audit.records(correlation_id=task.correlation_id)
    assert any(r.action == "tests.safe" and r.outcome == "denied" for r in records)
    runtime.grant()
    invalid = runtime.submit(
        (
            TaskStep(
                step_id="invalid",
                capability_id="tests.safe",
                arguments={"value": "secret invalid data"},
            ),
        )
    )
    result = runtime.run(invalid).task
    assert result.state == "FAILED" and result.error.code == "input_invalid"
    assert not runtime.calls
    assert "secret invalid data" not in " ".join(
        a.model_dump_json() for a in runtime.audit.records()
    )


def test_revocation_between_checkpoints_blocks_remaining_work(runtime):
    grant = runtime.grant()
    task = runtime.submit()

    def revoke(checkpoint):
        if checkpoint.steps[0].state == "COMPLETED":
            runtime.permissions.revoke(
                runtime.owner_session.token,
                runtime.friend.principal_id,
                "tests.invoke",
                correlation_id=task.correlation_id,
            )
            raise Interrupted()

    with pytest.raises(Interrupted):
        runtime.run(task, after_checkpoint=revoke)
    checkpoint = runtime.store.get(task.task_id)
    assert grant.policy_ref in checkpoint.authority_refs
    result = runtime.run(task, recovery=True).task
    assert result.state == "FAILED" and result.error.code == "permission_missing"
    assert [value for value, _ in runtime.calls] == [0]
    assert result.steps[0] == checkpoint.steps[0]
    assert any(
        a.outcome == "denied" for a in runtime.audit.records(correlation_id=task.correlation_id)
    )


def test_authority_references_never_restore_current_grants(runtime):
    runtime.grant()
    task = runtime.submit()
    with pytest.raises(Interrupted):
        runtime.run(task, after_checkpoint=lambda _: (_ for _ in ()).throw(Interrupted()))
    # A new trusted identity runtime issues a fresh session but has no grants.
    fresh = InMemoryIdentityService()
    fresh.bootstrap_owner_for_testing(principal_id=runtime.owner.principal_id)
    fresh.create_principal_for_testing("Friend", principal_id=runtime.friend.principal_id)
    session = fresh.establish_session_for_testing(runtime.friend.principal_id)
    permissions = InMemoryPermissionStore(fresh, runtime.audit)
    executor = CapabilityExecutor(
        fresh,
        runtime.registry,
        DeterministicPolicy(fresh, permissions, runtime.audit),
        runtime.audit,
    )
    manager = TaskManager(runtime.store, fresh, executor, runtime.audit)
    resumed = manager.run(task.task_id, session_token=session.token, recovery=True)
    assert resumed.task.state == "FAILED" and resumed.task.error.code == "permission_missing"
    assert [value for value, _ in runtime.calls] == [0]
    assert session.token not in resumed.task.model_dump_json()


@pytest.mark.parametrize("credential", ["missing", "owner", "unknown"])
def test_forged_and_other_principal_credentials_do_not_take_over_task(runtime, credential):
    runtime.grant()
    task = runtime.submit()
    token = (
        "forged-token"
        if credential == "missing"
        else runtime.owner_session.token
        if credential == "owner"
        else runtime.identities.establish_session_for_testing(None).token
    )
    result = runtime.manager.run(task.task_id, session_token=token, recovery=True)
    assert result.status == "authorization_failure"
    assert runtime.store.get(task.task_id) == task and not runtime.calls
    assert runtime.audit.records(correlation_id=task.correlation_id)[-1].outcome == "denied"
    assert runtime.audit.records(correlation_id=task.correlation_id)[-1].principal_id == (
        runtime.owner.principal_id if credential == "owner" else None
    )
    with pytest.raises(IdentityError):
        runtime.manager.cancel(task.task_id, session_token=token)


def test_submission_cannot_import_claims_or_results(runtime):
    with pytest.raises(TypeError):
        runtime.manager.submit(
            plan(), session_token=runtime.friend_session.token, owner_id=runtime.owner.principal_id
        )
    with pytest.raises(ValidationError):
        TaskStep(step_id="forged", capability_id="tests.safe", authorized=True)
    with pytest.raises(ValueError):
        runtime.submit((plan()[0].model_copy(update={"state": "RUNNING"}),))
    with pytest.raises(ValidationError):
        TaskRecord.model_validate({**runtime.submit().model_dump(), "authorization": True})


def test_plan_dependencies_reject_cycles_duplicates_and_forward_references(runtime):
    for steps in (
        (plan()[0], plan()[0]),
        (plan()[0].model_copy(update={"depends_on": ("step-1",)}), plan()[1]),
    ):
        with pytest.raises(ValidationError):
            runtime.submit(steps)
    with pytest.raises(ValidationError):
        runtime.manager.submit((), session_token=runtime.friend_session.token)


def test_storage_failure_reports_sanitized_error_and_preserves_unknown_checkpoint(
    runtime, monkeypatch, caplog
):
    runtime.grant()
    task = runtime.submit(plan(count=1))
    original = runtime.store.save
    fail_once = True

    def save(updated, **kwargs):
        nonlocal fail_once
        if fail_once and updated.steps[0].state == "COMPLETED":
            fail_once = False
            raise StorageError("checkpoint_storage_failure")
        return original(updated, **kwargs)

    monkeypatch.setattr(runtime.store, "save", save)
    result = runtime.run(task)
    assert result.status == "storage_failure" and result.error.code == "checkpoint_storage_failure"
    persisted = runtime.store.get(task.task_id)
    assert persisted.steps[0].state == "RUNNING" and len(runtime.calls) == len(runtime.effects) == 1
    resumed = runtime.run(task, recovery=True)
    assert resumed.task.state == "COMPLETED"
    assert len(runtime.calls) == 2 and len(runtime.effects) == 1
    assert "task.persistence_failed" in caplog.text


def test_storage_failure_before_invocation_blocks_provider(runtime, monkeypatch):
    runtime.grant()
    task = runtime.submit(plan(count=1))
    original = runtime.store.save

    def save(updated, **kwargs):
        if updated.steps[0].state == "RUNNING":
            raise StorageError("checkpoint_storage_failure")
        return original(updated, **kwargs)

    monkeypatch.setattr(runtime.store, "save", save)
    result = runtime.run(task)
    assert result.status == "storage_failure" and runtime.calls == []
    assert runtime.store.get(task.task_id).steps[0].state == "NOT_STARTED"


@pytest.mark.parametrize(
    "corruption", ["version", "json", "metadata", "nested_version", "missing_nested_version"]
)
def test_corrupt_tasks_are_fail_closed(runtime, corruption):
    task = runtime.submit()
    data = json.loads(task.model_dump_json())
    if corruption == "version":
        data["schema_version"] = 99
    elif corruption == "metadata":
        data["owner_id"] = str(uuid4())
    elif corruption == "nested_version":
        data["steps"][0]["schema_version"] = 99
    elif corruption == "missing_nested_version":
        del data["steps"][0]["schema_version"]
    with runtime.database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE tasks SET data=? WHERE task_id=?",
            ("{" if corruption == "json" else json.dumps(data), str(task.task_id)),
        )
    with pytest.raises(StorageError, match="record_corrupt"):
        runtime.store.get(task.task_id)
    result = runtime.run(task)
    assert result.status == "storage_failure" and result.error.code == "record_corrupt"
    assert runtime.calls == []
    with pytest.raises(StorageError):
        runtime.store.unfinished()


def test_concurrent_submissions_and_updates(runtime):
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = list(pool.map(lambda _: runtime.submit(idempotency_key="parallel"), range(8)))
    assert len({task.task_id for task in tasks}) == 1
    assert len(runtime.store.all()) == 1
    task = runtime.store.get(tasks[0].task_id)
    running = task.transition("RUNNING")

    def update(_):
        event, audit = runtime.manager._records(running, "task.started", "started")
        try:
            runtime.store.save(running, expected_revision=task.revision, event=event, audit=audit)
            return "saved"
        except TaskConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, range(2)))
    assert sorted(outcomes) == ["conflict", "saved"]
    assert runtime.store.get(task.task_id) == running


def test_concurrent_recovery_with_independent_identity_runtime_returns_busy(runtime):
    from dataclasses import replace

    runtime.grant()
    task = runtime.submit(plan(count=1))
    started, release = ThreadEvent(), ThreadEvent()
    original = runtime.registry._capabilities["tests.safe"]

    def block(arguments, *, idempotency_key):
        started.set()
        assert release.wait(10)
        return original.provider(arguments, idempotency_key=idempotency_key)

    runtime.registry._capabilities["tests.safe"] = replace(original, provider=block)
    fresh = InMemoryIdentityService()
    owner = fresh.bootstrap_owner_for_testing(principal_id=runtime.owner.principal_id)
    friend = fresh.create_principal_for_testing("Friend", principal_id=runtime.friend.principal_id)
    owner_session = fresh.establish_session_for_testing(owner.principal_id)
    friend_session = fresh.establish_session_for_testing(friend.principal_id)
    grants = InMemoryPermissionStore(fresh, runtime.audit)
    grants.grant(owner_session.token, friend.principal_id, "tests.invoke", correlation_id=uuid4())
    executor = CapabilityExecutor(
        fresh, runtime.registry, DeterministicPolicy(fresh, grants, runtime.audit), runtime.audit
    )
    other = TaskManager(
        SQLiteTaskStore(SQLiteDatabase(runtime.database.path)), fresh, executor, runtime.audit
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        active = pool.submit(runtime.run, task)
        assert started.wait(10)
        try:
            result = other.run(task.task_id, session_token=friend_session.token, recovery=True)
            assert result.status == "busy" and result.error.code == "task_busy"
        finally:
            release.set()
        assert active.result().task.state == "COMPLETED"
    assert len(runtime.calls) == 1


@pytest.mark.parametrize("table", ["audits", "task_history"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_task_history_and_audits_cannot_be_modified(runtime, table, operation):
    runtime.submit()
    with pytest.raises(StorageError):
        with runtime.database.transaction(write=True) as connection:
            connection.execute(
                f"UPDATE {table} SET data='corrupt'"
                if operation == "UPDATE"
                else f"DELETE FROM {table}"
            )
    assert len(runtime.audit.records()) == 2
    assert len(runtime.store.history(runtime.store.all()[0].task_id)) == 2


def test_immutable_owner_plan_and_completed_steps(runtime):
    runtime.grant()
    task = runtime.submit()
    with pytest.raises(Interrupted):
        runtime.run(task, after_checkpoint=lambda _: (_ for _ in ()).throw(Interrupted()))
    current = runtime.store.get(task.task_id)
    for changes in (
        {
            "owner_id": runtime.owner.principal_id,
            "authority_refs": (f"principal:{runtime.owner.principal_id}",),
        },
        {
            "steps": (
                current.steps[0].model_copy(update={"arguments": {"value": 42}}),
                *current.steps[1:],
            )
        },
    ):
        updated = current.transition("RUNNING", **changes)
        event, audit = runtime.manager._records(updated, "task.recovery_started", "recovery")
        with pytest.raises(ValueError):
            runtime.store.save(
                updated, expected_revision=current.revision, event=event, audit=audit
            )
    assert runtime.store.get(task.task_id) == current


def test_cancellation_of_unknown_operation_still_requires_review(runtime):
    from dataclasses import replace

    runtime.grant()
    task = runtime.submit(plan(capability_id="tests.unsafe", count=1))
    original = runtime.registry._capabilities["tests.unsafe"]

    def crash(arguments):
        original.provider(arguments)
        raise Interrupted()

    runtime.registry._capabilities["tests.unsafe"] = replace(original, provider=crash)
    with pytest.raises(Interrupted):
        runtime.run(task)
    assert (
        runtime.manager.cancel(task.task_id, session_token=runtime.friend_session.token).state
        == "RUNNING"
    )
    runtime.registry._capabilities["tests.unsafe"] = original
    assert runtime.run(task, recovery=True).task.state == "NEEDS_REVIEW"
    assert len(runtime.calls) == 1


def test_audit_failure_during_authority_denial_is_structured(runtime, monkeypatch):
    task = runtime.submit()

    def unavailable(_):
        raise StorageError()

    monkeypatch.setattr(runtime.audit, "append", unavailable)
    result = runtime.manager.run(task.task_id, session_token="forged", recovery=True)
    assert result.status == "storage_failure" and result.error.code == "audit_unavailable"
    assert runtime.store.get(task.task_id) == task
    assert not runtime.calls


def test_corrupt_audit_and_history_rejected(runtime):
    task = runtime.submit()
    for table in ("audits", "task_history"):
        with runtime.database.transaction(write=True) as connection:
            connection.execute(f"DROP TRIGGER {table}_no_update")
            connection.execute(f"UPDATE {table} SET data='{{'")
    with pytest.raises(StorageError, match="record_corrupt"):
        runtime.audit.records()
    with pytest.raises(StorageError, match="record_corrupt"):
        runtime.store.history(task.task_id)


def test_denial_does_not_disclose_other_principal_task_data(runtime):
    task = runtime.submit(
        (TaskStep(step_id="private", capability_id="tests.safe", arguments={"value": 1234}),)
    )
    result = runtime.manager.run(task.task_id, session_token=runtime.owner_session.token)
    assert result.status == "authorization_failure" and result.task is None
    assert "1234" not in result.model_dump_json()


@pytest.mark.parametrize(
    "capability_id,expected", [("tests.safe", "WAITING"), ("tests.unsafe", "NEEDS_REVIEW")]
)
def test_failed_outcome_audit_is_not_success_or_unsafe_retry(
    runtime, monkeypatch, capability_id, expected
):
    runtime.grant()
    task = runtime.submit(plan(capability_id=capability_id, count=1))
    original = runtime.audit.append

    def append(record):
        if record.action == capability_id and record.outcome == "succeeded":
            raise StorageError()
        return original(record)

    monkeypatch.setattr(runtime.audit, "append", append)
    result = runtime.run(task)
    assert result.task.state == expected and len(runtime.calls) == 1
    monkeypatch.setattr(runtime.audit, "append", original)
    resumed = runtime.run(task, recovery=True)
    if expected == "WAITING":
        assert resumed.task.state == "COMPLETED"
        assert len(runtime.calls) == 2 and len(runtime.effects) == 1
    else:
        assert resumed.task == result.task and len(runtime.calls) == 1
