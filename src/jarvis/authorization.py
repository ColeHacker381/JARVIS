"""Deterministic policy based only on current server-owned identity and grants."""

from typing import Protocol

from jarvis.core.contracts import (
    AccessScope,
    ActionProposal,
    AuditRecord,
    Capability,
    PolicyDecision,
)
from jarvis.identity import IdentityError, IdentityRepository
from jarvis.observability import AuditSink, write_audit
from jarvis.permissions import PermissionLookup


class PolicyEvaluator(Protocol):
    identities: IdentityRepository

    def evaluate(
        self,
        session_token: str,
        proposal: ActionProposal,
        capability: Capability,
        *,
        access_scope: AccessScope | None = None,
    ) -> PolicyDecision: ...


class DeterministicPolicy:
    def __init__(
        self,
        identities: IdentityRepository,
        permissions: PermissionLookup,
        audit: AuditSink,
    ) -> None:
        if permissions.identities is not identities:
            raise ValueError("policy and permissions must share trusted identity state")
        self.identities = identities
        self._permissions = permissions
        self._audit = audit

    def evaluate(
        self,
        session_token: str,
        proposal: ActionProposal,
        capability: Capability,
        *,
        access_scope: AccessScope | None = None,
    ) -> PolicyDecision:
        with self.identities.transaction():
            session = None
            principal_id = None
            refs = ("policy:explicit_grants",)
            reason = "permission_missing"
            try:
                session = self.identities.resolve_session(session_token)
                principal_id = session.principal_id
                if session.session_id != proposal.session_id:
                    reason = "session_mismatch"
                elif principal_id is None or session.authentication_ref is None:
                    reason = "unauthenticated"
                elif capability.capability_id != proposal.capability_id:
                    reason = "capability_mismatch"
                elif not capability.required_permissions:
                    reason = "permission_declaration_missing"
                elif access_scope is not None and not (
                    principal_id == access_scope.owner_id
                    or (access_scope.kind == "shared" and principal_id in access_scope.shared_with)
                ):
                    reason = "scope_denied"
                else:
                    grants = self._permissions.current_grants(
                        principal_id, capability.required_permissions
                    )
                    if len(grants) == len(capability.required_permissions):
                        reason = "permission_granted"
                        refs += tuple(grant.policy_ref for grant in grants)
            except IdentityError as exc:
                reason = exc.reason_code
            allowed = reason == "permission_granted"
            decision = PolicyDecision(
                proposal_id=proposal.proposal_id,
                session_id=session.session_id if session else proposal.session_id,
                principal_id=principal_id,
                correlation_id=proposal.correlation_id,
                decision="allow" if allowed else "deny",
                reason_code=reason,
                policy_refs=refs,
            )
            write_audit(
                self._audit,
                AuditRecord(
                    principal_id=principal_id,
                    session_id=session.session_id if session else None,
                    proposal_id=proposal.proposal_id,
                    task_id=proposal.task_id,
                    decision_id=decision.decision_id,
                    correlation_id=proposal.correlation_id,
                    action=capability.capability_id,
                    outcome="allowed" if allowed else "denied",
                    reason_code=reason,
                    policy_refs=refs,
                ),
            )
            return decision
