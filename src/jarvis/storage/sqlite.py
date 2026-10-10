"""Provisional local SQLite journal, atomic task checkpoints, and payload-free audit."""

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC
from pathlib import Path
from uuid import UUID

from jarvis.core.contracts import AuditRecord, Event, decode_contract
from jarvis.core.task_records import TERMINAL_STATES, TRANSITIONS, TaskRecord, decode_task
from jarvis.core.time import utc_now
from jarvis.identity import IdentityError
from jarvis.storage.errors import StorageError
from jarvis.tasks import TaskBusyError, TaskConflictError

SCHEMA_VERSION = 3


class DuplicateEventError(StorageError):
    def __init__(self) -> None:
        super().__init__("event_id_conflict")


class IdempotencyConflictError(TaskConflictError):
    pass


MIGRATIONS = {
    1: (
        """CREATE TABLE events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL, schema_version INTEGER NOT NULL,
            principal_id TEXT, occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
            correlation_id TEXT NOT NULL, causation_id TEXT, source TEXT NOT NULL,
            data TEXT NOT NULL)""",
        "CREATE INDEX events_correlation ON events(correlation_id, sequence)",
        "CREATE INDEX events_type ON events(event_type, sequence)",
        """CREATE TABLE audits (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, audit_id TEXT NOT NULL UNIQUE,
            correlation_id TEXT NOT NULL, task_id TEXT, recorded_at TEXT NOT NULL,
            data TEXT NOT NULL)""",
        "CREATE INDEX audits_correlation ON audits(correlation_id, sequence)",
        """CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, correlation_id TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision >= 0), state TEXT NOT NULL,
            idempotency_key TEXT, plan_hash TEXT NOT NULL, data TEXT NOT NULL,
            UNIQUE(owner_id, idempotency_key))""",
        """CREATE TABLE task_history (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL REFERENCES tasks(task_id), revision INTEGER NOT NULL,
            recorded_at TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(task_id, revision))""",
        """CREATE TABLE cancellations (
            task_id TEXT PRIMARY KEY REFERENCES tasks(task_id), requested_at TEXT NOT NULL)""",
        *(
            f"""CREATE TRIGGER {table}_no_{op.lower()} BEFORE {op} ON {table}
           BEGIN SELECT RAISE(ABORT, 'append_only'); END"""
            for table in ("events", "audits", "task_history")
            for op in ("UPDATE", "DELETE")
        ),
    ),
    2: (
        """CREATE TABLE security_principals (
            principal_id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
            data TEXT NOT NULL)""",
        """CREATE TABLE security_authority (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), schema_version INTEGER NOT NULL,
            authority_id TEXT NOT NULL UNIQUE, owner_id TEXT NOT NULL
            REFERENCES security_principals(principal_id), mode TEXT NOT NULL)""",
        """CREATE TABLE security_sessions (
            session_id TEXT PRIMARY KEY,
            principal_id TEXT REFERENCES security_principals(principal_id),
            data TEXT NOT NULL)""",
        """CREATE TABLE development_session_tokens (
            token_hash TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE
            REFERENCES security_sessions(session_id))""",
        """CREATE TABLE security_grants (
            grant_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL
            REFERENCES security_principals(principal_id), permission TEXT NOT NULL,
            granted_by TEXT NOT NULL REFERENCES security_principals(principal_id),
            revoked_at TEXT, data TEXT NOT NULL)""",
        """CREATE UNIQUE INDEX security_active_grant ON security_grants(principal_id,permission)
            WHERE revoked_at IS NULL""",
        """CREATE TRIGGER security_grants_no_delete BEFORE DELETE ON security_grants
            BEGIN SELECT RAISE(ABORT, 'retain_revocation_history'); END""",
        """CREATE TRIGGER security_grants_no_resurrection BEFORE UPDATE ON security_grants
            WHEN OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL
              OR NEW.grant_id != OLD.grant_id OR NEW.principal_id != OLD.principal_id
              OR NEW.permission != OLD.permission OR NEW.granted_by != OLD.granted_by
            BEGIN SELECT RAISE(ABORT, 'immutable_grant_or_revocation'); END""",
    ),
    3: (
        """CREATE TABLE memories (
            memory_id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision >= 0),
            owner_id TEXT NOT NULL REFERENCES security_principals(principal_id),
            creator_id TEXT NOT NULL REFERENCES security_principals(principal_id),
            scope_kind TEXT NOT NULL, kind TEXT NOT NULL, project_ref TEXT,
            created_at TEXT NOT NULL, valid_from TEXT, valid_to TEXT, expires_at TEXT,
            superseded_at TEXT, deletion_state TEXT NOT NULL, search_text TEXT NOT NULL,
            data TEXT NOT NULL)""",
        "CREATE INDEX memories_owner_recent ON memories(owner_id,created_at DESC,memory_id DESC)",
        "CREATE INDEX memories_kind_recent ON memories(kind,created_at DESC,memory_id DESC)",
        "CREATE INDEX memories_project ON memories(project_ref,created_at DESC,memory_id DESC)",
        """CREATE TABLE memory_readers (
            memory_id TEXT NOT NULL REFERENCES memories(memory_id), principal_id TEXT NOT NULL
            REFERENCES security_principals(principal_id), PRIMARY KEY(memory_id,principal_id))""",
        "CREATE INDEX memory_reader_access ON memory_readers(principal_id,memory_id)",
        """CREATE TABLE memory_subjects (
            memory_id TEXT NOT NULL REFERENCES memories(memory_id), subject_id TEXT NOT NULL,
            PRIMARY KEY(memory_id,subject_id))""",
        "CREATE INDEX memory_subject_filter ON memory_subjects(subject_id,memory_id)",
        """CREATE TRIGGER memories_no_restore BEFORE UPDATE ON memories
            WHEN OLD.deletion_state='tombstoned' OR NEW.revision != OLD.revision+1
              OR NEW.memory_id != OLD.memory_id OR NEW.owner_id != OLD.owner_id
              OR NEW.creator_id != OLD.creator_id OR NEW.created_at != OLD.created_at
            BEGIN SELECT RAISE(ABORT, 'immutable_memory_or_tombstone'); END""",
    ),
}


