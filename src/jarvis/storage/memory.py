"""Private SQLite adapter internals, consumed only through the authorized domain service."""

import json
from datetime import UTC, datetime
from uuid import UUID

from jarvis.core.contracts import AuditRecord
from jarvis.core.memory_records import MemoryQuery, MemoryRecord, decode_memory
from jarvis.memory import MemoryError
from jarvis.storage.errors import StorageError
from jarvis.storage.security import SQLiteIdentityRepository
from jarvis.storage.sqlite import SQLiteAuditSink, SQLiteDatabase, _append_audit


def _time(value):
    return value.astimezone(UTC).isoformat() if value is not None else None


def _text(record):
    return (
        ""
        if record.deletion_state != "active"
        else json.dumps(record.content, ensure_ascii=False, sort_keys=True).casefold()
    )


def _columns(record):
    return (
        str(record.memory_id),
        record.revision,
        str(record.access_scope.owner_id),
        str(record.creator_id),
        record.access_scope.kind,
        record.kind,
        record.project_ref,
        _time(record.created_at),
        _time(record.valid_from),
        _time(record.valid_to),
        _time(record.expires_at),
        _time(record.superseded_at),
        record.deletion_state,
        _text(record),
    )


COLUMNS = (
    "memory_id",
    "revision",
    "owner_id",
    "creator_id",
    "scope_kind",
    "kind",
    "project_ref",
    "created_at",
    "valid_from",
    "valid_to",
    "expires_at",
    "superseded_at",
    "deletion_state",
    "search_text",
)


