import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from jarvis.config import Settings
from jarvis.core.contracts import Event
from jarvis.storage.errors import StorageError
from jarvis.storage.sqlite import DuplicateEventError, SQLiteDatabase, SQLiteEventStore


@pytest.fixture
def database(tmp_path):
    result = SQLiteDatabase(tmp_path / "journal.sqlite3")
    result.initialize()
    return result


def event(**kwargs):
    return Event(event_type="test.recorded", correlation_id=uuid4(), source="tests", **kwargs)


def test_explicit_initialization_migrations_fk_and_durability(tmp_path):
    database = SQLiteDatabase(tmp_path / "journal.sqlite3")
    with pytest.raises(StorageError):
        SQLiteEventStore(database).append(event())
    database.initialize()
    database.initialize()
    with database.transaction() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    with pytest.raises(StorageError):
        with database.transaction(write=True) as connection:
            connection.execute("INSERT INTO cancellations VALUES(?,?)", (str(uuid4()), "now"))
    with pytest.raises(ValueError):
        SQLiteDatabase(":memory:")
    with pytest.raises(ValueError):
        SQLiteDatabase("src/jarvis/forbidden.sqlite3")


def test_event_append_survives_new_process(database):
    code = """
from uuid import UUID
from jarvis.core.contracts import Event
from jarvis.storage.sqlite import SQLiteDatabase, SQLiteEventStore
import sys
store=SQLiteEventStore(SQLiteDatabase(sys.argv[1]))
store.append(Event(event_id=UUID(sys.argv[2]),event_type='test.recorded',
 correlation_id=UUID(sys.argv[3]),source='subprocess',payload={'nested':{'ok':True}}))
"""
    event_id, correlation = uuid4(), uuid4()
    process = subprocess.run(
        [sys.executable, "-c", code, str(database.path), str(event_id), str(correlation)],
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
    recovered = SQLiteEventStore(SQLiteDatabase(database.path)).get(event_id)
    assert recovered.correlation_id == correlation
    assert recovered.payload == {"nested": {"ok": True}}
    assert recovered.source == "subprocess"


def test_duplicates_are_idempotent_but_conflicts_fail(database):
    store = SQLiteEventStore(database)
    item = event(payload={"a": 1, "b": 2})
    store.append(item)
    store.append(item)
    assert store.read() == (item,)
    with pytest.raises(DuplicateEventError):
        store.append(item.model_copy(update={"payload": {"changed": True}}))
    assert store.read() == (item,)


def test_order_filters_causation_and_utc_recording(database):
    store = SQLiteEventStore(database)
    correlation = uuid4()
    now = datetime.now(UTC)
    first = Event(
        event_type="task.created",
        correlation_id=correlation,
        source="tests",
        occurred_at=now.astimezone(timezone(timedelta(hours=5))),
        principal_id=uuid4(),
        causation_id=uuid4(),
        payload={"structured": [1, None, True]},
    )
    second = Event(
        event_type="task.completed",
        correlation_id=correlation,
        source="tests",
        occurred_at=now - timedelta(days=1),
    )
    third = event()
    for item in (first, second, third):
        store.append(item)
    restored = store.read()
    assert [item.event_id for item in restored] == [first.event_id, second.event_id, third.event_id]
    assert restored[0].occurred_at.utcoffset() == timedelta(0)
    assert restored[0].principal_id == first.principal_id
    assert restored[0].causation_id == first.causation_id
    assert store.by_correlation(correlation) == restored[:2]
    assert store.read(event_type="task.created", correlation_id=correlation) == restored[:1]
    assert store.read(after_sequence=1) == restored[1:]
    assert store.get(uuid4()) is None
    with database.transaction() as connection:
        assert datetime.fromisoformat(
            connection.execute("SELECT recorded_at FROM events LIMIT 1").fetchone()[0]
        ).utcoffset() == timedelta(0)


@pytest.mark.parametrize("version", [2, 0, "1", True, None])
def test_event_versions_fail_closed(database, version):
    store = SQLiteEventStore(database)
    with pytest.raises(ValidationError):
        store.append(event().model_copy(update={"schema_version": version}))
    assert store.read() == ()


@pytest.mark.parametrize("table", ["events", "audits", "task_history"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_append_only_tables(database, table, operation):
    # Statement triggers fire only when a row exists; an event is enough to test the journal.
    if table != "events":
        with database.transaction() as connection:
            assert connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name=?",
                (f"{table}_no_{operation.lower()}",),
            ).fetchone()
        return
    SQLiteEventStore(database).append(event())
    with pytest.raises(StorageError):
        with database.transaction(write=True) as connection:
            connection.execute(
                f"UPDATE {table} SET data='corrupt'"
                if operation == "UPDATE"
                else f"DELETE FROM {table}"
            )
    assert len(SQLiteEventStore(database).read()) == 1


@pytest.mark.parametrize("corruption", ["json", "version", "metadata", "missing_version"])
def test_corrupt_events_are_not_read_as_valid(database, corruption):
    store = SQLiteEventStore(database)
    item = event()
    store.append(item)
    with database.transaction(write=True) as connection:
        # Simulate filesystem-level corruption by removing the development append-only trigger.
        connection.execute("DROP TRIGGER events_no_update")
        body = json.loads(item.model_dump_json())
        if corruption == "version":
            body["schema_version"] = 99
        elif corruption == "missing_version":
            del body["schema_version"]
        elif corruption == "metadata":
            body["correlation_id"] = str(uuid4())
        connection.execute(
            "UPDATE events SET data=?", ("{" if corruption == "json" else json.dumps(body),)
        )
    with pytest.raises(StorageError, match="record_corrupt"):
        store.get(item.event_id)


def test_future_database_version_is_never_migrated(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")
        connection.execute("CREATE TABLE preserve(value TEXT)")
        connection.execute("INSERT INTO preserve VALUES('safe')")
    database = SQLiteDatabase(path)
    with pytest.raises(StorageError, match="database_version_unsupported"):
        database.initialize()
    with pytest.raises(StorageError, match="database_version_unsupported"):
        SQLiteEventStore(database).read()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM preserve").fetchone()[0] == "safe"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_backup_includes_committed_wal_and_refuses_overwrite(database, tmp_path):
    item = event()
    SQLiteEventStore(database).append(item)
    destination = tmp_path / "backup.sqlite3"
    database.backup(destination)
    assert SQLiteEventStore(SQLiteDatabase(destination)).get(item.event_id) == item
    with pytest.raises(StorageError, match="backup_destination_exists"):
        database.backup(destination)
    assert SQLiteEventStore(SQLiteDatabase(destination)).get(item.event_id) == item


def test_settings_database_path(monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", "/tmp/jarvis-data")
    monkeypatch.delenv("JARVIS_DATABASE_PATH", raising=False)
    assert (
        str(Settings.from_environment().resolved_database_path)
        == "/tmp/jarvis-data/journal.sqlite3"
    )
    monkeypatch.setenv("JARVIS_DATABASE_PATH", "/tmp/custom.sqlite3")
    assert str(Settings.from_environment().resolved_database_path) == "/tmp/custom.sqlite3"
    monkeypatch.setenv("JARVIS_DATABASE_PATH", "   ")
    with pytest.raises(ValidationError):
        Settings.from_environment()