@contextmanager
def _file_lock(path: Path, *, blocking: bool = False) -> Iterator[None]:
    """Crash-released local exclusion; nonblocking by default. Never use on network filesystems."""
    import fcntl

    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        raise StorageError() from None
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise TaskBusyError("task_busy") from None
        except OSError:
            raise StorageError() from None
        yield
    finally:
        os.close(descriptor)


class SQLiteDatabase:
    def __init__(self, path: Path | str) -> None:
        if str(path) == ":memory:":
            raise ValueError("durability requires a file database")
        self.path = Path(path).expanduser().resolve()
        source_dir = Path(__file__).resolve().parents[2]
        if self.path.is_relative_to(source_dir):
            raise ValueError("database must be outside source directories")

    def _open(self) -> sqlite3.Connection:
        connection = None
        try:
            if self.path.stat().st_nlink != 1:
                raise StorageError("database_alias_unsupported")
            connection = sqlite3.connect(f"{self.path.as_uri()}?mode=rw", uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA busy_timeout = 5000")
            return connection
        except (sqlite3.Error, OSError):
            if connection is not None:
                connection.close()
            raise StorageError() from None

    def initialize(self) -> None:
        """Explicit, idempotent migrations; refuse future versions without touching data."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with _file_lock(self.path.with_suffix(self.path.suffix + ".schema.lock")):
                try:
                    fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError:
                    pass
                else:
                    os.close(fd)
                with self._open() as connection:
                    version = connection.execute("PRAGMA user_version").fetchone()[0]
                    if version < 0 or version > SCHEMA_VERSION:
                        raise StorageError("database_version_unsupported")
                    connection.execute("PRAGMA journal_mode = WAL")
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        for target in range(version + 1, SCHEMA_VERSION + 1):
                            for statement in MIGRATIONS[target]:
                                connection.execute(statement)
                            connection.execute(f"PRAGMA user_version = {target}")
                        connection.commit()
                    except BaseException:
                        connection.rollback()
                        raise
        except (sqlite3.Error, OSError):
            raise StorageError() from None
        finally:
            # sqlite3's context manager commits/rolls back but does not close.
            if "connection" in locals():
                connection.close()

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._open()
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise StorageError(
                    "database_version_unsupported" if version else "database_uninitialized"
                )
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException as exc:
            connection.rollback()
            if isinstance(exc, (sqlite3.Error, OSError)) and not isinstance(exc, IdentityError):
                raise StorageError() from None
            raise
        finally:
            connection.close()

    def backup(self, destination: Path) -> None:
        destination = destination.expanduser().resolve()
        if destination.exists():
            raise StorageError("backup_destination_exists")
        source = self._open()
        target = None
        try:
            fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            target = sqlite3.connect(destination)
            source.backup(target)
        except (sqlite3.Error, OSError):
            raise StorageError("backup_failed") from None
        finally:
            source.close()
            if target is not None:
                target.close()


def _event_data(event: Event) -> str:
    event = Event.model_validate(event.model_dump())
    event = event.model_copy(update={"occurred_at": event.occurred_at.astimezone(UTC)})
    return json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _append_event(connection: sqlite3.Connection, event: Event) -> None:
    data = _event_data(event)
    existing = connection.execute(
        "SELECT data FROM events WHERE event_id=?", (str(event.event_id),)
    ).fetchone()
    if existing is not None:
        if existing["data"] != data:
            raise DuplicateEventError()
        return
    normalized = Event.model_validate_json(data)
    connection.execute(
        "INSERT INTO events(event_id,event_type,schema_version,principal_id,occurred_at,"
        "recorded_at,correlation_id,causation_id,source,data) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            str(event.event_id),
            event.event_type,
            event.schema_version,
            str(event.principal_id) if event.principal_id else None,
            normalized.occurred_at.isoformat(),
            utc_now().isoformat(),
            str(event.correlation_id),
            str(event.causation_id) if event.causation_id else None,
            event.source,
            data,
        ),
    )


def _decode_event(row: sqlite3.Row) -> Event:
    try:
        event = decode_contract(row["data"])
        if not isinstance(event, Event) or (
            str(event.event_id),
            event.event_type,
            event.schema_version,
            str(event.principal_id) if event.principal_id else None,
            event.occurred_at.isoformat(),
            str(event.correlation_id),
            str(event.causation_id) if event.causation_id else None,
            event.source,
        ) != tuple(
            row[key]
            for key in (
                "event_id",
                "event_type",
                "schema_version",
                "principal_id",
                "occurred_at",
                "correlation_id",
                "causation_id",
                "source",
            )
        ):
            raise ValueError("event metadata mismatch")
        if event.occurred_at.utcoffset().total_seconds() != 0:
            raise ValueError("non-UTC stored event")
        from datetime import datetime

        recorded = datetime.fromisoformat(row["recorded_at"])
        if recorded.utcoffset() is None or recorded.utcoffset().total_seconds() != 0:
            raise ValueError("non-UTC recording timestamp")
        return event
    except (ValueError, TypeError, KeyError, AttributeError):
        raise StorageError("record_corrupt") from None


class SQLiteEventStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def append(self, event: Event) -> None:
        with self.database.transaction(write=True) as connection:
            _append_event(connection, event)

    def get(self, event_id: UUID) -> Event | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM events WHERE event_id=?", (str(event_id),)
            ).fetchone()
            return _decode_event(row) if row is not None else None

    def read(
        self,
        *,
        event_type: str | None = None,
        correlation_id: UUID | None = None,
        after_sequence: int = 0,
    ) -> tuple[Event, ...]:
        conditions = ["sequence > ?"]
        parameters: list[object] = [after_sequence]
        for field, value in (("event_type", event_type), ("correlation_id", correlation_id)):
            if value is not None:
                conditions.append(f"{field}=?")
                parameters.append(str(value))
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE " + " AND ".join(conditions) + " ORDER BY sequence",
                parameters,
            ).fetchall()
            return tuple(_decode_event(row) for row in rows)

    def by_correlation(self, correlation_id: UUID) -> tuple[Event, ...]:
        return self.read(correlation_id=correlation_id)


def _append_audit(connection: sqlite3.Connection, record: AuditRecord) -> None:
    record = AuditRecord.model_validate(record.model_dump())
    connection.execute(
        "INSERT INTO audits(audit_id,correlation_id,task_id,recorded_at,data) VALUES(?,?,?,?,?)",
        (
            str(record.audit_id),
            str(record.correlation_id),
            str(record.task_id) if record.task_id else None,
            utc_now().isoformat(),
            record.model_dump_json(),
        ),
    )


class SQLiteAuditSink:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def append(self, record: AuditRecord) -> None:
        with self.database.transaction(write=True) as connection:
            _append_audit(connection, record)

    def records(self, *, correlation_id: UUID | None = None) -> tuple[AuditRecord, ...]:
        with self.database.transaction() as connection:
            query = "SELECT * FROM audits"
            params = ()
            if correlation_id is not None:
                query += " WHERE correlation_id=?"
                params = (str(correlation_id),)
            rows = connection.execute(query + " ORDER BY sequence", params).fetchall()
            records = []
            for row in rows:
                try:
                    record = decode_contract(row["data"])
                    if not isinstance(record, AuditRecord) or (
                        str(record.audit_id),
                        str(record.correlation_id),
                        str(record.task_id) if record.task_id else None,
                    ) != (row["audit_id"], row["correlation_id"], row["task_id"]):
                        raise ValueError("audit metadata mismatch")
                    records.append(record)
                except (ValueError, TypeError):
                    raise StorageError("record_corrupt") from None
            return tuple(records)


def _plan_hash(task: TaskRecord) -> str:
    plan = [
        {
            key: step.model_dump(mode="json")[key]
            for key in ("step_id", "capability_id", "arguments", "depends_on")
        }
        for step in task.steps
    ]
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()


def _decode_task_row(row: sqlite3.Row) -> TaskRecord:
    try:
        task = decode_task(row["data"])
        if (
            str(task.task_id),
            str(task.owner_id),
            str(task.correlation_id),
            task.revision,
            task.state,
            task.idempotency_key,
            _plan_hash(task),
        ) != tuple(
            row[key]
            for key in (
                "task_id",
                "owner_id",
                "correlation_id",
                "revision",
                "state",
                "idempotency_key",
                "plan_hash",
            )
        ):
            raise ValueError("task metadata mismatch")
        return task
    except (ValueError, TypeError, KeyError, AttributeError):
        raise StorageError("record_corrupt") from None


class SQLiteTaskStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def create(self, task: TaskRecord, event: Event, audit: AuditRecord) -> TaskRecord:
        task = TaskRecord.model_validate(task.model_dump())
        if task.state != "CREATED" or task.revision != 0:
            raise ValueError("new tasks must be CREATED at revision zero")
        with self.database.transaction(write=True) as connection:
            if task.idempotency_key is not None:
                existing = connection.execute(
                    "SELECT * FROM tasks WHERE owner_id=? AND idempotency_key=?",
                    (str(task.owner_id), task.idempotency_key),
                ).fetchone()
                if existing is not None:
                    previous = _decode_task_row(existing)
                    if _plan_hash(previous) != _plan_hash(task):
                        raise IdempotencyConflictError("idempotency_key_plan_conflict")
                    return previous
            connection.execute(
                "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(task.task_id),
                    str(task.owner_id),
                    str(task.correlation_id),
                    task.revision,
                    task.state,
                    task.idempotency_key,
                    _plan_hash(task),
                    task.model_dump_json(),
                ),
            )
            self._checkpoint(connection, task, event, audit)
        return task

    @staticmethod
    def _checkpoint(
        connection: sqlite3.Connection, task: TaskRecord, event: Event, audit: AuditRecord
    ) -> None:
        if (
            event.correlation_id != task.correlation_id
            or audit.correlation_id != task.correlation_id
            or audit.task_id != task.task_id
            or event.payload.get("task_id") != str(task.task_id)
        ):
            raise ValueError("checkpoint correlation mismatch")
        connection.execute(
            "INSERT INTO task_history(task_id,revision,recorded_at,data) VALUES(?,?,?,?)",
            (str(task.task_id), task.revision, utc_now().isoformat(), task.model_dump_json()),
        )
        _append_event(connection, event)
        _append_audit(connection, audit)

    def save(
        self, task: TaskRecord, *, expected_revision: int, event: Event, audit: AuditRecord
    ) -> None:
        task = TaskRecord.model_validate(task.model_dump())
        with self.database.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id=?", (str(task.task_id),)
            ).fetchone()
            if row is None or row["revision"] != expected_revision:
                raise TaskConflictError("task_revision_conflict")
            old = _decode_task_row(row)
            if task.revision != old.revision + 1 or task.state not in TRANSITIONS[old.state]:
                raise ValueError("invalid checkpoint transition")
            if (
                task.owner_id,
                task.correlation_id,
                task.idempotency_key,
                task.created_at,
                _plan_hash(task),
            ) != (
                old.owner_id,
                old.correlation_id,
                old.idempotency_key,
                old.created_at,
                _plan_hash(old),
            ):
                raise ValueError("immutable task identity/plan changed")
            for previous, updated in zip(old.steps, task.steps, strict=True):
                if previous.state == "COMPLETED" and previous != updated:
                    raise ValueError("completed checkpoints are immutable")
                if len(updated.attempts) < len(previous.attempts):
                    raise ValueError("attempt history cannot be removed")
                for before, after in zip(
                    previous.attempts, updated.attempts[: len(previous.attempts)], strict=True
                ):
                    if before.state != "RUNNING" and before != after:
                        raise ValueError("finished attempts are immutable")
                    if before.state == "RUNNING" and (
                        before.attempt_id,
                        before.proposal_id,
                        before.session_id,
                        before.idempotency_key,
                        before.started_at,
                        before.retry_safe,
                        before.capability_fingerprint,
                        before.policy_refs,
                    ) != (
                        after.attempt_id,
                        after.proposal_id,
                        after.session_id,
                        after.idempotency_key,
                        after.started_at,
                        after.retry_safe,
                        after.capability_fingerprint,
                        after.policy_refs,
                    ):
                        raise ValueError("attempt identity/profile changed")
            connection.execute(
                "UPDATE tasks SET revision=?,state=?,data=? WHERE task_id=? AND revision=?",
                (
                    task.revision,
                    task.state,
                    task.model_dump_json(),
                    str(task.task_id),
                    expected_revision,
                ),
            )
            self._checkpoint(connection, task, event, audit)

    def get(self, task_id: UUID) -> TaskRecord | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id=?", (str(task_id),)
            ).fetchone()
            return _decode_task_row(row) if row is not None else None

    def all(self) -> tuple[TaskRecord, ...]:
        with self.database.transaction() as connection:
            # Decode all rows before filtering, so corrupt state columns cannot hide unsafe work.
            tasks = tuple(
                _decode_task_row(row)
                for row in connection.execute("SELECT * FROM tasks ORDER BY rowid")
            )
            return tasks

    def unfinished(self) -> tuple[TaskRecord, ...]:
        return tuple(task for task in self.all() if task.state not in TERMINAL_STATES)

    def history(self, task_id: UUID) -> tuple[TaskRecord, ...]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM task_history WHERE task_id=? ORDER BY revision", (str(task_id),)
            ).fetchall()
            try:
                tasks = tuple(decode_task(row["data"]) for row in rows)
                if any(
                    task.task_id != task_id or task.revision != row["revision"]
                    for task, row in zip(tasks, rows, strict=True)
                ):
                    raise ValueError("history metadata mismatch")
                if any(task.revision != index for index, task in enumerate(tasks)):
                    raise ValueError("history revision gap")
                return tasks
            except (ValueError, TypeError, AttributeError):
                raise StorageError("record_corrupt") from None

    @contextmanager
    def claim(self, task_id: UUID) -> Iterator[None]:
        # Canonical database path puts all processes on the same lock, including symlink aliases.
        lock = self.database.path.with_name(f"{self.database.path.name}.task-{task_id}.lock")
        with _file_lock(lock):
            yield

    def request_cancel(self, task_id: UUID) -> None:
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO cancellations VALUES(?,?)",
                (str(task_id), utc_now().isoformat()),
            )

    def cancellation_requested(self, task_id: UUID) -> bool:
        with self.database.transaction() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM cancellations WHERE task_id=?", (str(task_id),)
                ).fetchone()
                is not None
            )
