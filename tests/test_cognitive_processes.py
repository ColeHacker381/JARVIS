import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.cognition_demo import run_cognition_demo

WORKER = Path(__file__).parent / "helpers" / "cognition_worker.py"


def worker(mode, path, info):
    return subprocess.run(
        [sys.executable, str(WORKER), mode, str(path)],
        input=json.dumps(info),
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "mode,exit_code,state,calls",
    [
        ("seed-before-link", 75, "COMPLETED", [50]),
        ("seed-before-response", 77, "COMPLETED", []),
        ("seed-ambiguous", 76, "NEEDS_REVIEW", []),
    ],
)
def test_actual_process_crash_preserves_intent_and_never_repeats_unknown_effect(
    tmp_path,
    mode,
    exit_code,
    state,
    calls,
):
    info = {"request_id": str(uuid4())}
    crashed = worker(mode, tmp_path, info)
    assert crashed.returncode == exit_code, crashed.stderr
    info = json.loads((tmp_path / "info.json").read_text())
    reopened = worker("reopen", tmp_path, info)
    assert reopened.returncode == 0, reopened.stderr
    result = json.loads(reopened.stdout)
    assert result["response"]["task_state"] == state
    assert result["lighting_calls"] == calls
    assert result["pid"] != os.getpid()
    if state == "NEEDS_REVIEW":
        with sqlite3.connect(tmp_path / "effects.sqlite3") as connection:
            assert connection.execute("SELECT brightness FROM effects").fetchall() == [(50,)]
    replayed = worker("reopen", tmp_path, info)
    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout)["lighting_calls"] == []
    assert json.loads(replayed.stdout)["response"] == result["response"]


def test_offline_demo_real_processes_and_continuity(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    demo = run_cognition_demo()
    assert demo["status"] == "ok" and len(set(demo["process_ids"])) == 2
    assert demo["before_restart"]["lighting_calls"] == [50]
    assert demo["after_restart"]["lighting_calls_on_replay"] == []
    assert demo["before_restart"]["guest_visible_memories"] == 0
    assert demo["results"]["guest_action"]["execution"]["status"] == "denied"
    assert demo["after_restart"]["history_turns_after"] == 3
