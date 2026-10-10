"""Independent offline verification process. IPC contains fixture credentials, never logs."""

import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

from jarvis.authorization import DeterministicPolicy
from jarvis.capabilities import InMemoryCapabilityRegistry
from jarvis.core.contracts import ActionProposal, Capability
from jarvis.core.task_records import TaskStep
from jarvis.execution import CapabilityExecutor
from jarvis.recovery_demo import SimulatedEffects
from jarvis.storage.security import SQLiteDevelopmentIdentityService, SQLitePermissionStore
from jarvis.storage.sqlite import SQLiteAuditSink, SQLiteDatabase, SQLiteTaskStore, _file_lock
from jarvis.task_manager import TaskManager
from jarvis.tasks import TaskBusyError


def send(event, **values):
    print(json.dumps({"event": event, **values}), flush=True)


def runtime(request):
    directory = Path(request["directory"])
    database = SQLiteDatabase(directory / "journal.sqlite3")
    identities = SQLiteDevelopmentIdentityService(database)
    audit = SQLiteAuditSink(database)
    permissions = SQLitePermissionStore(identities, audit)
    effects = SimulatedEffects(directory / "effects.sqlite3")
    parked = False

    def park(event):
        nonlocal parked
        if parked:
            return
        parked = True
        send(event)
        if sys.stdin.readline().strip() != "FINISH":
            raise RuntimeError("fixture_barrier_broken")

    def safe(arguments, *, idempotency_key):
        if request.get("barrier") == "before_effect":
            park("provider_entered")
        output = effects(arguments, idempotency_key=idempotency_key)
        if request.get("barrier") == "after_effect":
            park("provider_effect_committed")
        return output

    def unsafe(arguments):
        if request.get("barrier") == "before_effect":
            park("provider_entered")
        output = effects.non_idempotent(arguments)
        if request.get("barrier") == "after_effect":
            park("provider_effect_committed")
        return output

    registry = InMemoryCapabilityRegistry()
    for name, provider, idempotent in (("safe", safe, True), ("unsafe", unsafe, False)):
        registry.register(
            Capability(
                capability_id=f"verification.{name}",
                required_permissions=("verification.invoke",),
                side_effect="local",
                input_schema={
                    "type": "object",
                    "properties": {"label": {"type": "string"}},
                    "required": ["label"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {"label": {"type": "string"}},
                    "required": ["label"],
                    "additionalProperties": False,
                },
                adapter_ref=f"verification.simulated.{name}.v1",
            ),
            provider,
            supports_idempotency=idempotent,
        )
    executor = CapabilityExecutor(
        identities, registry, DeterministicPolicy(identities, permissions, audit), audit
    )
    store = SQLiteTaskStore(database)
    return (
        database,
        identities,
        permissions,
        audit,
        effects,
        executor,
        store,
        TaskManager(store, identities, executor, audit),
    )


def work(request):
    directory = Path(request["directory"])
    action = request["action"]
    if action == "init":
        directory.mkdir(parents=True, exist_ok=False)
        database = SQLiteDatabase(directory / "journal.sqlite3")
        database.initialize()
        identities = SQLiteDevelopmentIdentityService(database)
        owner = identities.bootstrap_owner_for_testing()
        friend = identities.create_principal_for_testing("Verification friend")
        owner_session = identities.establish_session_for_testing(owner.principal_id)
        friend_session = identities.establish_session_for_testing(friend.principal_id)
        permissions = SQLitePermissionStore(identities, SQLiteAuditSink(database))
        permissions.grant(
            owner_session.token, friend.principal_id, "verification.invoke", correlation_id=uuid4()
        )
        send(
            "result",
            owner_id=str(owner.principal_id),
            friend_id=str(friend.principal_id),
            owner_session_id=str(owner_session.session_id),
            friend_session_id=str(friend_session.session_id),
            owner_token=owner_session.token,
            friend_token=friend_session.token,
        )
        return
    database, identities, permissions, audit, effects, executor, store, manager = runtime(request)
    if action in {"revoke", "grant"}:
        if request.get("announce"):
            send("admin_started")
        getattr(permissions, action)(
            request["owner_token"],
            UUID(request["friend_id"]),
            "verification.invoke",
            correlation_id=uuid4(),
        )
        send("result", status="committed")
    elif action == "inspect":
        principal_id = UUID(request["friend_id"])
        principal = identities.get_principal(principal_id)
        session = identities.get_session(UUID(request["friend_session_id"]))
        grants = permissions.current_grants(principal_id, ("verification.invoke",))
        send(
            "result",
            principal_id=str(principal.principal_id),
            owner_id=str(identities.owner_id),
            session_principal=str(session.principal_id),
            grants=[g.policy_ref for g in grants],
            audits=[
                {
                    "action": a.action,
                    "outcome": a.outcome,
                    "reason_code": a.reason_code,
                    "principal_id": str(a.principal_id) if a.principal_id else None,
                    "decision_id": str(a.decision_id) if a.decision_id else None,
                }
                for a in audit.records()
            ],
        )
    elif action == "execute":
        proposal = ActionProposal(
            session_id=UUID(request["friend_session_id"]),
            capability_id="verification.safe",
            arguments={"label": "direct"},
            correlation_id=uuid4(),
            origin="model",
            source_ref="verification.fake_model",
        )
        result = executor.execute(proposal, session_token=request["friend_token"])
        send(
            "result",
            status=result.status,
            reason_code=result.error.code if result.error else None,
            calls=effects.counts()[0],
        )
    elif action == "submit":
        labels = request.get("labels", ["first", "second", "third"])
        steps = tuple(
            TaskStep(
                step_id=label,
                capability_id="verification.unsafe"
                if request.get("unsafe")
                else "verification.safe",
                arguments={"label": label},
                depends_on=() if not index else (labels[index - 1],),
            )
            for index, label in enumerate(labels)
        )
        task = manager.submit(
            steps, session_token=request["friend_token"], idempotency_key=request.get("key")
        )
        send("result", task_id=str(task.task_id), state=task.state)
    elif action == "run":
        if request.get("ready"):
            send("ready")
            if sys.stdin.readline().strip() != "GO":
                raise RuntimeError("fixture_barrier_broken")

        def stop_after_first(task):
            if request.get("checkpoint_crash"):
                import os

                os._exit(73)

        result = manager.run(
            UUID(request["task_id"]),
            session_token=request["friend_token"],
            recovery=True,
            after_checkpoint=stop_after_first,
        )
        send(
            "result",
            status=result.status,
            state=result.task.state if result.task else None,
            reason_code=result.error.code
            if result.error
            else result.task.error.code
            if result.task and result.task.error
            else None,
            private_task_returned=result.task is not None,
        )
    elif action == "probe_security_lock":
        try:
            with _file_lock(database.path.with_name(database.path.name + ".security.lock")):
                send("result", status="available")
        except TaskBusyError:
            send("result", status="busy")
    elif action == "state":
        task = store.get(UUID(request["task_id"]))
        send(
            "result",
            state=task.state,
            steps=[s.state for s in task.steps],
            revision=task.revision,
            attempts={
                s.step_id: [
                    {"attempt_id": str(a.attempt_id), "key": a.idempotency_key, "state": a.state}
                    for a in s.attempts
                ]
                for s in task.steps
            },
            calls=effects.counts()[0],
            effects=effects.counts()[1],
        )
    elif action == "close":
        identities.close_session_for_testing(request["friend_token"])
        send("result", status="closed")
    elif action == "disable":
        identities.disable_principal_for_testing(UUID(request["friend_id"]))
        send("result", status="disabled")
    else:
        raise RuntimeError("unknown_fixture_action")


if __name__ == "__main__":
    try:
        work(json.loads(sys.stdin.readline()))
    except Exception as exc:
        send("error", code=getattr(exc, "reason_code", getattr(exc, "code", "worker_failed")))
        raise SystemExit(1) from None
