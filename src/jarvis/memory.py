"""Permission-scoped memory domain. Credentials are separate from untrusted record data."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError

from jarvis.authorization import DeterministicPolicy
from jarvis.core.contracts import (
    AccessScope,
    ActionProposal,
    AuditRecord,
    Capability,
    ErrorDetail,
    Session,
)
from jarvis.core.memory_records import (
    MemoryCursor,
    MemoryDraft,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
    MemorySearchResult,
)
from jarvis.core.time import utc_now
from jarvis.identity import IdentityError, IdentityRepository
from jarvis.observability import AuditSink, AuditWriteError, write_audit
from jarvis.permissions import PermissionLookup
from jarvis.storage.errors import StorageError


class MemoryError(RuntimeError):
    """Stable codes only; unavailable IDs never reveal existence, owner, or content."""

    def __init__(self, code: str):
        self.code = code
        self.detail = ErrorDetail(code=code, message=code)
        super().__init__(code)


class MemoryStore(Protocol):
    """Trusted backend internals only; no public unscoped read/search interface."""

    identities: IdentityRepository
    audit: AuditSink

    def _bind(self) -> object: ...

    def _get(
        self,
        memory_id: UUID,
        principal_id: UUID,
        *,
        household: bool = False,
        owner_only: bool = False,
        access: object,
    ) -> MemoryRecord | None: ...

    def _search(
        self,
        principal_id: UUID,
        household: bool,
        query: MemoryQuery,
        now: datetime,
        *,
        access: object,
    ) -> tuple[MemoryRecord, ...]: ...

    def _commit(
        self,
        changes: tuple[tuple[MemoryRecord, int | None], ...],
        audit: AuditRecord,
        *,
        access: object,
    ) -> None: ...


def _active(record: MemoryRecord, query: MemoryQuery, now: datetime) -> bool:
    # Retention/deletion apply NOW even for past-time/history queries.
    if record.deletion_state != "active" or (
        record.expires_at is not None and record.expires_at <= now
    ):
        return False
    if not query.historical and record.superseded_at is not None:
        return False
    at = query.at or (None if query.historical else now)
    return at is None or (
        (record.valid_from is None or record.valid_from <= at)
        and (record.valid_to is None or at < record.valid_to)
    )


class MemoryService:
    def __init__(self, store: MemoryStore, permissions: PermissionLookup):
        if permissions.identities is not store.identities:
            raise ValueError("memory and permissions must share trusted identity state")
        self._store = store
        self._identities = store.identities
        self._permissions = permissions
        self._audit = store.audit
        self._policy = DeterministicPolicy(self._identities, permissions, self._audit)
        self._access = store._bind()

    @contextmanager
    def _request(self, token: str, action: str) -> Iterator[Session]:
        session = None
        try:
            # Same Phase 3 host-wide security claim spans policy and the memory operation.
            with self._identities.transaction():
                session = self._identities.resolve_session(token)
                if session.principal_id is None or session.authentication_ref is None:
                    raise IdentityError("unauthenticated")
                try:
                    yield session
                except ValidationError:
                    raise MemoryError("validation_failure") from None
                except StorageError as exc:
                    logging.getLogger("jarvis").error(
                        "memory.storage_failure", extra={"reason_code": exc.code}
                    )
                    raise MemoryError(exc.code) from None
        except (IdentityError, MemoryError, ValidationError) as exc:
            code = (
                exc.code
                if isinstance(exc, MemoryError)
                else "validation_failure"
                if isinstance(exc, ValidationError)
                else "access_denied"
            )
            try:
                write_audit(self._audit, self._record(session, action, "denied", code))
            except AuditWriteError:
                raise MemoryError("audit_unavailable") from None
            raise MemoryError(code) from None
        except AuditWriteError:
            raise MemoryError("audit_unavailable") from None
        except StorageError as exc:
            logging.getLogger("jarvis").error(
                "memory.storage_failure", extra={"reason_code": exc.code}
            )
            raise MemoryError(exc.code) from None

    @staticmethod
    def _record(session, action, outcome, reason, *, refs=(), decision=None):
        return AuditRecord(
            principal_id=session.principal_id if session else None,
            session_id=session.session_id if session else None,
            correlation_id=decision.correlation_id if decision else uuid4(),
            decision_id=decision.decision_id if decision else None,
            proposal_id=decision.proposal_id if decision else None,
            action=action,
            outcome=outcome,
            reason_code=reason,
            policy_refs=decision.policy_refs if decision else refs,
        )

    def _authorize(self, session, token, action, required, scope=None, *, hidden=False):
        # Household membership is explicit, plus a separate current owner-administered grant.
        if scope is not None and scope.kind == "household":
            required += ("memory.household.read",)
        label = (
            AccessScope(owner_id=session.principal_id)
            if scope is None
            else AccessScope(
                owner_id=scope.owner_id,
                kind="shared" if scope.kind != "private" else "private",
                shared_with=scope.shared_with,
                policy_ref=scope.policy_ref,
            )
        )
        proposal = ActionProposal(
            session_id=session.session_id,
            capability_id=action,
            correlation_id=uuid4(),
            origin="rule",
            source_ref="memory.service",
        )
        capability = Capability(
            capability_id=action,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            required_permissions=required,
            side_effect="local" if "memory.write" in required else "none",
            adapter_ref="memory.service",
        )
        decision = self._policy.evaluate(token, proposal, capability, access_scope=label)
        if decision.decision != "allow":
            raise MemoryError("memory_unavailable" if hidden else "access_denied")
        return decision

    def _targets(self, scope):
        for principal_id in (scope.owner_id, *scope.shared_with):
            target = self._identities.get_principal(principal_id)
            if target is None or target.status != "active":
                raise MemoryError("access_denied")

    def create(self, draft: MemoryDraft, *, session_token: str) -> MemoryRecord:
        with self._request(session_token, "memory.create") as session:
            draft = MemoryDraft.model_validate(draft.model_dump()).model_copy(deep=True)
            scope = draft.access_scope or MemoryScope(owner_id=session.principal_id)
            required = ("memory.write",)
            if scope.owner_id != session.principal_id:
                if session.principal_id != self._identities.owner_id:
                    raise MemoryError("access_denied")
                required += ("memory.write_others",)
            if scope.kind != "private":
                required += ("memory.share",)
            self._targets(scope)
            # Writing for another owner does not grant the creator read access.
            decision = self._authorize(session, session_token, "memory.create", required)
            now = utc_now()
            data = draft.model_dump(exclude={"access_scope", "schema_version"})
            record = MemoryRecord(
                **data,
                access_scope=scope,
                creator_id=session.principal_id,
                created_at=now,
                updated_at=now,
            )
            self._store._commit(
                ((record, None),),
                self._record(session, "memory.create", "succeeded", "created", decision=decision),
                access=self._access,
            )
            # Acknowledges only the submitted new data; later retrieval still needs read access.
            return record.model_copy(deep=True)

    def get(
        self,
        memory_id: UUID,
        *,
        session_token: str,
        historical: bool = False,
        at: datetime | None = None,
    ) -> MemoryRecord:
        with self._request(session_token, "memory.get") as session:
            query = MemoryQuery(historical=historical, at=at)
            required = ("memory.read",) + (
                ("memory.history",) if historical or at is not None else ()
            )
            self._authorize(session, session_token, "memory.get", required)
            household = bool(
                self._permissions.current_grants(session.principal_id, ("memory.household.read",))
            )
            record = self._store._get(
                memory_id, session.principal_id, household=household, access=self._access
            )
            if record is None:
                raise MemoryError("memory_unavailable")
            self._authorize(
                session, session_token, "memory.get", required, record.access_scope, hidden=True
            )
            if not _active(record, query, utc_now()):
                raise MemoryError("memory_unavailable")
            return record

    def search(self, query: MemoryQuery, *, session_token: str) -> MemorySearchResult:
        with self._request(session_token, "memory.search") as session:
            query = MemoryQuery.model_validate(query.model_dump()).model_copy(deep=True)
            required = ("memory.read",) + (
                ("memory.history",) if query.historical or query.at is not None else ()
            )
            self._authorize(session, session_token, "memory.search", required)
            household = bool(
                self._permissions.current_grants(session.principal_id, ("memory.household.read",))
            )
            records = self._store._search(
                session.principal_id, household, query, utc_now(), access=self._access
            )
            # Defense in depth: backend selection must never expand domain scope.
            for record in records:
                scope = record.access_scope
                if not (
                    session.principal_id == scope.owner_id
                    or (scope.kind != "private" and session.principal_id in scope.shared_with)
                ) or (scope.kind == "household" and not household):
                    raise StorageError("memory_scope_invalid")
            page = records[: query.limit]
            cursor = None
            if len(records) > query.limit:
                last = page[-1]
                cursor = MemoryCursor(created_at=last.created_at, memory_id=last.memory_id)
            return MemorySearchResult(records=page, next_cursor=cursor)

    def _owned(self, memory_id, expected_revision, session, token, action, required):
        self._authorize(session, token, action, required)
        record = self._store._get(
            memory_id, session.principal_id, owner_only=True, access=self._access
        )
        if record is None or record.access_scope.owner_id != session.principal_id:
            raise MemoryError("memory_unavailable")
        if record.deletion_state != "active":
            raise MemoryError("memory_unavailable")
        if type(expected_revision) is not int or record.revision != expected_revision:
            raise MemoryError("revision_conflict")
        decision = self._authorize(
            session, token, action, required, MemoryScope(owner_id=record.access_scope.owner_id)
        )
        return record, decision

    def supersede(
        self,
        memory_id: UUID,
        replacement: MemoryDraft,
        *,
        expected_revision: int,
        session_token: str,
    ) -> MemoryRecord:
        with self._request(session_token, "memory.supersede") as session:
            old, decision = self._owned(
                memory_id,
                expected_revision,
                session,
                session_token,
                "memory.supersede",
                ("memory.write",),
            )
            now = utc_now()
            if not _active(old, MemoryQuery(), now):
                raise MemoryError("memory_unavailable")
            replacement = MemoryDraft.model_validate(replacement.model_dump()).model_copy(deep=True)
            scope = replacement.access_scope or old.access_scope
            # Correction cannot silently change recipients, owner, consent or retention.
            if scope != old.access_scope or (
                replacement.consent_ref,
                replacement.retention_ref,
                replacement.expires_at,
            ) != (old.consent_ref, old.retention_ref, old.expires_at):
                raise MemoryError("validation_failure")
            cutoff = replacement.valid_from or now
            if cutoff > now or (old.valid_from is not None and cutoff < old.valid_from):
                raise MemoryError("validation_failure")
            new = MemoryRecord(
                **replacement.model_dump(exclude={"access_scope", "valid_from", "schema_version"}),
                access_scope=scope,
                creator_id=session.principal_id,
                created_at=now,
                updated_at=now,
                valid_from=cutoff,
                supersedes=old.memory_id,
            )
            prior = MemoryRecord.model_validate(
                {
                    **old.model_dump(),
                    "revision": old.revision + 1,
                    "updated_at": now,
                    "valid_to": cutoff,
                    "superseded_at": now,
                    "superseded_by": new.memory_id,
                }
            )
            self._store._commit(
                ((prior, old.revision), (new, None)),
                self._record(
                    session, "memory.supersede", "succeeded", "superseded", decision=decision
                ),
                access=self._access,
            )
            return new

    def delete(self, memory_id: UUID, *, expected_revision: int, session_token: str) -> None:
        with self._request(session_token, "memory.delete") as session:
            old, decision = self._owned(
                memory_id,
                expected_revision,
                session,
                session_token,
                "memory.delete",
                ("memory.write",),
            )
            now = utc_now()
            record = MemoryRecord.model_validate(
                {
                    **old.model_dump(),
                    "revision": old.revision + 1,
                    "updated_at": now,
                    "deleted_at": now,
                    "deletion_state": "tombstoned",
                }
            )
            self._store._commit(
                ((record, old.revision),),
                self._record(
                    session, "memory.delete", "succeeded", "tombstoned", decision=decision
                ),
                access=self._access,
            )

    def revoke_access(
        self, memory_id: UUID, principal_id: UUID, *, expected_revision: int, session_token: str
    ) -> None:
        with self._request(session_token, "memory.revoke_access") as session:
            old, decision = self._owned(
                memory_id,
                expected_revision,
                session,
                session_token,
                "memory.revoke_access",
                ("memory.write", "memory.share"),
            )
            if not isinstance(principal_id, UUID):
                raise MemoryError("validation_failure")
            recipients = tuple(p for p in old.access_scope.shared_with if p != principal_id)
            scope = MemoryScope(
                owner_id=old.access_scope.owner_id,
                kind=old.access_scope.kind if recipients else "private",
                shared_with=recipients,
                policy_ref=old.access_scope.policy_ref,
            )
            record = MemoryRecord.model_validate(
                {
                    **old.model_dump(),
                    "access_scope": scope,
                    "revision": old.revision + 1,
                    "updated_at": utc_now(),
                }
            )
            self._store._commit(
                ((record, old.revision),),
                self._record(
                    session,
                    "memory.revoke_access",
                    "succeeded",
                    "access_revoked",
                    decision=decision,
                ),
                access=self._access,
            )

    def set_retention(
        self,
        memory_id: UUID,
        *,
        retention_ref: str,
        expires_at: datetime | None,
        expected_revision: int,
        session_token: str,
    ) -> None:
        with self._request(session_token, "memory.retention") as session:
            old, decision = self._owned(
                memory_id,
                expected_revision,
                session,
                session_token,
                "memory.retention",
                ("memory.write",),
            )
            # Expired data cannot be resurrected by removing/extending its expiry.
            if old.expires_at is not None and old.expires_at <= utc_now():
                raise MemoryError("memory_unavailable")
            record = MemoryRecord.model_validate(
                {
                    **old.model_dump(),
                    "retention_ref": retention_ref,
                    "expires_at": expires_at,
                    "revision": old.revision + 1,
                    "updated_at": utc_now(),
                }
            )
            self._store._commit(
                ((record, old.revision),),
                self._record(
                    session, "memory.retention", "succeeded", "retention_changed", decision=decision
                ),
                access=self._access,
            )
