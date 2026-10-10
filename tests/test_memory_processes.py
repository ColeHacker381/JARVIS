import json
import selectors
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

WORKER = Path(__file__).parent / "helpers" / "memory_worker.py"


def call(request):
    result = subprocess.run(
        [sys.executable, str(WORKER)],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, "memory fixture failed"
    return json.loads(result.stdout)


@pytest.fixture
def authority(tmp_path):
    path = str(tmp_path / "journal.sqlite3")
    seed = call({"action": "init", "path": path})
    return {"path": path, **seed}


def test_separate_process_persistence_provenance_and_authorized_current_history(authority):
    first = call({**authority, "action": "derived"})["record"]
    second = call({**authority, "action": "get", "memory_id": first["memory_id"]})["record"]
    assert first == second
    assert second["source"]["category"] == "derived"
    assert second["source"]["derived_from"] == ["evidence:unverified"]
    assert second["confidence"] == 0.2
    for action in ("get", "search"):
        result = call(
            {
                **authority,
                "action": action,
                "memory_id": authority["memories"]["old_fact"],
                "query": {"project_ref": "project:slipstream"},
            }
        )
        if action == "get":
            assert result["error"]["code"] == "memory_unavailable"
        else:
            assert [r["memory_id"] for r in result["result"]["records"]] == [
                authority["memories"]["new_fact"]
            ]
    history = call(
        {
            **authority,
            "action": "search",
            "query": {"project_ref": "project:slipstream", "historical": True},
        }
    )
    assert len(history["result"]["records"]) == 2
    assert history["result"]["records"][0]["supersedes"] == authority["memories"]["old_fact"]


@pytest.mark.parametrize("claim", ["principal", "session_json"])
def test_new_process_rejects_forged_identity_and_hides_private_search(authority, claim):
    token = (
        authority["principals"]["owner"]
        if claim == "principal"
        else json.dumps(
            {
                "principal_id": authority["principals"]["owner"],
                "kind": "owner",
                "authentication_ref": "trusted",
            }
        )
    )
    result = call({**authority, "action": "search", "token": token})
    assert result["error"]["code"] == "access_denied" and "result" not in result


def test_revoked_global_access_cannot_reappear_on_restart(authority):
    request = {
        **authority,
        "memory_id": authority["memories"]["shared"],
        "token": authority["tokens"]["friend"],
    }
    assert call({**request, "action": "get"})["status"] == "ok"
    assert call({**authority, "action": "revoke_grant"})["status"] == "ok"
    for action in ("get", "search"):
        result = call({**request, "action": action})
        assert result["error"]["code"] == "access_denied"
        assert "record" not in result and "result" not in result
    assert call({**request, "action": "get"})["error"]["code"] == "access_denied"
    audits = call({**authority, "action": "audit"})["audits"]
    assert any(a["action"] == "permission.revoke" and a["outcome"] == "succeeded" for a in audits)
    assert any(a["action"] == "memory.get" and a["outcome"] == "denied" for a in audits)
    assert all(token not in json.dumps(audits) for token in authority["tokens"].values())


def test_recipient_revocation_survives_restart_without_deleting_memory(authority):
    memory_id = authority["memories"]["shared"]
    assert call({**authority, "action": "revoke_access", "memory_id": memory_id})["status"] == "ok"
    result = call(
        {
            **authority,
            "action": "get",
            "memory_id": memory_id,
            "token": authority["tokens"]["friend"],
        }
    )
    assert result["error"]["code"] == "memory_unavailable"
    search = call(
        {
            **authority,
            "action": "search",
            "token": authority["tokens"]["friend"],
            "query": {"text": "Shared project"},
        }
    )["result"]
    assert search["records"] == [] and search["next_cursor"] is None
    assert call({**authority, "action": "get", "memory_id": memory_id})["record"]["revision"] == 1


@pytest.mark.parametrize("action", ["delete", "retention"])
def test_deletion_and_expiry_remain_hidden_in_new_process_history(authority, action):
    assert call({**authority, "action": action, "expire": True})["status"] == "ok"
    assert (
        call({**authority, "action": "get", "historical": True})["error"]["code"]
        == "memory_unavailable"
    )
    result = call(
        {**authority, "action": "search", "query": {"text": "Owner tea", "historical": True}}
    )["result"]
    assert result["records"] == [] and result["next_cursor"] is None


@pytest.mark.parametrize("damage", ["missing_db", "missing_authority", "corrupt_owner"])
def test_new_memory_process_fails_closed_when_trusted_security_state_is_lost(authority, damage):
    if damage == "missing_db":
        Path(authority["path"]).unlink()
    else:
        with sqlite3.connect(authority["path"]) as connection:
            if damage == "missing_authority":
                connection.execute("DELETE FROM security_authority")
            else:
                connection.execute(
                    "UPDATE security_principals SET data='{corrupt' WHERE kind='owner'"
                )
    result = call({**authority, "action": "get"})
    assert result["status"] == "error" and "record" not in result
    assert result["error"]["code"] in ("access_denied", "audit_unavailable")
    if damage == "missing_db":
        assert not Path(authority["path"]).exists()


def test_two_synchronized_process_updates_cannot_overwrite_the_same_revision(authority):
    processes = []
    try:
        for i in range(2):
            process = subprocess.Popen(
                [sys.executable, str(WORKER)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            processes.append(process)
            request = {
                **authority,
                "action": "retention",
                "barrier": True,
                "retention_ref": f"retention.worker.{i}",
                "revision": 0,
            }
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
        ready = []
        for process in processes:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                assert selector.select(20), "worker did not reach barrier"
                ready.append(json.loads(process.stdout.readline()))
        assert all(r["event"] == "ready" for r in ready) and ready[0]["pid"] != ready[1]["pid"]
        for process in processes:
            process.stdin.write("GO\n")
            process.stdin.flush()
        outcomes = []
        for process in processes:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                assert selector.select(20), "worker did not complete"
                outcomes.append(json.loads(process.stdout.readline()))
            assert process.wait(timeout=20) == 0
        assert sorted(r["status"] for r in outcomes) == ["error", "ok"]
        loser = next(r for r in outcomes if r["status"] == "error")
        assert loser["error"]["code"] == "revision_conflict"
        winner = next(i for i, r in enumerate(outcomes) if r["status"] == "ok")
        record = call({**authority, "action": "get"})["record"]
        assert record["revision"] == 1 and record["retention_ref"] == f"retention.worker.{winner}"
        assert record["content"] == {"text": "Owner tea"}
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=20)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


def test_cli_demo_runs_real_workers_and_preserves_ordinary_database(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    ordinary = tmp_path / "ordinary.sqlite3"
    monkeypatch.setenv("JARVIS_DATABASE_PATH", str(ordinary))

    def cli(*args):
        result = subprocess.run(
            [sys.executable, "-m", "jarvis", *args], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    cli("db-init")
    summary = cli("memory-demo")
    assert summary["status"] == "ok"
    assert len(set(summary["process_ids"])) == 2
    assert summary["before_restart"] == summary["after_restart"]
    assert (
        summary["after_restart"]["cross_user_denied"] and summary["after_restart"]["guest_denied"]
    )
    assert summary["after_restart"]["tombstone_hidden"]
    assert summary["after_restart"]["current_fact_count"] == 1
    assert summary["after_restart"]["historical_fact_count"] == 2
    assert summary["after_restart"]["current_fact"] == {"text": "Slipstream uses microcontroller B"}
    assert summary["database_path"] != str(ordinary)
    assert "tokens" not in summary
    with sqlite3.connect(ordinary) as connection:
        assert connection.execute("SELECT count(*) FROM memories").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM security_principals").fetchone()[0] == 0
