"""Provisional explicit grants; only the server-established owner administers them."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from jarvis.core.contracts import AuditRecord, Name, Session
from jarvis.identity import IdentityError, IdentityRepository
from jarvis.observability import AuditSink, write_audit


@dataclass(frozen=True)
class PermissionGrant:
    """Structured permission metadata retained for referenced audit history."""

    grant_id: UUID
    principal_id: UUID
    permission: str
    granted_by: UUID

    @property
    def policy_ref(self) -> str:
        return f"grant:{self.grant_id}"


class PermissionAdministrationError(PermissionError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class PermissionLookup(Protocol):
    identities: IdentityRepository

    def current_grants(
        self, principal_id: UUID, permissions: tuple[str, ...]
    ) -> tuple[PermissionGrant, ...]: ...


class InMemoryPermissionStore:
    def __init__(self, identities: IdentityRepository, audit: AuditSink) -> None:
        self.identities = identities
        self._audit = audit
        self._grants: dict[tuple[UUID, str], PermissionGrant] = {}
        self._history: dict[str, PermissionGrant] = {}

    def _owner_session(self, token: str, correlation_id: UUID, action: str) -> Session:
        session = None
        reason = "owner_required"
        try:
            session = self.identities.resolve_session(token)
            if (
                session.principal_id is not None
                and session.principal_id == self.identities.owner_id
            ):
                return session
        except IdentityError as exc:
            reason = exc.reason_code
        write_audit(
            self._audit,
            AuditRecord(
                principal_id=session.principal_id if session else None,
                session_id=session.session_id if session else None,
                correlation_id=correlation_id,
                action=action,
                outcome="denied",
                reason_code=reason,
                policy_refs=("policy:owner_administration",),
            ),
        )
        raise PermissionAdministrationError(reason)

    def _change(
        self, token: str, principal_id: UUID, permission: str, correlation_id: UUID, *, revoke: bool
    ) -> PermissionGrant | None:
        action = "permission.revoke" if revoke else "permission.grant"
        with self.identities.transaction():
            session = self._owner_session(token, correlation_id, action)
            # Validation happens after authority is established and never logs raw input.
            try:
                permission = TypeAdapter(Name).validate_python(permission, strict=True)
            except ValidationError:
                write_audit(
                    self._audit,
                    AuditRecord(
                        principal_id=session.principal_id,
                        session_id=session.session_id,
                        correlation_id=correlation_id,
                        action=action,
                        outcome="denied",
                        reason_code="permission_invalid",
                        policy_refs=("policy:owner_administration",),
                    ),
                )
                raise PermissionAdministrationError("permission_invalid") from None
            target = self.identities.get_principal(principal_id)
            if target is None or (not revoke and target.status != "active"):
                write_audit(
                    self._audit,
                    AuditRecord(
                        principal_id=session.principal_id,
                        session_id=session.session_id,
                        correlation_id=correlation_id,
                        action=action,
                        outcome="denied",
                        reason_code="unknown_or_disabled_target",
                        policy_refs=("policy:owner_administration",),
                    ),
                )
                raise PermissionAdministrationError("unknown_or_disabled_target")
            key = (principal_id, permission)
            grant = self._grants.get(key)
            if not revoke and grant is None:
                grant = PermissionGrant(uuid4(), principal_id, permission, session.principal_id)
            refs = ("policy:owner_administration",) + ((grant.policy_ref,) if grant else ())
            # Audit before committing a grant/revoke; a sink failure leaves state unchanged.
            write_audit(
                self._audit,
                AuditRecord(
                    principal_id=session.principal_id,
                    session_id=session.session_id,
                    correlation_id=correlation_id,
                    action=action,
                    outcome="succeeded",
                    reason_code="permission_revoked" if revoke else "permission_granted",
                    policy_refs=refs,
                ),
            )
            if revoke:
                self._grants.pop(key, None)
            else:
                self._grants[key] = grant
                self._history[grant.policy_ref] = grant
            return grant

    def grant(
        self, owner_token: str, principal_id: UUID, permission: str, *, correlation_id: UUID
    ) -> PermissionGrant:
        grant = self._change(owner_token, principal_id, permission, correlation_id, revoke=False)
        if grant is None:
            raise RuntimeError("grant creation failed")
        return grant

    def revoke(
        self, owner_token: str, principal_id: UUID, permission: str, *, correlation_id: UUID
    ) -> None:
        self._change(owner_token, principal_id, permission, correlation_id, revoke=True)

    def current_grants(
        self, principal_id: UUID, permissions: tuple[str, ...]
    ) -> tuple[PermissionGrant, ...]:
        """Trusted policy lookup only; it cannot grant or authorize anything."""
        with self.identities.transaction():
            return tuple(
                self._grants[(principal_id, permission)]
                for permission in permissions
                if (principal_id, permission) in self._grants
            )

    def inspect_grant(self, owner_token: str, policy_ref: str) -> PermissionGrant:
        """Owner-only structured audit metadata, retained after revocation."""
        with self.identities.transaction():
            self._owner_session(owner_token, uuid4(), "permission.inspect")
            return self._history[policy_ref]
