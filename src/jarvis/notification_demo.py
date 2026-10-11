"""Three real processes exercise admission, interaction, delivery and crash recovery."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from jarvis.cognitive_runtime import FRIEND_ID, development_runtime
from jarvis.core.notification_records import (
    NotificationPolicy,
    NotificationPreference,
    NotificationRecipient,
    NotificationSource,
)
from jarvis.core.task_records import TaskStep
from jarvis.initiative_demo import FixtureClock, emit, install_policy, process_all
from jarvis.notifications import NotificationError


def install(
    runtime, token, name, *, kind="task", recipient=None, origin=None, channels=("simulated-push",)
):
    return runtime.notifications.configure(
        NotificationPolicy(
            policy_id=name,
            origin_id=origin or runtime.identities.owner_id,
            recipient=NotificationRecipient(principal_id=recipient or runtime.identities.owner_id),
            source_kind=kind,
            channels=channels,
            cooldown_seconds=0,
        ),
        session_token=token,
    )


def completed_task(runtime, token):
    task = runtime.tasks.submit(
        (
            TaskStep(
                step_id="echo",
                capability_id="development.echo",
                arguments={"text": "notification demo"},
            ),
        ),
        session_token=token,
    )
    task = runtime.tasks.run(task.task_id, session_token=token).task
    assert task.state == "COMPLETED"
    return task


def create(runtime, token, policy, source):
    return runtime.notifications.create(
        policy.policy_id,
        NotificationSource(
            kind=policy.source_kind,
            source_id=source.task_id if policy.source_kind == "task" else source.initiative_id,
            revision=source.revision,
        ),
        session_token=token,
    )


def delivery(runtime, token, notification_id):
    return next(
        d
        for d in runtime.notifications.deliveries(notification_id, session_token=token)
        if d.channel.channel_id != "local-inbox"
    )


def stage_one(path):
    clock = FixtureClock(datetime(2026, 10, 11, 10, 30, tzinfo=UTC))
    rt = development_runtime(path, bootstrap=True, clock=clock)
    owner, guest, friend = (
        rt.credentials("owner"),
        rt.credentials("guest"),
        rt.credentials("friend"),
    )
    token = owner.token
    p = install(rt, token, "task-updates")
    t = completed_task(rt, token)
    basic = create(rt, token, p, t)
    assert rt.notifications.get(basic.notification_id, session_token=token)
    for reference in (basic.notification_id, uuid4()):
        try:
            rt.notifications.get(reference, session_token=guest.token)
            raise AssertionError("guest accessed private inbox")
        except NotificationError as exc:
            assert exc.code == "notification_unavailable"
    for operation in ("read", "acknowledge"):
        basic = rt.notifications.interact(
            basic.notification_id, operation, expected_revision=basic.revision, session_token=token
        )
    rt.notifications.deliver(
        delivery(rt, token, basic.notification_id).delivery_id, session_token=token
    )
    rt.permissions.grant(token, rt.identities.owner_id, "world.ingest", correlation_id=uuid4())
    initiative_policy = install_policy(
        rt,
        owner,
        "garage",
        mode="APPROVAL_REQUIRED",
        capability="development.simulated_notice",
        arguments={"text": "approved local fixture"},
        ttl=3600,
        max_age_seconds=3600,
    )
    emit(rt, owner, initiative_policy)
    process_all(rt, owner)
    initiative = rt.initiatives.list(session_token=token)[0]
    ip = install(rt, token, "initiative-updates", kind="initiative")
    approval = create(rt, token, ip, initiative)
    for operation in ("read", "acknowledge"):
        approval = rt.notifications.interact(
            approval.notification_id,
            operation,
            expected_revision=approval.revision,
            session_token=token,
        )
        assert not rt.notices.calls
    verified = rt.notifications.approval(
        approval.notification_id, expected_revision=approval.revision, session_token=token
    )
    assert verified.state == "COMPLETED" and len(rt.notices.calls) == 1
    assert (
        rt.notifications.source_context(approval.notification_id, session_token=token)["state"]
        == "COMPLETED"
    )
    # Quiet hours retain an authoritative local inbox and defer simulated transport.
    prefs = NotificationPreference(
        principal_id=rt.identities.owner_id,
        channels=("simulated-push", "simulated-unreliable"),
        quiet_start_minute=600,
        quiet_end_minute=660,
    )
    rt.notifications.set_preferences(prefs, session_token=token)
    quiet_note = create(rt, token, p, completed_task(rt, token))
    d = rt.notifications.deliver(
        delivery(rt, token, quiet_note.notification_id).delivery_id, session_token=token
    )
    assert d.state == "RETRY_WAIT" and d.reason_code == "notification_quiet_hours"
    # Explicitly advance the injectable clock; no wall-clock sleeping or scheduler.
    clock.now = d.due_at
    unreliable = install(rt, token, "unreliable", channels=("simulated-unreliable",))
    transient = create(rt, token, unreliable, completed_task(rt, token))
    rt.notifications.channels["simulated-unreliable"].outcomes = ["temporary_failure"]
    d = rt.notifications.deliver(
        delivery(rt, token, transient.notification_id).delivery_id, session_token=token
    )
    assert d.state == "RETRY_WAIT"
    ambiguous = create(rt, token, unreliable, completed_task(rt, token))
    for _ in range(20):
        assert create(rt, token, p, t).notification_id == basic.notification_id
    fp = install(rt, token, "friend-updates", recipient=FRIEND_ID, origin=FRIEND_ID)
    friend_note = create(rt, friend.token, fp, completed_task(rt, friend.token))
    rt.permissions.revoke(token, FRIEND_ID, "notifications.deliver", correlation_id=uuid4())
    state = {
        "clock": clock().isoformat(),
        "basic": str(basic.notification_id),
        "approval": str(approval.notification_id),
        "quiet": str(quiet_note.notification_id),
        "transient": str(transient.notification_id),
        "ambiguous": str(ambiguous.notification_id),
        "friend": str(friend_note.notification_id),
    }
    path.with_suffix(".demo.json").write_text(json.dumps(state))
    return {
        "scenario": "admission_and_interactions",
        "guest_denial": "notification_unavailable",
        "approval_handler_calls": len(rt.notices.calls),
        "quiet_delivery": "RETRY_WAIT",
        "transient_delivery": "RETRY_WAIT",
        "duplicates_suppressed": 20,
        "push_calls": len(rt.notifications.channels["simulated-push"].calls),
        "unreliable_calls": len(rt.notifications.channels["simulated-unreliable"].calls),
    }


def stage_crash(path):
    from uuid import UUID

    state = json.loads(path.with_suffix(".demo.json").read_text())
    clock = FixtureClock(datetime.fromisoformat(state["clock"]))
    rt = development_runtime(path, clock=clock)
    owner = rt.credentials("owner")

    def effect(request, result):
        with path.with_suffix(".effect").open("a") as file:
            file.write(str(request.delivery_id) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os._exit(17)  # actual effect happened, task/delivery result was never checkpointed

    rt.notifications.channels["simulated-unreliable"].after_effect = effect
    rt.notifications.deliver(
        delivery(rt, owner.token, UUID(state["ambiguous"])).delivery_id, session_token=owner.token
    )
    raise AssertionError("crash hook did not run")


def stage_reopen(path):
    from uuid import UUID

    state = json.loads(path.with_suffix(".demo.json").read_text())
    clock = FixtureClock(datetime.fromisoformat(state["clock"]))
    clock.advance(61)
    rt = development_runtime(path, clock=clock)
    owner, friend = rt.credentials("owner"), rt.credentials("friend")
    recovered = rt.notifications.recover(session_token=owner.token)
    notes = {
        key: rt.notifications.get(UUID(state[key]), session_token=owner.token)
        for key in ("basic", "approval", "quiet", "transient", "ambiguous")
    }
    outcomes = {key: delivery(rt, owner.token, n.notification_id).state for key, n in notes.items()}
    assert notes["basic"].inbox_state == "ACKNOWLEDGED"
    assert outcomes["basic"] == outcomes["quiet"] == outcomes["transient"] == "DELIVERED"
    assert outcomes["ambiguous"] == "NEEDS_REVIEW"
    assert len(path.with_suffix(".effect").read_text().splitlines()) == 1
    assert not rt.permissions.current_grants(FRIEND_ID, ("notifications.deliver",))
    fd = rt.notifications.deliver(
        delivery(rt, friend.token, UUID(state["friend"])).delivery_id, session_token=friend.token
    )
    assert fd.state == "CANCELLED"
    assert not rt.notifications.recover(session_token=owner.token)
    assert not rt.notices.calls
    return {
        "scenario": "separate_process_recovery",
        "owner_notifications": len(rt.notifications.list(session_token=owner.token)[0]),
        "deliveries": outcomes,
        "basic_inbox": notes["basic"].inbox_state,
        "friend_revocation": fd.state,
        "ambiguous_effects": 1,
        "push_calls": len(rt.notifications.channels["simulated-push"].calls),
        "unreliable_calls": len(rt.notifications.channels["simulated-unreliable"].calls),
        "recovered_deliveries": len(recovered),
        "references": [
            {
                "notification_id": str(n.notification_id),
                "source_id": str(n.source.source_id),
                "audit_id": str(n.audit_id),
            }
            for n in notes.values()
        ],
    }


def run_notification_demo():
    path = (Path(".jarvis/notification-demo") / str(uuid4()) / "journal.sqlite3").resolve()
    first = subprocess.run(
        [sys.executable, "-m", "jarvis.notification_demo", "first", str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    crash = subprocess.run(
        [sys.executable, "-m", "jarvis.notification_demo", "crash", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if crash.returncode != 17:
        raise RuntimeError("notification demo crash verification failed")
    final = subprocess.run(
        [sys.executable, "-m", "jarvis.notification_demo", "reopen", str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return {
        "status": "ok",
        "mode": "offline_simulated",
        "database": str(path),
        "processes": 3,
        "first": json.loads(first.stdout),
        "reopen": json.loads(final.stdout),
    }


if __name__ == "__main__":
    stage, path = sys.argv[1], Path(sys.argv[2])
    if stage == "crash":
        stage_crash(path)
    else:
        print(json.dumps(stage_one(path) if stage == "first" else stage_reopen(path)))
