"""Server-owned development identities. No production authentication is provided."""

import secrets
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Protocol
from uuid import UUID, uuid4

from jarvis.core.contracts import Principal, Session
from jarvis.core.time import utc_now


class IdentityRepository(Protocol):
    def get_principal(self, principal_id: UUID) -> Principal | None: ...

    def get_session(self, session_id: UUID) -> Session | None: ...

    def resolve_session(self, token: str) -> Session: ...

    def transaction(self) -> AbstractContextManager[None]: ...

    @property
    def owner_id(self) -> UUID | None: ...


class IdentityError(PermissionError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SessionCredentials:
    """Opaque bearer token issued by the trusted fixture, never placed in a proposal/log."""

    session_id: UUID
    token: str = field(repr=False)


class InMemoryIdentityService:
    """Only the named *_for_testing APIs establish or alter identities.

    They are trusted fixture/bootstrap entry points, NOT request handlers. A future
    authentication adapter must replace them before any network interface exists.
    """

    def __init__(self) -> None:
        self._principals: dict[UUID, Principal] = {}
        self._sessions: dict[UUID, Session] = {}
        self._tokens: dict[str, UUID] = {}
        self._owner_id: UUID | None = None
        self._lock = RLock()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Serialize identity/grant changes and synchronous authorization/invocation."""
        with self._lock:
            yield

    @property
    def owner_id(self) -> UUID | None:
        with self.transaction():
            return self._owner_id

    def bootstrap_owner_for_testing(
        self, display_name: str = "Development owner", *, principal_id: UUID | None = None
    ) -> Principal:
        with self.transaction():
            if self._owner_id is not None:
                raise ValueError("owner already bootstrapped")
            if principal_id is not None and principal_id in self._principals:
                raise ValueError("principal already exists")
            owner = Principal(
                display_name=display_name, kind="owner", principal_id=principal_id or uuid4()
            )
            self._principals[owner.principal_id] = owner
            self._owner_id = owner.principal_id
            return owner

    def create_principal_for_testing(
        self,
        display_name: str,
        *,
        kind: str = "user",
        relationship_ref: str | None = None,
        principal_id: UUID | None = None,
    ) -> Principal:
        if kind not in ("user", "guest"):
            raise ValueError("only the singleton bootstrap may create the owner")
        principal = Principal(
            display_name=display_name,
            kind=kind,
            relationship_ref=relationship_ref,
            principal_id=principal_id or uuid4(),
        )
        with self.transaction():
            if principal.principal_id in self._principals:
                raise ValueError("principal already exists")
            self._principals[principal.principal_id] = principal
        return principal

    def establish_session_for_testing(
        self, principal_id: UUID | None, *, expires_at: datetime | None = None
    ) -> SessionCredentials:
        with self.transaction():
            if principal_id is not None:
                principal = self._principals.get(principal_id)
                if principal is None or principal.status != "active":
                    raise IdentityError("unknown_or_disabled_principal")
            session = Session(
                principal_id=principal_id,
                authentication_ref="development.test_fixture" if principal_id else None,
                interface="development",
                expires_at=expires_at,
            )
            token = secrets.token_urlsafe(32)
            self._sessions[session.session_id] = session
            self._tokens[token] = session.session_id
            return SessionCredentials(session_id=session.session_id, token=token)

    def get_principal(self, principal_id: UUID) -> Principal | None:
        with self.transaction():
            return self._principals.get(principal_id)

    def get_session(self, session_id: UUID) -> Session | None:
        with self.transaction():
            return self._sessions.get(session_id)

    def resolve_session(self, token: str) -> Session:
        with self.transaction():
            if not isinstance(token, str) or token not in self._tokens:
                raise IdentityError("invalid_session")
            session = self._sessions[self._tokens[token]]
            now = utc_now()
            if session.ended_at is not None or (
                session.expires_at is not None and session.expires_at <= now
            ):
                raise IdentityError("session_inactive")
            if session.principal_id is not None:
                principal = self._principals.get(session.principal_id)
                if principal is None or principal.status != "active":
                    raise IdentityError("principal_inactive")
            return session

    def disable_principal_for_testing(self, principal_id: UUID) -> None:
        with self.transaction():
            principal = self._principals[principal_id]
            self._principals[principal_id] = principal.model_copy(update={"status": "disabled"})

    def close_session_for_testing(self, token: str) -> None:
        with self.transaction():
            session = self.resolve_session(token)
            self._sessions[session.session_id] = session.model_copy(update={"ended_at": utc_now()})
