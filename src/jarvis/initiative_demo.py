"""Offline initiatives through real services and a separate-process continuation."""

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from jarvis.cognitive_runtime import development_runtime
from jarvis.core.contracts import AccessScope
from jarvis.core.initiative_records import InitiativePolicy
from jarvis.core.time import utc_now
from jarvis.core.world_records import ObservationInput, SensorPayload, SourceRegistration


class FixtureClock:
    def __init__(self, now=None):
        self.now = now or utc_now()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def install_policy(
    runtime,
    owner,
    key,
    *,
    mode="OBSERVE_ONLY",
    capability=None,
    arguments=None,
    value=True,
    observation_type="motion",
    ttl=3600,
    shared_with=(),
    principal_id=None,
    **limits,
):
    source = SourceRegistration(
        source_id=f"{key}.sensor",
        principal_id=runtime.identities.owner_id,
        access_scope=AccessScope(
            owner_id=runtime.identities.owner_id,
            kind="shared" if shared_with else "private",
            shared_with=shared_with,
        ),
        observation_types=(observation_type,),
        entities=(key,),
        location=key,
        ttl_seconds=ttl,
    )
    runtime.world.register_source(source, session_token=owner.token)
    policy = InitiativePolicy(
        policy_id=key,
        principal_id=principal_id or runtime.identities.owner_id,
        target_id=principal_id or runtime.identities.owner_id,
        source_id=source.source_id,
        entity_id=key,
        location=key,
        attribute={
            "motion": "motion",
            "person_detected": "occupancy",
            "occupancy": "occupancy",
            "availability": "online",
        }[observation_type],
        expected_value=value,
        mode=mode,
        capability_id=capability,
        arguments=arguments or {},
        allow_inferred=observation_type == "person_detected",
        **limits,
    )
    runtime.initiatives.configure(policy, session_token=owner.token)
    return policy


def emit(runtime, owner, policy, value=None):
    source = runtime.world._store._source(policy.source_id, access=runtime.world._access)
    return runtime.world.ingest(
        ObservationInput(
            source_id=source.source_id,
            observation_type=source.observation_types[0],
            entity_id=policy.entity_id,
            location=policy.location,
            observed_at=runtime.world.clock(),
            confidence=0.9,
            payload=SensorPayload(value=policy.expected_value if value is None else value),
        ),
        session_token=owner.token,
    )


def process_all(runtime, owner, **kwargs):
    # Finite pages: each pass has a fixed limit; no unbounded background loop.
    for _ in range(4):
        runtime.initiatives.process(session_token=owner.token, limit=100, **kwargs)


def summary(record):
    return {
        "initiative_id": str(record.initiative_id),
        "state": record.state,
        "decision": record.decision.reason_code,
        "evidence_ids": [str(i) for i in record.proposal.evidence_ids[:2]],
        "approval": record.approval.state if record.approval else None,
        "task_id": str(record.execution.task_id) if record.execution else None,
        "audit_ref": str(record.audit_id),
        "result": record.execution.result.status
        if record.execution and record.execution.result
        else None,
    }


