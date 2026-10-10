import json
import subprocess
import sys
from pathlib import Path

from jarvis.recovery_demo import run_recovery_demo
from jarvis.storage.sqlite import SCHEMA_VERSION, SQLiteDatabase, SQLiteTaskStore


def test_real_process_crashes_and_safe_recovery(tmp_path):
    summary = run_recovery_demo(directory=tmp_path / "acceptance")
    assert summary["status"] == "ok"
    assert len(set(summary["process_ids"])) == 7
    assert {name: task["state"] for name, task in summary["tasks"].items()} == {
        "safe": "COMPLETED",
        "ambiguous": "NEEDS_REVIEW",
        "revoked": "FAILED",
    }
    assert summary["tasks"]["safe"]["completed_steps"] == 3
    assert summary["tasks"]["safe"]["attempt_counts"] == {"first": 1, "second": 2, "third": 1}
    assert summary["provider_calls"]["first"] == 1
    assert summary["provider_calls"]["second"] == 2
    assert summary["simulated_effects"]["second"] == 1
    assert summary["provider_calls"]["ambiguous"] == 1
    assert "revoked-second" not in summary["provider_calls"]
    assert any(
        a["outcome"] == "denied" and a["reason_code"] == "permission_missing"
        for a in summary["tasks"]["revoked"]["audit_outcomes"]
    )
    assert any(
        a["action"] == "task.recovery_blocked"
        for a in summary["tasks"]["ambiguous"]["audit_outcomes"]
    )
    assert Path(summary["database_path"]).is_file()
    assert len(SQLiteTaskStore(SQLiteDatabase(summary["database_path"])).all()) == 3


def test_cli_recovery_demo_and_database_operations(tmp_path, monkeypatch):
    database_path = tmp_path / "ordinary.sqlite3"
    monkeypatch.setenv("JARVIS_DATABASE_PATH", str(database_path))

    def cli(*args):
        process = subprocess.run(
            [sys.executable, "-m", "jarvis", *args], text=True, capture_output=True, timeout=30
        )
        assert process.returncode == 0, process.stderr
        return json.loads(process.stdout)

    assert cli("db-init")["schema_version"] == SCHEMA_VERSION
    assert cli("db-init")["status"] == "ok"
    assert cli("db-inspect")["tasks"] == []
    backup = tmp_path / "backup.sqlite3"
    assert cli("db-backup", "--destination", str(backup))["backup_path"] == str(backup)
    assert backup.is_file()
    summary = cli("recovery-demo")
    assert summary["status"] == "ok"
    assert summary["database_path"] != str(database_path)
    assert cli("db-inspect")["tasks"] == []  # ordinary database never reset or repurposed
    inspect_process = subprocess.run(
        [sys.executable, "-m", "jarvis", "db-backup", "--destination", str(backup)],
        text=True,
        capture_output=True,
    )
    assert inspect_process.returncode != 0
    assert backup.is_file()
