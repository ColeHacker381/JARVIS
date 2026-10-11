"""Explicit host-only provisioning for the provisional loopback development API."""

import hashlib
import json
import os
import secrets
import stat
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from jarvis.core.contracts import AuditRecord, Session
from jarvis.core.time import utc_now
from jarvis.identity import IdentityError
from jarvis.storage.security import SQLiteDevelopmentIdentityService, SQLiteIdentityRepository
from jarvis.storage.sqlite import _append_audit


def read_private_json(path: Path):
    """Explicit path, same OS user, no symlinks, regular 0600 file, bounded bytes."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 65536
        ):
            raise ValueError("credential_file_unsafe")
        with os.fdopen(fd, closefd=False) as stream:
            return json.load(stream)
    finally:
        os.close(fd)


def write_private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def export_curl_configuration(credential_path, output):
    import re

    credential = read_private_json(credential_path)
    if not isinstance(credential, dict):
        raise ValueError("client_credential_file_invalid")
    token = credential.get("token")
    if (
        credential.get("kind") != "client"
        or not isinstance(token, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", token)
    ):
        raise ValueError("client_credential_file_invalid")
    # Token is only in a new 0600 file; never in curl argv, stdout or shell history.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(f'header = "Authorization: Bearer {token}"\n')
        stream.flush()
        os.fsync(stream.fileno())


class LocalCredentialAuthenticator:
    def __init__(self, database):
        self.database = database

    def authenticate(self, token, *, kind=None):
        if not isinstance(token, str) or not 40 <= len(token) <= 128:
            raise IdentityError("invalid_session")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.database.transaction() as c:
            row = c.execute(
                "SELECT * FROM local_api_credentials WHERE token_hash=?", (digest,)
            ).fetchone()
        matched = secrets.compare_digest(row["token_hash"] if row else "0" * 64, digest)
        if (
            not row
            or not matched
            or row["revoked_at"] is not None
            or row["kind"] not in {"client", "worker"}
            or (kind is not None and row["kind"] != kind)
        ):
            raise IdentityError("invalid_session")
        return UUID(row["session_id"])


class LocalDevelopmentIdentityRepository(SQLiteIdentityRepository):
    """No fixture issuer/bootstrap API; tokens map to persisted, expiring sessions."""

    _mode = "development"

    def __init__(self, database):
        super().__init__(database, LocalCredentialAuthenticator(database))

    def resolve_kind(self, token, kind):
        with self.transaction():
            self._authenticator.authenticate(token, kind=kind)
            session = self.resolve_session(token)
            if session.authentication_ref != f"development.local_api.{kind}":
                raise IdentityError("invalid_session")
            return session

    def resolve_session(self, token):
        session = super().resolve_session(token)
        if (
            session.interface != "development.local_api"
            or session.authentication_ref
            not in {"development.local_api.client", "development.local_api.worker"}
            or session.expires_at is None
        ):
            raise IdentityError("invalid_session")
        return session

    def contains_credential(self, text):
        import re

        candidates = re.findall(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])", text)
        with self.database.transaction() as c:
            return any(
                c.execute(
                    "SELECT 1 FROM local_api_credentials WHERE token_hash=?",
                    (hashlib.sha256(token.encode()).hexdigest(),),
                ).fetchone()
                for token in candidates
            )

    def validate_authority(self):
        from jarvis.storage.security import _decode_grant, _decode_principal, _decode_session

        with self.transaction(), self.database.transaction() as c:
            owner = self.owner_id
            for row in c.execute("SELECT * FROM security_principals"):
                _decode_principal(row)
            for row in c.execute("SELECT * FROM security_sessions"):
                _decode_session(row)
            for row in c.execute("SELECT * FROM security_grants"):
                if _decode_grant(row).granted_by != owner:
                    raise IdentityError("security_grant_authority_invalid")
            if c.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise IdentityError("security_record_invalid")
            for row in c.execute("SELECT * FROM local_api_credentials"):
                import re
                from datetime import datetime

                UUID(row["credential_id"])
                session = self.get_session(UUID(row["session_id"]))
                created = datetime.fromisoformat(row["created_at"])
                revoked = datetime.fromisoformat(row["revoked_at"]) if row["revoked_at"] else None
                if (
                    not re.fullmatch(r"[a-f0-9]{64}", row["token_hash"])
                    or row["kind"] not in {"client", "worker"}
                    or created.utcoffset() is None
                    or session is None
                    or session.expires_at is None
                    or session.interface != "development.local_api"
                    or session.authentication_ref != f"development.local_api.{row['kind']}"
                    or (revoked is not None and (revoked.utcoffset() is None or revoked < created))
                ):
                    raise IdentityError("security_record_invalid")


def issue_credential(database, principal_id, *, kind="client", hours=24):
    """Trusted OS operator command only. No HTTP route calls this function."""
    if kind not in {"client", "worker"} or type(hours) is not int or not 1 <= hours <= 8760:
        raise ValueError("credential_options_invalid")
    fixture = SQLiteDevelopmentIdentityService(database)
    with fixture.transaction():
        principal = fixture.get_principal(principal_id)
        if principal is None or principal.status != "active":
            raise IdentityError("unknown_or_disabled_principal")
        token, credential_id = secrets.token_urlsafe(32), uuid4()
        session = Session(
            principal_id=principal_id,
            interface="development.local_api",
            authentication_ref=f"development.local_api.{kind}",
            expires_at=utc_now() + timedelta(hours=hours),
        )
        with database.transaction(write=True) as c:
            c.execute(
                "INSERT INTO security_sessions VALUES(?,?,?)",
                (str(session.session_id), str(principal_id), session.model_dump_json()),
            )
            c.execute(
                "INSERT INTO local_api_credentials VALUES(?,?,?,?,?,NULL)",
                (
                    str(credential_id),
                    hashlib.sha256(token.encode()).hexdigest(),
                    str(session.session_id),
                    kind,
                    utc_now().isoformat(),
                ),
            )
            _append_audit(
                c,
                AuditRecord(
                    principal_id=principal_id,
                    session_id=session.session_id,
                    correlation_id=credential_id,
                    action="local_credential.issued",
                    outcome="succeeded",
                    reason_code="development_host_provisioned",
                ),
            )
    return {
        "credential_id": str(credential_id),
        "token": token,
        "expires_at": session.expires_at.isoformat(),
        "kind": kind,
    }


def revoke_credential(database, credential_id):
    fixture = SQLiteDevelopmentIdentityService(database)
    with fixture.transaction(), database.transaction(write=True) as c:
        row = c.execute(
            "SELECT * FROM local_api_credentials WHERE credential_id=?", (str(credential_id),)
        ).fetchone()
        if row is None:
            raise IdentityError("credential_unavailable")
        if row["revoked_at"] is None:
            c.execute(
                "UPDATE local_api_credentials SET revoked_at=? WHERE credential_id=?",
                (utc_now().isoformat(), str(credential_id)),
            )
            _append_audit(
                c,
                AuditRecord(
                    principal_id=None,
                    correlation_id=credential_id,
                    action="local_credential.revoked",
                    outcome="succeeded",
                    reason_code="development_host_revoked",
                ),
            )