class SQLiteMemoryStore:
    def __init__(
        self, database: SQLiteDatabase, identities: SQLiteIdentityRepository, audit: SQLiteAuditSink
    ):
        if database.path != identities.database.path or database.path != audit.database.path:
            raise ValueError("memory, authority and audits must share one database")
        self._database = database
        self.identities = identities
        self.audit = audit
        self._access = None

    def _bind(self):
        if self._access is not None:
            raise ValueError("memory store already bound to a service")
        self._access = object()
        return self._access

    def _guard(self, access):
        if self._access is None or access is not self._access:
            raise MemoryError("memory_store_private")

    @staticmethod
    def _decode(connection, row):
        if row is None:
            return None
        try:
            record = decode_memory(row["data"])
            if _columns(record) != tuple(row[k] for k in COLUMNS):
                raise ValueError("memory index metadata mismatch")
            readers = {
                r[0]
                for r in connection.execute(
                    "SELECT principal_id FROM memory_readers WHERE memory_id=?", (row["memory_id"],)
                )
            }
            subjects = {
                r[0]
                for r in connection.execute(
                    "SELECT subject_id FROM memory_subjects WHERE memory_id=?", (row["memory_id"],)
                )
            }
            expected = set(map(str, record.access_scope.shared_with))
            if record.deletion_state != "active":
                expected = set()
            if readers != expected or subjects != set(map(str, record.subject_ids)):
                raise ValueError("memory scope/subject index mismatch")
            return record
        except (ValueError, TypeError, KeyError, AttributeError):
            raise StorageError("memory_record_invalid") from None

    def _get(
        self,
        memory_id: UUID,
        principal_id: UUID,
        *,
        household: bool = False,
        owner_only: bool = False,
        access: object,
    ):
        self._guard(access)
        if not isinstance(memory_id, UUID):
            raise MemoryError("validation_failure")
        # Scope selection comes before decoding: corruption in another user's record
        # cannot reveal its existence through a different error code.
        visible = "m.owner_id=?"
        params = [str(memory_id), str(principal_id)]
        if not owner_only:
            visible = (
                "(" + visible + " OR (m.scope_kind IN ('shared','household') AND EXISTS "
                "(SELECT 1 FROM memory_readers r WHERE r.memory_id=m.memory_id "
                "AND r.principal_id=?)))"
            )
            params.append(str(principal_id))
            if not household:
                visible += " AND m.scope_kind!='household'"
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT m.* FROM memories m WHERE m.memory_id=? AND " + visible, params
            ).fetchone()
            return self._decode(connection, row)

    def _search(
        self,
        principal_id: UUID,
        household: bool,
        query: MemoryQuery,
        now: datetime,
        *,
        access: object,
        project_only: bool = False,
    ):
        self._guard(access)
        # Authorization, lifecycle, and filters precede ORDER/LIMIT; no global count/ranking.
        conditions = [
            "m.deletion_state='active'",
            "(m.expires_at IS NULL OR m.expires_at>?)",
            "(m.owner_id=? OR (m.scope_kind IN ('shared','household') AND EXISTS "
            "(SELECT 1 FROM memory_readers r WHERE r.memory_id=m.memory_id AND r.principal_id=?)))",
        ]
        params = [_time(now), str(principal_id), str(principal_id)]
        if project_only:
            conditions.append("m.project_ref IS NOT NULL")
        if not household:
            conditions.append("m.scope_kind!='household'")
        if not query.historical:
            conditions.append("m.superseded_at IS NULL")
        at = query.at or (None if query.historical else now)
        if at is not None:
            conditions += [
                "(m.valid_from IS NULL OR m.valid_from<=?)",
                "(m.valid_to IS NULL OR m.valid_to>?)",
            ]
            params += [_time(at), _time(at)]
        if query.text:
            conditions.append("instr(m.search_text,?)>0")
            params.append(query.text.casefold())
        for column, value in (("kind", query.kind), ("project_ref", query.project_ref)):
            if value is not None:
                conditions.append(f"m.{column}=?")
                params.append(value)
        if query.subject_id is not None:
            conditions.append(
                "EXISTS (SELECT 1 FROM memory_subjects s WHERE "
                "s.memory_id=m.memory_id AND s.subject_id=?)"
            )
            params.append(str(query.subject_id))
        if query.cursor is not None:
            conditions.append("(m.created_at<? OR (m.created_at=? AND m.memory_id<?))")
            params += [
                _time(query.cursor.created_at),
                _time(query.cursor.created_at),
                str(query.cursor.memory_id),
            ]
        params.append(query.limit + 1)
        sql = "SELECT m.* FROM memories m WHERE " + " AND ".join(conditions)
        sql += " ORDER BY m.created_at DESC,m.memory_id DESC LIMIT ?"
        with self._database.transaction() as connection:
            return tuple(self._decode(connection, r) for r in connection.execute(sql, params))

    def _commit(
        self,
        changes: tuple[tuple[MemoryRecord, int | None], ...],
        audit: AuditRecord,
        *,
        access: object,
    ):
        self._guard(access)
        with self._database.transaction(write=True) as connection:
            for record, expected in changes:
                record = MemoryRecord.model_validate(record.model_dump()).model_copy(deep=True)
                old = self._decode(
                    connection,
                    connection.execute(
                        "SELECT * FROM memories WHERE memory_id=?", (str(record.memory_id),)
                    ).fetchone(),
                )
                if expected is None:
                    if old is not None or record.revision != 0:
                        raise MemoryError("revision_conflict")
                    values = (*_columns(record), record.model_dump_json())
                    columns = ",".join((*COLUMNS, "data"))
                    connection.execute(
                        f"INSERT INTO memories({columns}) VALUES({','.join('?' for _ in values)})",
                        values,
                    )
                else:
                    if old is None or old.revision != expected or record.revision != expected + 1:
                        raise MemoryError("revision_conflict")
                    if old.deletion_state != "active":
                        raise MemoryError("memory_unavailable")
                    # Content/evidence and ownership are immutable: corrections get NEW IDs.
                    for name in (
                        "memory_id",
                        "creator_id",
                        "created_at",
                        "kind",
                        "content",
                        "source",
                        "subject_ids",
                        "project_ref",
                        "confidence",
                        "consent_ref",
                        "valid_from",
                        "supersedes",
                    ):
                        if getattr(old, name) != getattr(record, name):
                            raise MemoryError("validation_failure")
                    if record.access_scope.owner_id != old.access_scope.owner_id:
                        raise MemoryError("validation_failure")
                    if not set(record.access_scope.shared_with) <= set(
                        old.access_scope.shared_with
                    ):
                        raise MemoryError("validation_failure")
                    values = (
                        *_columns(record)[1:],
                        record.model_dump_json(),
                        str(record.memory_id),
                        expected,
                    )
                    assignments = ",".join(f"{c}=?" for c in (*COLUMNS[1:], "data"))
                    updated = connection.execute(
                        f"UPDATE memories SET {assignments} WHERE memory_id=? AND revision=?",
                        values,
                    )
                    if updated.rowcount != 1:
                        raise MemoryError("revision_conflict")
                connection.execute(
                    "DELETE FROM memory_readers WHERE memory_id=?", (str(record.memory_id),)
                )
                if record.deletion_state == "active":
                    connection.executemany(
                        "INSERT INTO memory_readers VALUES(?,?)",
                        [(str(record.memory_id), str(p)) for p in record.access_scope.shared_with],
                    )
                if old is None:
                    connection.executemany(
                        "INSERT INTO memory_subjects VALUES(?,?)",
                        [(str(record.memory_id), str(p)) for p in set(record.subject_ids)],
                    )
            # Successful mutation audit and EVERY row/index change commit or roll back together.
            _append_audit(connection, audit)
