import json
import selectors
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

WORKER = Path(__file__).parent / "helpers" / "security_worker.py"


def call(request, *, expected_exit=0):
    process = subprocess.run(
        [sys.executable, str(WORKER)],
        input=json.dumps(request) + "\n",
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert process.returncode == expected_exit, process.stderr
    return json.loads(process.stdout.strip()) if process.stdout.strip() else None


@pytest.fixture
def authority(tmp_path):
    request = {"directory": str(tmp_path / "authority")}
    initialized = call({**request, "action": "init"})
    return {**request, **{k: v for k, v in initialized.items() if k != "event"}}


def start(request):
    process = subprocess.Popen(
        [sys.executable, str(WORKER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    process.stdin.write(json.dumps(request) + "\n")
    process.stdin.flush()
    return process


def receive(process, *, timeout=15):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(timeout), "fixture process did not reach synchronization barrier"
        line = process.stdout.readline()
    assert line, "fixture process exited before barrier"
    return json.loads(line)


def send(process, message):
    process.stdin.write(message + "\n")
    process.stdin.flush()


def finish(process):
    process.stdin.close()
    assert process.wait(timeout=15) == 0, process.stderr.read()
    process.stdout.close()
    process.stderr.close()


def stop(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=15)
    for stream in (process.stdin, process.stdout, process.stderr):
        if not stream.closed:
            stream.close()


def test_grant_and_audits_survive_independent_restarts(authority):
    first = call({**authority, "action": "inspect"})
    assert first["principal_id"] == authority["friend_id"]
    assert first["owner_id"] == authority["owner_id"]
    assert first["session_principal"] == authority["friend_id"]
    assert len(first["grants"]) == 1
    assert call({**authority, "action": "execute"})["status"] == "success"
    restarted = call({**authority, "action": "inspect"})
    assert restarted["grants"] == first["grants"]
    assert [a["outcome"] for a in restarted["audits"]] == ["succeeded", "allowed", "succeeded"]
    assert restarted["audits"][1]["decision_id"] == restarted["audits"][2]["decision_id"]
    assert restarted["audits"][1]["principal_id"] == authority["friend_id"]


def test_revocation_persists_and_new_process_denies_execution(authority):
    assert call({**authority, "action": "revoke"})["status"] == "committed"
    assert call({**authority, "action": "inspect"})["grants"] == []
    denied = call({**authority, "action": "execute"})
    assert denied["status"] == "denied" and denied["reason_code"] == "permission_missing"
    assert denied["calls"] == {}
    history = call({**authority, "action": "inspect"})["audits"]
    assert any(a["action"] == "permission.revoke" and a["outcome"] == "succeeded" for a in history)
    assert history[-1]["outcome"] == "denied"
    assert call({**authority, "action": "execute"})["status"] == "denied"


@pytest.mark.parametrize("field", ["friend_id", "friend_session_id"])
def test_model_generated_principal_or_session_claim_does_not_authenticate(authority, field):
    result = call({**authority, "action": "execute", "friend_token": authority[field]})
    assert result["status"] == "denied" and result["calls"] == {}


@pytest.mark.parametrize("action", ["close", "disable"])
def test_session_and_principal_revocation_survive_new_process(authority, action):
    call({**authority, "action": action})
    result = call({**authority, "action": "execute"})
    assert result["status"] == "denied" and result["calls"] == {}


@pytest.mark.parametrize(
    "damage",
    [
        "missing_database",
        "missing_authority",
        "corrupt_owner",
        "corrupt_grant",
        "unsupported_grant",
        "deleted_grant",
    ],
)
def test_missing_corrupt_or_deleted_authority_fails_closed_across_processes(authority, damage):
    path = Path(authority["directory"]) / "journal.sqlite3"
    if damage == "missing_database":
        path.unlink()
    else:
        with sqlite3.connect(path) as connection:
            if damage == "missing_authority":
                connection.execute("DROP TABLE security_authority")
            elif damage == "corrupt_owner":
                connection.execute("UPDATE security_principals SET data='{' WHERE kind='owner'")
            elif damage == "deleted_grant":
                connection.execute("DROP TRIGGER security_grants_no_delete")
                connection.execute("DELETE FROM security_grants")
            else:
                connection.execute("DROP TRIGGER security_grants_no_resurrection")
                if damage == "corrupt_grant":
                    connection.execute("UPDATE security_grants SET data='{'")
                else:
                    data = json.loads(
                        connection.execute("SELECT data FROM security_grants").fetchone()[0]
                    )
                    data["schema_version"] = 99
                    connection.execute("UPDATE security_grants SET data=?", (json.dumps(data),))
    result = call({**authority, "action": "execute"})
    assert result["status"] != "success" and result["calls"] == {}
    if damage == "missing_database":
        assert not path.exists()  # no creation/bootstrap fallback


def test_two_independent_recovery_workers_race_with_one_active_claim(authority):
    task = call({**authority, "action": "submit", "key": "race"})
    request = {
        **authority,
        "action": "run",
        "task_id": task["task_id"],
        "ready": True,
        "barrier": "before_effect",
    }
    workers = [start(request), start(request)]
    try:
        assert [receive(w)["event"] for w in workers] == ["ready", "ready"]
        for worker in workers:
            send(worker, "GO")
        messages = [receive(worker) for worker in workers]
        assert sorted(m["event"] for m in messages) == ["provider_entered", "result"]
        winner = messages.index(next(m for m in messages if m["event"] == "provider_entered"))
        loser = messages[1 - winner]
        assert loser["status"] == "busy" and not loser["private_task_returned"]
        send(workers[winner], "FINISH")
        assert receive(workers[winner])["state"] == "COMPLETED"
        for worker in workers:
            finish(worker)
    finally:
        for worker in workers:
            stop(worker)
    state = call({**authority, "action": "state", "task_id": task["task_id"]})
    assert state["calls"] == state["effects"] == {"first": 1, "second": 1, "third": 1}
    for _ in range(3):
        assert (
            call({**authority, "action": "run", "task_id": task["task_id"]})["state"] == "COMPLETED"
        )
    assert (
        call({**authority, "action": "state", "task_id": task["task_id"]})["calls"]
        == state["calls"]
    )


@pytest.mark.parametrize("unsafe", [False, True])
def test_crashed_worker_releases_claim_without_proving_effect_absence(authority, unsafe):
    task = call({**authority, "action": "submit", "labels": ["dangerous"], "unsafe": unsafe})
    request = {**authority, "action": "run", "task_id": task["task_id"], "barrier": "after_effect"}
    worker = start(request)
    try:
        assert receive(worker)["event"] == "provider_effect_committed"
        competing = call({**authority, "action": "run", "task_id": task["task_id"]})
        assert competing["status"] == "busy"
        assert call({**authority, "action": "probe_security_lock"})["status"] == "busy"
        worker.kill()
        assert worker.wait(timeout=15) == -signal.SIGKILL
    finally:
        stop(worker)
    resumed = call({**authority, "action": "run", "task_id": task["task_id"]})
    assert resumed["state"] == ("NEEDS_REVIEW" if unsafe else "COMPLETED")
    state = call({**authority, "action": "state", "task_id": task["task_id"]})
    assert state["effects"] == {"dangerous": 1}
    assert state["calls"] == {"dangerous": 1 if unsafe else 2}
    attempts = state["attempts"]["dangerous"]
    assert attempts[0]["state"] == "OUTCOME_UNKNOWN"
    if not unsafe:
        assert attempts[0]["key"] == attempts[1]["key"]
        assert attempts[0]["attempt_id"] != attempts[1]["attempt_id"]
    assert (
        call({**authority, "action": "run", "task_id": task["task_id"]})["state"]
        == resumed["state"]
    )
    assert (
        call({**authority, "action": "state", "task_id": task["task_id"]})["calls"]
        == state["calls"]
    )


def test_recovery_does_not_restore_saved_grants_after_revocation(authority):
    task = call({**authority, "action": "submit"})
    call(
        {**authority, "action": "run", "task_id": task["task_id"], "checkpoint_crash": True},
        expected_exit=73,
    )
    before = call({**authority, "action": "state", "task_id": task["task_id"]})
    assert before["steps"] == ["COMPLETED", "NOT_STARTED", "NOT_STARTED"]
    call({**authority, "action": "revoke"})
    result = call({**authority, "action": "run", "task_id": task["task_id"]})
    assert result["state"] == "FAILED" and result["reason_code"] == "permission_missing"
    assert call({**authority, "action": "state", "task_id": task["task_id"]})["calls"] == {
        "first": 1
    }
    assert call({**authority, "action": "inspect"})["grants"] == []


def test_completed_checkpoint_is_not_repeated_by_a_new_recovery_process(authority):
    task = call({**authority, "action": "submit"})
    call(
        {**authority, "action": "run", "task_id": task["task_id"], "checkpoint_crash": True},
        expected_exit=73,
    )
    assert call({**authority, "action": "run", "task_id": task["task_id"]})["state"] == "COMPLETED"
    state = call({**authority, "action": "state", "task_id": task["task_id"]})
    assert state["calls"] == {"first": 1, "second": 1, "third": 1}
    assert all(len(attempts) == 1 for attempts in state["attempts"].values())
