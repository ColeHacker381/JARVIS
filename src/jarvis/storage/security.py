"""Durable security adapters; development credential issuance is explicitly isolated."""

import hashlib
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import AwareDatetime

from jarvis.core.contracts import AuditRecord, Contract, Name, Principal, Session, decode_contract
from jarvis.core.time import utc_now
from jarvis.identity import IdentityError, SessionCredentials
from jarvis.permissions import InMemoryPermissionStore, PermissionGrant
from jarvis.storage.errors import StorageError
from jarvis.storage.sqlite import SQLiteAuditSink, SQLiteDatabase, _append_audit, _file_lock


class SessionAuthenticator(Protocol):
    """Trusted runtime adapter verifies credentials, never request-supplied identity claims."""

    def authenticate(self, token: str) -> UUID: ...


class GrantRecord(Contract):
    grant_id: UUID
    principal_id: UUID
    permission: Name
    granted_by: UUID
    granted_at: AwareDatetime
    revoked_at: AwareDatetime | None = None

    def as_grant(self) -> PermissionGrant:
        return PermissionGrant(self.grant_id, self.principal_id, self.permission, self.granted_by)


def _decode_principal(row) -> Principal | None:
    if row is None:
        return None
    record = decode_contract(row["data"])
    if not isinstance(record, Principal) or (
        str(record.principal_id),
        record.kind,
        record.status,
    ) != (row["principal_id"], row["kind"], row["status"]):
        raise ValueError("principal metadata mismatch")
    return record


def _decode_session(row) -> Session | None:
    if row is None:
        return None
    record = decode_contract(row["data"])
    if not isinstance(record, Session) or (
        str(record.session_id),
        str(record.principal_id) if record.principal_id else None,
    ) != (row["session_id"], row["principal_id"]):
        raise ValueError("session metadata mismatch")
    return record


def _decode_grant(row) -> GrantRecord | None:
    if row is None:
        return None
    import json

    data = json.loads(row["data"])
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("unsupported grant version")
    record = GrantRecord.model_validate_json(row["data"])
    if (
        str(record.grant_id),
        str(record.principal_id),
        record.permission,
        str(record.granted_by),
        record.revoked_at.isoformat() if record.revoked_at else None,
    ) != (
        row["grant_id"],
        row["principal_id"],
        row["permission"],
        row["granted_by"],
        row["revoked_at"],
    ):
        raise ValueError("grant metadata mismatch")
    if record.revoked_at is not None and record.revoked_at < record.granted_at:
        raise ValueError("revocation precedes grant")
    return record


