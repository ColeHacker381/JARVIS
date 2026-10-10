"""Trusted cloud-egress policy, independent of model transport and generated output."""

from jarvis.core.contracts import AuditRecord
from jarvis.models import ModelError
from jarvis.observability import write_audit


class EgressPolicy:
    """Trusted, default-deny exception policy; model selection is never authorization.

    Provisional exact grants are per provider and data class. Destination allowlists
    are server configuration, never user/model parameters. Private conversation and
    trusted session identifiers are always classified, even without retrieved memory.
    """

    def __init__(self, identities, permissions, audit, *, enabled=False, destinations=()):
        if permissions.identities is not identities:
            raise ValueError("egress and permissions must share trusted identity state")
        self.identities, self.permissions, self.audit = identities, permissions, audit
        self.enabled = enabled
        self.destinations = frozenset(destinations)

    def authorize(self, identity, request, *, session_token, contains_memory=False):
        with self.identities.transaction():
            session = self.identities.resolve_session(session_token)
            classes = ["conversation", "identity"]
            if request.memories or contains_memory:
                classes.append("memory")
            if request.capabilities or request.tasks:
                classes.append("capabilities")
            required = tuple(f"model.egress:{identity.provider_id}:{c}" for c in classes)
            grants = self.permissions.current_grants(session.principal_id, required)
            allowed = (
                self.enabled
                and session.authentication_ref is not None
                and session.session_id == request.session_id
                and (identity.provider_id, identity.destination) in self.destinations
                and len(grants) == len(required)
            )
            write_audit(
                self.audit,
                AuditRecord(
                    principal_id=session.principal_id,
                    session_id=session.session_id,
                    correlation_id=request.correlation_id,
                    action="model.egress",
                    outcome="allowed" if allowed else "denied",
                    reason_code="egress_allowed" if allowed else "egress_denied",
                    policy_refs=tuple(g.policy_ref for g in grants) if allowed else (),
                ),
            )
            if not allowed:
                raise ModelError("egress_denied")
