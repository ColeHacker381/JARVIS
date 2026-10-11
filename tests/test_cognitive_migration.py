import sqlite3
from uuid import uuid4

from jarvis.cognitive_runtime import development_runtime
from jarvis.core.memory_records import MemoryQuery
from jarvis.core.task_records import TaskStep
from jarvis.storage.sqlite import MIGRATIONS, SCHEMA_VERSION, SQLiteDatabase


def test_schema3_upgrade_preserves_memory_authority_tasks_and_journals(tmp_path):
    source = development_runtime(tmp_path / "current.sqlite3", bootstrap=True)
    credentials = source.credentials("owner")
    source.tasks.submit(
        (
            TaskStep(
                step_id="first", capability_id="development.echo", arguments={"text": "preserved"}
            ),
        ),
        session_token=credentials.token,
        correlation_id=uuid4(),
    )
    old_path = tmp_path / "schema3.sqlite3"
    with sqlite3.connect(old_path) as old, source.database.transaction() as current:
        old.execute("PRAGMA foreign_keys=ON")
        for version in (1, 2, 3):
            for statement in MIGRATIONS[version]:
                old.execute(statement)
        tables = (
            "security_principals",
            "security_authority",
            "security_sessions",
            "development_session_tokens",
            "security_grants",
            "memories",
            "memory_readers",
            "memory_subjects",
            "events",
            "audits",
            "tasks",
            "task_history",
            "cancellations",
        )
        before = {}
        for table in tables:
            rows = [tuple(row) for row in current.execute(f"SELECT * FROM {table}")]
            before[table] = rows
            if rows:
                placeholders = ",".join("?" for _ in rows[0])
                old.executemany(f"INSERT INTO {table} VALUES({placeholders})", rows)
        old.execute("PRAGMA user_version=3")
    database = SQLiteDatabase(old_path)
    database.initialize()
    database.initialize()
    with database.transaction() as upgraded:
        assert upgraded.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        for table in tables:
            assert [tuple(row) for row in upgraded.execute(f"SELECT * FROM {table}")] == before[
                table
            ]
        assert upgraded.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0
    reopened = development_runtime(old_path)
    assert (
        reopened.identities.resolve_session(credentials.token).principal_id
        == source.identities.owner_id
    )
    memory = reopened.memory.search(MemoryQuery(text="Slipstream"), session_token=credentials.token)
    assert memory.records[0].content["text"] == "Slipstream uses microcontroller B"
    assert reopened.tasks.store.all() == source.tasks.store.all()