class SQLiteIdentityRepository:
    """No bootstrap or authentication-by-UUID API. Missing authenticator always denies.

    A future production authenticator must verify credentials and return a persisted
    session reference. Its stack and trusted provisioning remain OPEN. This default
    repository refuses development authority stores and development sessions.
    """

    _mode = "trusted"

    def __init__(self, database: SQLiteDatabase, authenticator: SessionAuthenticator | None = None):
        self.database = database
        self._authenticator = authenticator
        self._lock = RLock()
        self._depth = 0
        self._authority_id: UUID | None = None

    def _state(self, *, allow_empty: bool = False):
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM security_authority WHERE singleton=1"
            ).fetchone()
            if row is None:
                if allow_empty:
                    return None
                raise IdentityError("security_state_uninitialized")
            if type(row["schema_version"]) is not int or row["schema_version"] != 1:
                raise IdentityError("security_version_unsupported")
            if row["mode"] != self._mode:
                raise IdentityError("security_mode_mismatch")
            authority_id, owner_id = UUID(row["authority_id"]), UUID(row["owner_id"])
            owner = _decode_principal(
                connection.execute(
                    "SELECT * FROM security_principals WHERE principal_id=?", (str(owner_id),)
                ).fetchone()
            )
            if owner is None or owner.kind != "owner" or owner.status != "active":
                raise IdentityError("security_owner_invalid")
            if self._authority_id is not None and self._authority_id != authority_id:
                raise IdentityError("security_authority_changed")
            self._authority_id = authority_id
            return owner_id

    @contextmanager
    def _transaction(self, *, allow_empty: bool = False) -> Iterator[None]:
        # Reentrant identity/policy/admin/executor calls share one OS claim, not a long SQL tx.
        with self._lock:
            try:
                if self._depth:
                    self._depth += 1
                    try:
                        yield
                    finally:
                        self._depth -= 1
                else:
                    path = self.database.path.with_name(self.database.path.name + ".security.lock")
                    with _file_lock(path, blocking=True):
                        self._state(allow_empty=allow_empty)
                        self._depth = 1
                        try:
                            yield
                        finally:
                            self._depth = 0
            except StorageError:
                raise IdentityError("security_state_unavailable") from None
            except (ValueError, TypeError, KeyError, AttributeError):
                raise IdentityError("security_record_invalid") from None

    def transaction(self):
        return self._transaction()

    @property
    def owner_id(self) -> UUID:
        with self.transaction():
            return self._state()

    def get_principal(self, principal_id: UUID) -> Principal | None:
        with self.transaction(), self.database.transaction() as connection:
            return _decode_principal(
                connection.execute(
                    "SELECT * FROM security_principals WHERE principal_id=?", (str(principal_id),)
                ).fetchone()
            )

    def get_session(self, session_id: UUID) -> Session | None:
        with self.transaction(), self.database.transaction() as connection:
            return _decode_session(
                connection.execute(
                    "SELECT * FROM security_sessions WHERE session_id=?", (str(session_id),)
                ).fetchone()
            )

    def _authenticate(self, token: str) -> UUID:
        if self._authenticator is None:
            raise IdentityError("authentication_unavailable")
        session_id = self._authenticator.authenticate(token)
        if not isinstance(session_id, UUID):
            raise IdentityError("authentication_invalid")
        return session_id

    def resolve_session(self, token: str) -> Session:
        with self.transaction():
            if not isinstance(token, str) or not token:
                raise IdentityError("invalid_session")
            session = self.get_session(self._authenticate(token))
            if session is None or session.authentication_ref is None:
                raise IdentityError("invalid_session")
            if self._mode == "trusted" and session.authentication_ref.startswith("development."):
                raise IdentityError("development_session_forbidden")
            now = utc_now()
            if session.ended_at is not None or (
                session.expires_at is not None and session.expires_at <= now
            ):
                raise IdentityError("session_inactive")
            principal = self.get_principal(session.principal_id) if session.principal_id else None
            if session.principal_id is not None and (
                principal is None or principal.status != "active"
            ):
                raise IdentityError("principal_inactive")
            return session


class SQLiteDevelopmentIdentityService(SQLiteIdentityRepository):
    """Explicit offline fixture only. Never wire this subclass to a production request API."""

    _mode = "development"

    def bootstrap_owner_for_testing(self, display_name="Development owner", *, principal_id=None):
        with (
            self._transaction(allow_empty=True),
            self.database.transaction(write=True) as connection,
        ):
            if connection.execute("SELECT 1 FROM security_authority").fetchone() is not None:
                raise IdentityError("owner_already_bootstrapped")
            for table in ("security_principals", "security_sessions", "security_grants"):
                if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None:
                    raise IdentityError("security_bootstrap_not_empty")
            owner = Principal(
                principal_id=principal_id or uuid4(), display_name=display_name, kind="owner"
            )
            self._insert_principal(connection, owner)
            connection.execute(
                "INSERT INTO security_authority VALUES(1,1,?,?,?)",
                (str(uuid4()), str(owner.principal_id), self._mode),
            )
            return owner

    @staticmethod
    def _insert_principal(connection, principal):
        connection.execute(
            "INSERT INTO security_principals VALUES(?,?,?,?)",
            (
                str(principal.principal_id),
                principal.kind,
                principal.status,
                principal.model_dump_json(),
            ),
        )

    def create_principal_for_testing(
        self, display_name, *, kind="user", relationship_ref=None, principal_id=None
    ):
        if kind not in {"user", "guest"}:
            raise IdentityError("owner_bootstrap_required")
        principal = Principal(
            principal_id=principal_id or uuid4(),
            display_name=display_name,
            kind=kind,
            relationship_ref=relationship_ref,
        )
        with self.transaction(), self.database.transaction(write=True) as connection:
            self._insert_principal(connection, principal)
        return principal

    def establish_session_for_testing(self, principal_id, *, expires_at: datetime | None = None):
        with self.transaction():
            if principal_id is not None:
                principal = self.get_principal(principal_id)
                if principal is None or principal.status != "active":
                    raise IdentityError("unknown_or_disabled_principal")
            session = Session(
                principal_id=principal_id,
                interface="development",
                authentication_ref="development.sqlite_fixture" if principal_id else None,
                expires_at=expires_at,
            )
            token = secrets.token_urlsafe(32)
            with self.database.transaction(write=True) as connection:
                connection.execute(
                    "INSERT INTO security_sessions VALUES(?,?,?)",
                    (
                        str(session.session_id),
                        str(principal_id) if principal_id else None,
                        session.model_dump_json(),
                    ),
                )
                connection.execute(
                    "INSERT INTO development_session_tokens VALUES(?,?)",
                    (
                        hashlib.sha256(token.encode()).hexdigest(),
                        str(session.session_id),
                    ),
                )
            return SessionCredentials(session_id=session.session_id, token=token)

    def _authenticate(self, token: str) -> UUID:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT session_id FROM development_session_tokens WHERE token_hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
        if row is None:
            raise IdentityError("invalid_session")
        return UUID(row["session_id"])

    def disable_principal_for_testing(self, principal_id):
        with self.transaction():
            principal = self.get_principal(principal_id)
            if principal is None:
                raise IdentityError("principal_unknown")
            disabled = Principal.model_validate({**principal.model_dump(), "status": "disabled"})
            with self.database.transaction(write=True) as connection:
                connection.execute(
                    "UPDATE security_principals SET status=?,data=? WHERE principal_id=?",
                    (disabled.status, disabled.model_dump_json(), str(principal_id)),
                )

    def close_session_for_testing(self, token):
        with self.transaction():
            session = self.resolve_session(token)
            closed = Session.model_validate({**session.model_dump(), "ended_at": utc_now()})
            with self.database.transaction(write=True) as connection:
                connection.execute(
                    "UPDATE security_sessions SET data=? WHERE session_id=?",
                    (closed.model_dump_json(), str(session.session_id)),
                )