def _first(path):
    clock = FixtureClock()
    runtime = development_runtime(path, bootstrap=True, clock=clock)
    owner = runtime.credentials("owner")
    runtime.permissions.grant(
        owner.token, runtime.identities.owner_id, "world.ingest", correlation_id=uuid4()
    )
    porch = install_policy(runtime, owner, "porch", observation_type="person_detected")
    emit(runtime, owner, porch)
    process_all(runtime, owner)
    observe = next(
        r
        for r in runtime.initiatives.list(session_token=owner.token)
        if r.proposal.policy_id == "porch"
    )
    assert observe.state == "PROPOSED" and not runtime.lighting.calls and not runtime.notices.calls
    recovery = install_policy(
        runtime,
        owner,
        "device",
        mode="APPROVAL_REQUIRED",
        observation_type="availability",
        value=False,
        capability="home.lighting.set_brightness",
        arguments={"brightness": 30},
    )
    emit(runtime, owner, recovery)
    process_all(runtime, owner)
    pending = next(
        r
        for r in runtime.initiatives.list(session_token=owner.token)
        if r.proposal.policy_id == "device"
    )
    assert pending.state == "AWAITING_APPROVAL" and not runtime.lighting.calls
    runtime.initiatives.approve(pending.initiative_id, session_token=owner.token)
    approved = runtime.initiatives.execute(pending.initiative_id, session_token=owner.token)
    assert approved.state == "COMPLETED" and runtime.lighting.calls == [30]
    auto = install_policy(
        runtime,
        owner,
        "notice",
        mode="AUTO_EXECUTE",
        capability="development.simulated_notice",
        arguments={"text": "Simulated local recommendation"},
    )
    runtime.permissions.grant(
        owner.token, runtime.identities.owner_id, "initiatives.auto:notice", correlation_id=uuid4()
    )
    emit(runtime, owner, auto)
    process_all(runtime, owner)
    automatic = next(
        r
        for r in runtime.initiatives.list(session_token=owner.token)
        if r.proposal.policy_id == "notice"
    )
    assert automatic.state == "COMPLETED" and len(runtime.notices.calls) == 1
    for _ in range(5):
        emit(runtime, owner, auto)
    process_all(runtime, owner)
    suppressed = [
        r for r in runtime.initiatives.list(session_token=owner.token) if r.state == "SUPPRESSED"
    ]
    assert len(runtime.notices.calls) == 1 and suppressed
    revoke = install_policy(
        runtime,
        owner,
        "revoked",
        mode="APPROVAL_REQUIRED",
        capability="home.lighting.set_brightness",
        arguments={"brightness": 99},
    )
    emit(runtime, owner, revoke)
    process_all(runtime, owner)
    revoked = next(
        r
        for r in runtime.initiatives.list(session_token=owner.token)
        if r.proposal.policy_id == "revoked"
    )
    runtime.initiatives.approve(revoked.initiative_id, session_token=owner.token)
    runtime.permissions.revoke(
        owner.token,
        runtime.identities.owner_id,
        "home.lighting.set_brightness",
        correlation_id=uuid4(),
    )
    revoked = runtime.initiatives.execute(revoked.initiative_id, session_token=owner.token)
    assert revoked.state == "FAILED" and runtime.lighting.calls == [30]
    uncertain = install_policy(
        runtime,
        owner,
        "uncertain",
        mode="APPROVAL_REQUIRED",
        ttl=1,
        capability="development.simulated_notice",
        arguments={"text": "Must never execute"},
    )
    emit(runtime, owner, uncertain)
    process_all(runtime, owner)
    stale = next(
        r
        for r in runtime.initiatives.list(session_token=owner.token)
        if r.proposal.policy_id == "uncertain"
    )
    runtime.initiatives.approve(stale.initiative_id, session_token=owner.token)
    clock.advance(2)
    stale = runtime.initiatives.execute(stale.initiative_id, session_token=owner.token)
    assert stale.state == "FAILED" and len(runtime.notices.calls) == 1
    restart = install_policy(
        runtime,
        owner,
        "restart",
        mode="APPROVAL_REQUIRED",
        capability="development.simulated_notice",
        arguments={"text": "Pending across restart"},
    )
    emit(runtime, owner, restart)
    process_all(runtime, owner)
    pending_restart = next(
        r
        for r in runtime.initiatives.list(session_token=owner.token)
        if r.proposal.policy_id == "restart"
    )
    conversation = runtime.conversations.start(session_token=owner.token)
    response = asyncio.run(
        runtime.coordinator.respond(
            conversation.conversation_id,
            "What actions are awaiting my approval?",
            session_token=owner.token,
        )
    )
    assert "AWAITING_APPROVAL" in response.text and response.execution is None
    ambiguous_policy = install_policy(
        runtime,
        owner,
        "ambiguous",
        mode="APPROVAL_REQUIRED",
        capability="development.simulated_notice",
        arguments={"text": "Ambiguous simulated call"},
    )
    emit(runtime, owner, ambiguous_policy)
    process_all(runtime, owner)
    ambiguous = next(
        r
        for r in runtime.initiatives.list(session_token=owner.token)
        if r.proposal.policy_id == "ambiguous"
    )
    runtime.initiatives.approve(ambiguous.initiative_id, session_token=owner.token)
    metadata = {
        "now": clock().isoformat(),
        "ambiguous": str(ambiguous.initiative_id),
        "pending": str(pending_restart.initiative_id),
        "completed": [str(approved.initiative_id), str(automatic.initiative_id)],
        "suppressed": str(suppressed[0].initiative_id),
        "conversation": str(conversation.conversation_id),
    }
    path.with_suffix(".demo.json").write_text(json.dumps(metadata))
    return {
        "A": summary(observe),
        "B": summary(approved),
        "C": summary(automatic),
        "D": {"suppressed": len(suppressed), "notice_handler_calls": len(runtime.notices.calls)},
        "E": summary(revoked),
        "F": summary(stale),
        "G_pending": summary(pending_restart),
        "handler_calls": {
            "lighting": len(runtime.lighting.calls),
            "notice": len(runtime.notices.calls),
        },
    }


