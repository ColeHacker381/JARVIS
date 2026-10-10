import hashlib
import secrets
import sqlite3
from uuid import uuid4

import pytest

from jarvis.core.contracts import (
    AccessScope,
    AuditRecord,
    Event,
    Memory,
    Principal,
    Provenance,
    Session,
)
from jarvis.core.task_records import TaskRecord, TaskStep
from jarvis.core.time import utc_now
from jarvis.memory import MemoryService
from jarvis.storage.errors import StorageError
from jarvis.storage.memory import SQLiteMemoryStore
from jarvis.storage.security import (
    GrantRecord,
    SQLiteDevelopmentIdentityService,
    SQLitePermissionStore,
)
from jarvis.storage.sqlite import (
    MIGRATIONS,
    SCHEMA_VERSION,
    SQLiteAuditSink,
    SQLiteDatabase,
    SQLiteEventStore,
    SQLiteTaskStore,
    _append_audit,
    _append_event,
    _plan_hash,
)


def test_real_schema_two_migration_preserves_principals_sessions_grants_tasks_events_and_audits(
    tmp_path,
):
    path = tmp_path / "old.sqlite3"
    owner = Principal(display_name="Owner", kind="owner")
    friend = Principal(display_name="Friend")
    session = Session(
        principal_id=friend.principal_id,
        interface="development",
        authentication_ref="development.sqlite_fixture",
    )
    token = secrets.token_urlsafe(32)
    grant = GrantRecord(
        grant_id=uuid4(),
        principal_id=friend.principal_id,
        permission="memory.read",
        granted_by=owner.principal_id,
        granted_at=utc_now(),
    )
    event = Event(
        event_type="test.old_event",
        correlation_id=uuid4(),
        source="test",
        payload={"preserved": True},
    )
    audit = AuditRecord(
        principal_id=owner.principal_id,
        correlation_id=uuid4(),
        action="permission.grant",
        outcome="succeeded",
        reason_code="permission_granted",
    )
    task = TaskRecord(
        owner_id=friend.principal_id,
        correlation_id=uuid4(),
        authority_refs=(f"principal:{friend.principal_id}",),
        steps=(TaskStep(step_id="first", capability_id="test.fake"),),
    )
    with sqlite3.connect(path) as connection:
        for version in (1, 2):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute("PRAGMA user_version=2")
        for principal in (owner, friend):
            connection.execute(
                "INSERT INTO security_principals VALUES(?,?,?,?)",
                (
                    str(principal.principal_id),
                    principal.kind,
                    principal.status,
                    principal.model_dump_json(),
                ),
            )
        connection.execute(
            "INSERT INTO security_authority VALUES(1,1,?,?,'development')",
            (str(uuid4()), str(owner.principal_id)),
        )
        connection.execute(
            "INSERT INTO security_sessions VALUES(?,?,?)",
            (str(session.session_id), str(friend.principal_id), session.model_dump_json()),
        )
        connection.execute(
            "INSERT INTO development_session_tokens VALUES(?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), str(session.session_id)),
        )
        connection.execute(
            "INSERT INTO security_grants VALUES(?,?,?,?,?,?)",
            (
                str(grant.grant_id),
                str(friend.principal_id),
                grant.permission,
                str(owner.principal_id),
                None,
                grant.model_dump_json(),
            ),
        )
        connection.execute(
            "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)",
            (
                str(task.task_id),
                str(friend.principal_id),
                str(task.correlation_id),
                0,
                task.state,
                None,
                _plan_hash(task),
                task.model_dump_json(),
            ),
        )
        connection.execute(
            "INSERT INTO task_history(task_id,revision,recorded_at,data) VALUES(?,?,?,?)",
            (str(task.task_id), 0, utc_now().isoformat(), task.model_dump_json()),
        )
        _append_event(connection, event)
        _append_audit(connection, audit)
        tables = (
            "security_principals",
            "security_authority",
            "security_sessions",
            "development_session_tokens",
            "security_grants",
            "events",
            "audits",
            "tasks",
            "task_history",
        )
        before = {
            table: connection.execute(f"SELECT * FROM {table}").fetchall() for table in tables
        }
    database = SQLiteDatabase(path)
    database.initialize()
    database.initialize()
    with database.transaction() as connection:
        for table in tables:
            assert [tuple(r) for r in connection.execute(f"SELECT * FROM {table}")] == before[table]
        assert connection.execute("SELECT count(*) FROM memories").fetchone()[0] == 0
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 3
    identities = SQLiteDevelopmentIdentityService(database)
    sink = SQLiteAuditSink(database)
    permissions = SQLitePermissionStore(identities, sink)
    assert identities.resolve_session(token) == session
    assert identities.owner_id == owner.principal_id
    assert (
        permissions.current_grants(friend.principal_id, ("memory.read",))[0].grant_id
        == grant.grant_id
    )
    assert SQLiteEventStore(database).get(event.event_id) == event
    assert sink.records() == (audit,)
    assert SQLiteTaskStore(database).get(task.task_id) == task
    assert SQLiteTaskStore(database).history(task.task_id) == (task,)
    service = MemoryService(SQLiteMemoryStore(database, identities, sink), permissions)
    from jarvis.core.memory_records import MemoryQuery

    assert service.search(MemoryQuery(), session_token=token).records == ()


def test_memory_v1_remains_decodable_but_is_not_silently_imported_as_v2():
    from jarvis.core.contracts import decode_contract
    from jarvis.core.memory_records import decode_memory

    record = Memory(
        kind="person",
        content={"text": "old"},
        source=Provenance(category="import", source_ref="legacy:test"),
        access_scope=AccessScope(owner_id=uuid4()),
    )
    assert decode_contract(record.model_dump_json()) == record
    with pytest.raises(ValueError, match="version 2"):
        decode_memory(record.model_dump_json())


def test_future_database_version_is_refused_without_rewriting_data(tmp_path):
    database = SQLiteDatabase(tmp_path / "future.sqlite3")
    database.initialize()
    with sqlite3.connect(database.path) as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(StorageError, match="database_version_unsupported"):
        database.initialize()
    with sqlite3.connect(database.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999