class SQLitePermissionStore(InMemoryPermissionStore):
    """Reuses owner/target/input checks; SQL is the only grant authority, never cached refs."""

    def __init__(self, identities: SQLiteIdentityRepository, audit: SQLiteAuditSink):
        if identities.database.path != audit.database.path:
            raise ValueError("security state and durable audit must share one database")
        self.identities = identities
        self._audit = audit
        self.database = identities.database

    def _active_grant(self, principal_id, permission):
        with self.database.transaction() as connection:
            record = _decode_grant(
                connection.execute(
                    "SELECT * FROM security_grants WHERE principal_id=? AND permission=? "
                    "AND revoked_at IS NULL",
                    (str(principal_id), permission),
                ).fetchone()
            )
        if record is not None and record.granted_by != self.identities.owner_id:
            raise IdentityError("security_grant_authority_invalid")
        return record.as_grant() if record else None

    def _historical_grant(self, policy_ref):
        if not policy_ref.startswith("grant:"):
            raise KeyError(policy_ref)
        with self.database.transaction() as connection:
            record = _decode_grant(
                connection.execute(
                    "SELECT * FROM security_grants WHERE grant_id=?",
                    (policy_ref.removeprefix("grant:"),),
                ).fetchone()
            )
        if record is None:
            raise KeyError(policy_ref)
        return record.as_grant()

    def _commit_change(self, key, grant, revoke, record: AuditRecord):
        with self.database.transaction(write=True) as connection:
            _append_audit(connection, record)
            if grant is None:
                return  # Even a no-op revoke remains durably audited.
            old = _decode_grant(
                connection.execute(
                    "SELECT * FROM security_grants WHERE grant_id=?", (str(grant.grant_id),)
                ).fetchone()
            )
            if revoke:
                revoked = old.model_copy(update={"revoked_at": utc_now()})
                connection.execute(
                    "UPDATE security_grants SET revoked_at=?,data=? WHERE grant_id=?",
                    (
                        revoked.revoked_at.isoformat(),
                        revoked.model_dump_json(),
                        str(grant.grant_id),
                    ),
                )
            elif old is None:
                stored = GrantRecord(
                    grant_id=grant.grant_id,
                    principal_id=key[0],
                    permission=key[1],
                    granted_by=grant.granted_by,
                    granted_at=utc_now(),
                )
                connection.execute(
                    "INSERT INTO security_grants VALUES(?,?,?,?,?,?)",
                    (
                        str(grant.grant_id),
                        str(grant.principal_id),
                        grant.permission,
                        str(grant.granted_by),
                        None,
                        stored.model_dump_json(),
                    ),
                )
