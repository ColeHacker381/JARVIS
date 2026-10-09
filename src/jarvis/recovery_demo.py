"""Offline subprocess acceptance harness. All effects are a simulated local ledger."""

import argparse
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from jarvis.authorization import DeterministicPolicy
from jarvis.capabilities import InMemoryCapabilityRegistry
from jarvis.config import Settings
from jarvis.core.contracts import Capability
from jarvis.core.task_records import TaskStep
from jarvis.execution import CapabilityExecutor
from jarvis.identity import InMemoryIdentityService
from jarvis.permissions import InMemoryPermissionStore
from jarvis.storage.sqlite import SQLiteAuditSink, SQLiteDatabase, SQLiteTaskStore
from jarvis.task_manager import TaskManager

PERMISSION = "development.recovery.invoke"
OWNER_ID = uuid5(NAMESPACE_URL, "jarvis.offline.recovery.owner")
FRIEND_ID = uuid5(NAMESPACE_URL, "jarvis.offline.recovery.friend")
CHECKPOINT_CRASH = 73
PROVIDER_CRASH = 74


class SimulatedEffects:
    """A provider that atomically deduplicates stable keys, independent of task checkpoints."""

    def __init__(self, path: Path, *, crash_label: str | None = None) -> None:
        self.path = path
        self.crash_label = crash_label
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS calls(sequence INTEGER PRIMARY KEY, "
                "label TEXT NOT NULL, operation_key TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS effects("
                "operation_key TEXT PRIMARY KEY, label TEXT NOT NULL)"
            )

    def __call__(self, arguments, *, idempotency_key: str):
        with sqlite3.connect(self.path, timeout=5) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO calls(label,operation_key) VALUES(?,?)",
                (arguments["label"], idempotency_key),
            )
            previous = connection.execute(
                "SELECT label FROM effects WHERE operation_key=?", (idempotency_key,)
            ).fetchone()
            if previous is not None and previous[0] != arguments["label"]:
                raise ValueError("idempotency_payload_conflict")
            connection.execute(
                "INSERT OR IGNORE INTO effects VALUES(?,?)", (idempotency_key, arguments["label"])
            )
        if arguments["label"] == self.crash_label:
            # Acknowledged provider effect, before executor audit and task result checkpoint.
            os._exit(PROVIDER_CRASH)
        return {"label": arguments["label"]}

    def non_idempotent(self, arguments):
        return self(arguments, idempotency_key=f"non-idempotent:{uuid4()}")

    def counts(self):
        with sqlite3.connect(self.path) as connection:
            calls = Counter(row[0] for row in connection.execute("SELECT label FROM calls"))
            effects = Counter(row[0] for row in connection.execute("SELECT label FROM effects"))
        return dict(calls), dict(effects)