def _second(path):
    metadata = json.loads(path.with_suffix(".demo.json").read_text())
    runtime = development_runtime(path, clock=FixtureClock(datetime.fromisoformat(metadata["now"])))
    owner = runtime.credentials("owner")
    before = {
        i: runtime.tasks.store.get(
            runtime.initiatives.get(UUID(i), session_token=owner.token).execution.task_id
        )
        for i in metadata["completed"]
    }
    runtime.initiatives.recover(session_token=owner.token)
    for i, task in before.items():
        r = runtime.initiatives.execute(UUID(i), session_token=owner.token)
        assert runtime.tasks.store.get(r.execution.task_id) == task and r.state == "COMPLETED"
    ambiguous = runtime.initiatives.get(UUID(metadata["ambiguous"]), session_token=owner.token)
    assert ambiguous.state == "NEEDS_REVIEW"
    assert path.with_suffix(".effects").read_text().splitlines() == ["invoked"]
    pending = runtime.initiatives.get(UUID(metadata["pending"]), session_token=owner.token)
    assert pending.state == "AWAITING_APPROVAL"
    policy = next(
        p
        for p in runtime.initiatives.policies(session_token=owner.token)
        if p.policy_id == "notice"
    )
    emit(runtime, owner, policy)
    process_all(runtime, owner)
    assert not runtime.notices.calls and not runtime.lighting.calls
    assert not runtime.permissions.current_grants(
        runtime.identities.owner_id, ("home.lighting.set_brightness",)
    )
    response = asyncio.run(
        runtime.coordinator.respond(
            UUID(metadata["conversation"]),
            "What actions are awaiting my approval?",
            session_token=owner.token,
        )
    )
    assert "AWAITING_APPROVAL" in response.text
    return {
        "pending": summary(pending),
        "ambiguous": summary(ambiguous),
        "completed_not_repeated": len(before),
        "handler_calls_after_restart": {
            "lighting": len(runtime.lighting.calls),
            "notice": len(runtime.notices.calls),
        },
        "revoked_permission_restored": False,
        "conversation_continued": response.kind == "direct",
    }


def _ambiguous(path):
    metadata = json.loads(path.with_suffix(".demo.json").read_text())
    runtime = development_runtime(path, clock=FixtureClock(datetime.fromisoformat(metadata["now"])))
    owner = runtime.credentials("owner")
    registry = runtime.coordinator.context.registry
    registered = registry._capabilities["development.simulated_notice"]

    def provider(arguments):
        # A real simulated invocation occurs, then the process dies before its result checkpoint.
        runtime.notices(arguments)
        with path.with_suffix(".effects").open("a") as stream:
            stream.write("invoked\n")
            stream.flush()
            os.fsync(stream.fileno())
        os._exit(17)

    registry._capabilities["development.simulated_notice"] = replace(registered, provider=provider)
    runtime.initiatives.execute(UUID(metadata["ambiguous"]), session_token=owner.token)


def run_initiative_demo():
    path = Path(".jarvis/initiative-demo") / str(uuid4()) / "journal.sqlite3"
    path.parent.mkdir(parents=True)
    result = {}
    for stage in ("first", "ambiguous", "second"):
        completed = subprocess.run(
            [sys.executable, "-m", "jarvis.initiative_demo", stage, str(path.resolve())],
            capture_output=True,
            text=True,
            check=False,
        )
        if stage == "ambiguous":
            if completed.returncode != 17:
                raise RuntimeError("controlled initiative interruption failed")
            continue
        completed.check_returncode()
        result[stage] = json.loads(completed.stdout)
    return {
        "status": "ok",
        "mode": "offline_simulated_development",
        "database_path": str(path.resolve()),
        **result,
    }


if __name__ == "__main__":
    if sys.argv[1] == "ambiguous":
        _ambiguous(Path(sys.argv[2]))
    else:
        print(json.dumps((_first if sys.argv[1] == "first" else _second)(Path(sys.argv[2]))))