def _runtime(directory: Path, *, crash_label: str | None = None):
    database = SQLiteDatabase(directory / "journal.sqlite3")
    database.initialize()
    audit = SQLiteAuditSink(database)
    identities = InMemoryIdentityService()
    # Fixed, explicitly trusted offline fixture IDs; never restore credentials/grants from a task.
    owner = identities.bootstrap_owner_for_testing(principal_id=OWNER_ID)
    friend = identities.create_principal_for_testing(
        "Trusted development friend", principal_id=FRIEND_ID
    )
    owner_session = identities.establish_session_for_testing(owner.principal_id)
    friend_session = identities.establish_session_for_testing(friend.principal_id)
    permissions = InMemoryPermissionStore(identities, audit)
    permissions.grant(owner_session.token, friend.principal_id, PERMISSION, correlation_id=uuid4())
    registry = InMemoryCapabilityRegistry()
    effects = SimulatedEffects(directory / "simulated-effects.sqlite3", crash_label=crash_label)
    for cap_id, provider, idempotent in (
        ("development.recovery.safe", effects, True),
        ("development.recovery.unsafe", effects.non_idempotent, False),
    ):
        registry.register(
            Capability(
                capability_id=cap_id,
                input_schema={
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "enum": [
                                "first",
                                "second",
                                "third",
                                "ambiguous",
                                "revoked-first",
                                "revoked-second",
                            ],
                        }
                    },
                    "required": ["label"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {"label": {"type": "string"}},
                    "required": ["label"],
                    "additionalProperties": False,
                },
                required_permissions=(PERMISSION,),
                side_effect="local",
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
    return manager, store, permissions, owner_session, friend_session, effects, audit


def _steps(labels: tuple[str, ...], *, unsafe: bool = False):
    return tuple(
        TaskStep(
            step_id=label,
            capability_id="development.recovery.unsafe" if unsafe else "development.recovery.safe",
            arguments={"label": label},
            depends_on=() if index == 0 else (labels[index - 1],),
        )
        for index, label in enumerate(labels)
    )


def _worker(directory: Path, scenario: str, stage: str) -> None:
    crash_label = (
        "second"
        if stage == "provider-window"
        else "ambiguous"
        if scenario == "ambiguous" and stage == "start"
        else None
    )
    manager, store, permissions, owner_session, credentials, _, _ = _runtime(
        directory, crash_label=crash_label
    )
    if stage == "start":
        labels = (
            ("first", "second", "third")
            if scenario == "safe"
            else ("ambiguous",)
            if scenario == "ambiguous"
            else ("revoked-first", "revoked-second")
        )
        task = manager.submit(
            _steps(labels, unsafe=scenario == "ambiguous"),
            session_token=credentials.token,
            idempotency_key=f"demo:{scenario}",
        )
    else:
        with store.database.transaction() as connection:
            row = connection.execute(
                "SELECT task_id FROM tasks WHERE idempotency_key=?", (f"demo:{scenario}",)
            ).fetchone()
        from uuid import UUID

        task = store.get(UUID(row[0]))
    if scenario == "revoked" and stage == "recover":
        permissions.revoke(
            owner_session.token, FRIEND_ID, PERMISSION, correlation_id=task.correlation_id
        )

    def interrupt(checkpoint):
        if (
            stage == "start"
            and scenario in {"safe", "revoked"}
            and checkpoint.steps[0].state == "COMPLETED"
        ):
            os._exit(CHECKPOINT_CRASH)

    result = manager.run(
        task.task_id,
        session_token=credentials.token,
        recovery=stage != "start",
        after_checkpoint=interrupt,
    )
    if result.status != "ok":
        raise RuntimeError("recovery_demo_worker_failed")


def run_recovery_demo(*, directory: Path | None = None) -> dict[str, object]:
    if directory is None:
        settings = Settings.from_environment()
        directory = settings.resolved_database_path.parent / f"recovery-demo-{uuid4()}"
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    # Do not repurpose/reset an existing database. Each acceptance run gets a fresh directory.
    stages = (
        ("safe", "start", CHECKPOINT_CRASH),
        ("safe", "provider-window", PROVIDER_CRASH),
        ("safe", "recover", 0),
        ("ambiguous", "start", PROVIDER_CRASH),
        ("ambiguous", "recover", 0),
        ("revoked", "start", CHECKPOINT_CRASH),
        ("revoked", "recover", 0),
    )
    process_ids = []
    for scenario, stage, expected in stages:
        process = subprocess.Popen(
            [sys.executable, "-m", "jarvis.recovery_demo", str(directory), scenario, stage],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            _, _ = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise RuntimeError("recovery_demo_worker_timeout") from None
        process_ids.append(process.pid)
        if process.returncode != expected:
            raise RuntimeError("recovery_demo_unexpected_exit")
    database = SQLiteDatabase(directory / "journal.sqlite3")
    store = SQLiteTaskStore(database)
    with database.transaction() as connection:
        from uuid import UUID

        tasks = {
            row["idempotency_key"].split(":")[1]: store.get(UUID(row["task_id"]))
            for row in connection.execute("SELECT task_id,idempotency_key FROM tasks")
        }
    calls, effects = SimulatedEffects(directory / "simulated-effects.sqlite3").counts()
    if (
        {name: task.state for name, task in tasks.items()}
        != {"safe": "COMPLETED", "ambiguous": "NEEDS_REVIEW", "revoked": "FAILED"}
        or calls != {"first": 1, "second": 2, "third": 1, "ambiguous": 1, "revoked-first": 1}
        or effects != {"first": 1, "second": 1, "third": 1, "ambiguous": 1, "revoked-first": 1}
    ):
        raise RuntimeError("recovery_demo_acceptance_failed")
    audit = SQLiteAuditSink(database)
    return {
        "status": "ok",
        "database_path": str(database.path),
        "process_ids": process_ids,
        "tasks": {
            name: {
                "task_id": str(task.task_id),
                "state": task.state,
                "completed_steps": sum(step.state == "COMPLETED" for step in task.steps),
                "attempt_counts": {step.step_id: len(step.attempts) for step in task.steps},
                "audit_outcomes": [
                    {
                        "action": record.action,
                        "outcome": record.outcome,
                        "reason_code": record.reason_code,
                    }
                    for record in audit.records(correlation_id=task.correlation_id)
                ],
            }
            for name, task in tasks.items()
        },
        "provider_calls": calls,
        "simulated_effects": effects,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Internal offline crash fixture")
    parser.add_argument("directory", type=Path)
    parser.add_argument("scenario", choices=["safe", "ambiguous", "revoked"])
    parser.add_argument("stage", choices=["start", "provider-window", "recover"])
    args = parser.parse_args()
    _worker(args.directory, args.scenario, args.stage)
