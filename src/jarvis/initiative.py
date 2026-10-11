"""Finite local initiative evaluation. Models and sensors never supply authority."""

import hashlib
import json
import re
from contextlib import contextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4

from jsonschema.exceptions import ValidationError as ArgumentValidationError

from jarvis.core.contracts import ActionProposal, AuditRecord, Capability, Observation, Session
from jarvis.core.initiative_records import (
    TERMINAL,
    InitiativeApproval,
    InitiativeCandidate,
    InitiativeContext,
    InitiativeDecision,
    InitiativeExecutionRecord,
    InitiativePolicy,
    InitiativeProposal,
    InitiativeRecord,
    InitiativeTrigger,
    decode_initiative,
    proposal_hash,
)
from jarvis.core.memory_records import MemoryQuery
from jarvis.core.task_records import TaskStep
from jarvis.core.time import utc_now
from jarvis.core.world_records import WorldReference
from jarvis.execution import validate_capability_arguments
from jarvis.identity import IdentityError
from jarvis.memory import MemoryError
from jarvis.observability import AuditWriteError, write_audit
from jarvis.storage.errors import StorageError
from jarvis.world_state import WorldError

if TYPE_CHECKING:
    from jarvis.core.cognitive_records import ModelRequest
    from jarvis.models import ModelRouter


class InitiativeEvaluator(Protocol):
    """Legacy Phase 1 proposal-source interface, retained for adapter compatibility."""

    def suggest(
        self, session: Session, observations: tuple[Observation, ...]
    ) -> tuple[ActionProposal, ...]: ...


# Trusted server catalog: an ID, adapter, risk and simulation declaration must agree.
# No real providers, external communications, or physical safety operations are wired.
SIMULATED_RISKS = {
    "development.simulated_notice": ("development.simulated_notice", "LOW"),
    "home.lighting.set_brightness": ("development.simulated_lighting", "MODERATE"),
}


class InitiativeError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class InitiativeStore(Protocol):
    """Private replaceable store. Application reads are exposed only by the engine."""

    identities: object
    audit: object

    def _bind(self) -> object: ...
    def _policy(self, policy_id, *, access): ...
    def _policies(self, principal_id, *, access): ...
    def _configure(self, policy, expected_revision, audit, *, access): ...
    def _cursor(self, policy_id, *, access): ...
    def _get(self, initiative_id, owner_id, *, access): ...
    def _list(self, owner_id, *, limit, active=False, state=None, terms=None, access): ...
    def _accept(self, policy, event, sequence, expected_cursor, record, session_id, *, access): ...
    def _execution_limits(self, record, policy, now, *, access): ...
    def _save(self, old, new, session_id, *, access): ...
    def _claim(self, initiative_id, *, access): ...
    def _history(self, initiative_id, owner_id, *, limit, access): ...


class ModelInitiativeGenerator(Protocol):
    """Future advisory boundary using Phase 5 ModelRequest. Not enabled in this milestone.

    Implementations must call ModelRouter (including egress) and return strictly decoded
    InitiativeCandidate intent. They cannot assign identity, evidence, risk or approval.
    """

    async def suggest(
        self, request: "ModelRequest", *, router: "ModelRouter", session_token: str
    ) -> InitiativeCandidate: ...


class RuleInitiativeGenerator:
    def suggest(self, policy, state):
        return InitiativeCandidate(capability_id=policy.capability_id, arguments=policy.arguments)


def is_initiative_question(text):
    text = text.casefold()
    return any(
        term in text
        for term in (
            "initiative",
            "approve",
            "approval",
            "awaiting",
            "recommendation",
            "why did",
            "why didn't",
            "what evidence",
            "flag",
            "already been resolved",
        )
    )


def describe_initiatives(records):
    if not records:
        return (
            "No currently authorized initiative records are available. "
            "Conversation text cannot approve actions."
        )
    lines = []
    for r in records[:3]:
        lines.append(
            f"Initiative {r.initiative_id}: {r.state}; decision={r.reason_code}; "
            f"approval={r.approval_status or 'not required'}; "
            f"verified task={r.task_state or 'none'}, result={r.result_status or 'none'}. "
            f"Current evidence={r.current_evidence_status}. Original snapshot: {r.explanation} "
            f"Evidence: {', '.join(str(i) for i in r.evidence_ids[:2])}. "
            "All capabilities here are simulated. Approval requires the "
            "trusted inbox interface."
        )
    return "\n".join(lines)


class InitiativeEngine:
    def __init__(
        self,
        store: InitiativeStore,
        *,
        permissions,
        world,
        memory,
        registry,
        policy,
        tasks,
        clock=utc_now,
        generator=None,
    ):
        identities = store.identities
        if any(
            i is not identities
            for i in (
                permissions.identities,
                world.identities,
                memory._identities,
                policy.identities,
                tasks.identities,
            )
        ):
            raise ValueError("initiative services must share trusted identity state")
        self.identities, self.permissions, self.audit = identities, permissions, store.audit
        self.world, self.memory, self.registry, self.policy, self.tasks = (
            world,
            memory,
            registry,
            policy,
            tasks,
        )
        self._store, self._access = store, store._bind()
        self.clock, self.generator = clock, generator or RuleInitiativeGenerator()
        tasks.register_execution_guard("initiative", self._task_guard)

    @contextmanager
    def _request(self, token, action):
        session = None
        try:
            with self.identities.transaction():
                session = self.identities.resolve_session(token)
                if session.principal_id is None or session.authentication_ref is None:
                    raise InitiativeError("unauthenticated")
                try:
                    yield session
                except (StorageError, WorldError, MemoryError) as exc:
                    raise InitiativeError(exc.code) from None
                except (ValueError, TypeError, AttributeError, KeyError, ArgumentValidationError):
                    raise InitiativeError("initiative_validation_failure") from None
                except AuditWriteError:
                    raise InitiativeError("audit_unavailable") from None
        except IdentityError as exc:
            raise InitiativeError(exc.reason_code) from None
        except InitiativeError as exc:
            try:
                write_audit(
                    self.audit,
                    AuditRecord(
                        principal_id=session.principal_id if session else None,
                        session_id=session.session_id if session else None,
                        correlation_id=uuid4(),
                        action=action,
                        outcome="denied",
                        reason_code=exc.code,
                    ),
                )
            except (StorageError, AuditWriteError):
                raise InitiativeError("audit_unavailable") from None
            raise

    def _require(self, session, token, names, corr):
        capability = Capability(
            capability_id="initiative.policy",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            required_permissions=names,
            adapter_ref="initiative.service",
        )
        proposal = ActionProposal(
            session_id=session.session_id,
            capability_id=capability.capability_id,
            correlation_id=corr,
            origin="initiative",
            source_ref="jarvis.initiatives",
        )
        decision = self.policy.evaluate(token, proposal, capability)
        if decision.decision != "allow":
            raise InitiativeError("initiative_access_denied")
        return decision.policy_refs

    def configure(self, value, *, session_token, expected_revision=None):
        with self._request(session_token, "initiative.configure") as session:
            self._require(session, session_token, ("initiatives.manage",), uuid4())
            if session.principal_id != self.identities.owner_id:
                raise InitiativeError("owner_required")
            value = decode_initiative(value if isinstance(value, str) else value.model_dump_json())
            if not isinstance(value, InitiativePolicy):
                raise InitiativeError("initiative_validation_failure")
            if expected_revision is not None and (
                type(expected_revision) is not int or expected_revision < 0
            ):
                raise InitiativeError("initiative_validation_failure")
            principal = self.identities.get_principal(value.principal_id)
            if principal is None or principal.status != "active":
                raise InitiativeError("unknown_authority")
            if value.capability_id:
                capability, risk = self._capability(value.capability_id, value.arguments)
                if value.mode == "AUTO_EXECUTE" and risk != "LOW":
                    raise InitiativeError("automatic_risk_denied")
            # Registration routes are trusted Phase 6 records. Administration is owner-scoped.
            source = self.world._store._source(value.source_id, access=self.world._access)
            if source is None or source.access_scope.owner_id != session.principal_id:
                raise InitiativeError("source_unavailable")
            if (
                value.entity_id not in source.entities
                or value.location != source.location
                or value.principal_id
                not in (source.access_scope.owner_id, *source.access_scope.shared_with)
            ):
                raise InitiativeError("source_scope_denied")
            return self._store._configure(
                value,
                expected_revision,
                AuditRecord(
                    principal_id=session.principal_id,
                    session_id=session.session_id,
                    correlation_id=uuid4(),
                    action="initiative.configure",
                    outcome="succeeded",
                    reason_code="policy_configured",
                    policy_refs=(f"initiative:{value.policy_id}:{value.revision}",),
                ),
                access=self._access,
            )

    def policies(self, *, session_token):
        with self._request(session_token, "initiative.policies") as session:
            self._require(session, session_token, ("initiatives.read",), uuid4())
            return self._store._policies(session.principal_id, access=self._access)

    def pause(self, policy_id, *, session_token, expected_revision):
        with self._request(session_token, "initiative.pause"):
            policy = self._store._policy(policy_id, access=self._access)
            if policy is None:
                raise InitiativeError("initiative_unavailable")
            return self.configure(
                policy.model_copy(update={"enabled": False, "revision": expected_revision + 1}),
                session_token=session_token,
                expected_revision=expected_revision,
            )

    def _capability(self, capability_id, arguments):
        capability = self.registry.get(capability_id)
        declaration = SIMULATED_RISKS.get(capability_id)
        if declaration is None or declaration[0] != capability.adapter_ref:
            raise InitiativeError("capability_not_simulated")
        if capability.side_effect not in {"none", "local"}:
            raise InitiativeError("capability_not_simulated")
        try:
            validate_capability_arguments(capability, arguments)
        except ArgumentValidationError:
            raise InitiativeError("arguments_invalid") from None
        return capability, declaration[1]

    def _authorized_action(self, record, session, token):
        action = record.proposal.action
        if action is None:
            return ()
        registered = self.registry._resolve_for_executor(action.capability_id)
        self._capability(action.capability_id, action.arguments)
        current = action.model_copy(update={"session_id": session.session_id})
        decision = self.policy.evaluate(
            token, current, registered.capability, access_scope=registered.access_scope
        )
        if decision.decision != "allow":
            raise InitiativeError("capability_permission_revoked")
        return decision.policy_refs

    def _policy_for(self, record):
        p = record.proposal
        policy = self._store._policy(p.policy_id, access=self._access)
        if (
            policy is None
            or not policy.enabled
            or policy.revision != p.policy_revision
            or policy.principal_id != record.owner_id
        ):
            raise InitiativeError("autonomy_policy_revoked")
        if policy.allowed_hours_utc and self.clock().hour not in policy.allowed_hours_utc:
            raise InitiativeError("time_restricted")
        return policy

    def _evidence(self, state, policy):
        now = self.clock()
        status = state.status in (
            {"observed", "inferred"} if policy.allow_inferred else {"observed"}
        )
        if (
            not status
            or state.value is not policy.expected_value
            or state.confidence < policy.min_confidence
            or state.expires_at <= now
            or state.last_observed_at > now
            or (now - state.last_observed_at).total_seconds() > policy.max_age_seconds
        ):
            raise InitiativeError("evidence_not_current")

    def _revalidate(self, record, session, token, *, execution=False):
        p = record.proposal
        policy = self._policy_for(record)
        if self.clock() >= p.expires_at:
            raise InitiativeError("initiative_expired")
        self._require(
            session,
            token,
            ("initiatives.evaluate",) + (("initiatives.execute",) if execution else ()),
            record.correlation_id,
        )
        for ref in p.world_refs:
            state = self.world.get(ref.state_id, session_token=token)
            if state.revision != ref.revision:
                raise InitiativeError("evidence_changed")
            self._evidence(state, policy)
        for memory_id in p.memory_ids:
            self.memory.get(memory_id, session_token=token)
        source = self.world._store._source(p.trigger.source_id, access=self.world._access)
        if (
            source is None
            or not source.enabled
            or source.principal_id != p.trigger.source_principal_id
        ):
            raise InitiativeError("source_unavailable")
        self._authorized_action(record, session, token)
        if execution:
            if policy.mode == "OBSERVE_ONLY":
                raise InitiativeError("observe_only")
            if policy.mode == "AUTO_EXECUTE":
                self._require(
                    session, token, (f"initiatives.auto:{policy.policy_id}",), record.correlation_id
                )
                _, risk = self._capability(p.action.capability_id, p.action.arguments)
                if risk != "LOW" or p.risk != "LOW":
                    raise InitiativeError("automatic_risk_denied")
            else:
                approval = record.approval
                if (
                    approval is None
                    or approval.state not in {"APPROVED", "CONSUMED"}
                    or approval.approving_principal_id != record.owner_id
                    or approval.authentication_source is None
                ):
                    raise InitiativeError("approval_required")
                if approval.expires_at <= self.clock():
                    raise InitiativeError("approval_expired")
                names = (
                    *p.required_permissions,
                    "initiatives.approve",
                    "initiatives.evaluate",
                    "initiatives.execute",
                )
                refs = tuple(
                    sorted(
                        g.policy_ref
                        for g in self.permissions.current_grants(
                            session.principal_id, tuple(dict.fromkeys(names))
                        )
                    )
                )
                if refs != approval.grant_refs:
                    raise InitiativeError("approval_invalidated")
        return policy

    def _new(self, event, sequence, policy, session, token):
        if type(event.schema_version) is not int or event.schema_version != 1:
            raise InitiativeError("event_version_unsupported")
        eligible = {
            "perception.person_detected",
            "sensor.occupancy_changed",
            "sensor.motion_detected",
            "device.disconnected",
            "device.connected",
        }
        if event.event_type not in eligible or event.source != policy.source_id:
            return None
        if (
            type(event.payload.get("schema_version")) is not int
            or event.payload["schema_version"] != 1
        ):
            raise InitiativeError("event_version_unsupported")
        state = next(
            (
                s
                for s in self.world.query(
                    session_token=token,
                    entity=policy.entity_id,
                    location=policy.location,
                    attr=policy.attribute,
                    limit=20,
                )
                if UUID(event.payload["observation_id"]) in s.evidence_ids
            ),
            None,
        )
        if (
            state is not None
            and state.status in {"observed", "inferred"}
            and state.value is not policy.expected_value
        ):
            return None
        if state is None:
            return None  # Out-of-order/old/foreign evidence is never promoted into current truth.
        source = self.world._store._source(event.source, access=self.world._access)
        if source is None or event.principal_id != source.principal_id:
            raise InitiativeError("event_source_invalid")
        candidate = decode_initiative(self.generator.suggest(policy, state).model_dump_json())
        if (
            not isinstance(candidate, InitiativeCandidate)
            or candidate.capability_id != policy.capability_id
            or candidate.arguments != policy.arguments
        ):
            raise InitiativeError("candidate_not_allowlisted")
        now = self.clock()
        action, risk, permissions = None, "INFORMATIONAL", ()
        if candidate.capability_id:
            cap, risk = self._capability(candidate.capability_id, candidate.arguments)
            permissions = cap.required_permissions
            action = ActionProposal(
                session_id=session.session_id,
                capability_id=cap.capability_id,
                arguments=candidate.arguments,
                correlation_id=event.correlation_id,
                origin="initiative",
                source_ref="jarvis.initiatives",
            )
        # Small keyword memory query, scoped by the existing service. No extraction or instructions.
        try:
            memories = self.memory.search(
                MemoryQuery(text=policy.entity_id, limit=3), session_token=token
            ).records
        except MemoryError as exc:
            if exc.code != "access_denied":
                raise
            memories = ()
        dedup = hashlib.sha256(
            json.dumps(
                [
                    policy.policy_id,
                    str(policy.principal_id),
                    policy.source_id,
                    policy.entity_id,
                    policy.attribute,
                    policy.expected_value,
                    candidate.capability_id,
                    candidate.arguments,
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        proposal = InitiativeProposal(
            owner_id=policy.principal_id,
            target_id=policy.target_id,
            policy_id=policy.policy_id,
            policy_revision=policy.revision,
            entity_id=policy.entity_id,
            location=policy.location,
            attribute=policy.attribute,
            expected_value=policy.expected_value,
            trigger=InitiativeTrigger(
                event=event,
                sequence=sequence,
                source_id=source.source_id,
                source_principal_id=source.principal_id,
            ),
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            action=action,
            evidence_ids=state.evidence_ids,
            world_refs=(WorldReference(state_id=state.state_id, revision=state.revision),),
            memory_ids=tuple(m.memory_id for m in memories),
            confidence=state.confidence,
            observed_at=state.last_observed_at,
            evidence_expires_at=state.expires_at,
            evidence_status=state.status,
            risk=risk,
            required_permissions=permissions,
            approval_required=policy.mode == "APPROVAL_REQUIRED",
            deduplication_key=dedup,
            created_at=now,
            expires_at=now + timedelta(seconds=policy.expires_seconds),
            explanation=f"{policy.entity_id} {policy.attribute}={state.value}; "
            f"evidence is {state.status}, strength={state.confidence}. "
            f"Source {source.source_id}; no personal identity is inferred.",
        )
        return InitiativeRecord(
            initiative_id=proposal.initiative_id,
            owner_id=policy.principal_id,
            correlation_id=event.correlation_id,
            proposal=proposal,
            proposal_hash=proposal_hash(proposal),
            decision=InitiativeDecision(
                mode=policy.mode,
                disposition="observe",
                reason_code="detected",
                policy_refs=(f"initiative:{policy.policy_id}:{policy.revision}",),
            ),
            created_at=now,
            updated_at=now,
            audit_id=uuid4(),
        )

    def _change(self, record, session, state, reason, *, disposition=None, **updates):
        decision = record.decision.model_copy(
            update={"reason_code": reason, **({"disposition": disposition} if disposition else {})}
        )
        new = record.model_copy(
            update={
                "state": state,
                "revision": record.revision + 1,
                "updated_at": self.clock(),
                "audit_id": uuid4(),
                "decision": decision,
                **updates,
            }
        )
        return self._store._save(record, new, session.session_id, access=self._access)

    def process(self, *, session_token, limit=20, after_accept=None, execute=True):
        if type(limit) is not int or not 1 <= limit <= 100 or type(execute) is not bool:
            raise InitiativeError("initiative_validation_failure")
        accepted = []
        with self._request(session_token, "initiative.process") as session:
            self._require(session, session_token, ("initiatives.evaluate",), uuid4())
            for policy in self._store._policies(session.principal_id, access=self._access):
                if not policy.enabled:
                    continue
                cursor = self._store._cursor(policy.policy_id, access=self._access)
                page = self.world.events(
                    session_token=session_token, limit=limit, after_sequence=cursor
                )
                for sequence, event in page:
                    record = self._new(event, sequence, policy, session, session_token)
                    record = self._store._accept(
                        policy,
                        event,
                        sequence,
                        cursor,
                        record,
                        session.session_id,
                        access=self._access,
                    )
                    cursor = sequence
                    if record:
                        if after_accept:
                            after_accept(
                                record
                            )  # Trusted crash-test hook; durable acceptance precedes it.
                        accepted.append(record.initiative_id)
        for initiative_id in accepted:
            self.evaluate(initiative_id, session_token=session_token, execute=execute)
        return tuple(self.get(i, session_token=session_token) for i in accepted)

    def _load(self, initiative_id, session):
        record = self._store._get(initiative_id, session.principal_id, access=self._access)
        if record is None:
            raise InitiativeError("initiative_unavailable")
        return record

    def get(self, initiative_id, *, session_token):
        with self._request(session_token, "initiative.get") as session:
            self._require(session, session_token, ("initiatives.read",), uuid4())
            return self._load(initiative_id, session)

    def list(self, *, session_token, limit=20, active=False):
        if type(limit) is not int or not 1 <= limit <= 100 or type(active) is not bool:
            raise InitiativeError("initiative_validation_failure")
        with self._request(session_token, "initiative.list") as session:
            self._require(session, session_token, ("initiatives.read",), uuid4())
            return self._store._list(
                session.principal_id, limit=limit, active=active, access=self._access
            )

    def history(self, initiative_id, *, session_token, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InitiativeError("initiative_validation_failure")
        with self._request(session_token, "initiative.history") as session:
            self._require(session, session_token, ("initiatives.read",), uuid4())
            self._load(initiative_id, session)
            return self._store._history(
                initiative_id, session.principal_id, limit=limit, access=self._access
            )

    def _blocked(self, record, session, code):
        if record.state in TERMINAL:
            return record
        approval = record.approval
        if approval and approval.state in {"PENDING", "APPROVED", "CONSUMED"}:
            approval = approval.model_copy(
                update={"state": "EXPIRED" if "expired" in code else "INVALIDATED"}
            )
        execution = record.execution
        if execution is not None:
            task = self.tasks.store.get(execution.task_id)
            if task is not None and task.owner_id == record.owner_id:
                execution = execution.model_copy(
                    update={"task_state": task.state, "result": task.steps[0].result}
                )
        return self._change(
            record,
            session,
            "EXPIRED" if "expired" in code else "FAILED",
            code,
            disposition="deny",
            approval=approval,
            execution=execution,
        )

    def evaluate(self, initiative_id, *, session_token, execute=True):
        with self._store._claim(initiative_id, access=self._access):
            with self._request(session_token, "initiative.evaluate") as session:
                self._require(session, session_token, ("initiatives.evaluate",), uuid4())
                record = self._load(initiative_id, session)
                if record.state in TERMINAL:
                    return record
                if record.state == "DETECTED":
                    record = self._change(record, session, "EVALUATING", "evaluating")
                if record.state == "EVALUATING":
                    try:
                        policy = self._revalidate(record, session, session_token)
                    except (InitiativeError, WorldError, MemoryError) as exc:
                        return self._blocked(record, session, exc.code)
                    record = self._change(
                        record, session, "PROPOSED", "observe_only", disposition="observe"
                    )
                    if policy.mode == "APPROVAL_REQUIRED":
                        p = record.proposal
                        approval = InitiativeApproval(
                            initiative_id=record.initiative_id,
                            proposal_hash=record.proposal_hash,
                            capability_id=p.action.capability_id,
                            arguments=p.action.arguments,
                            created_at=self.clock(),
                            expires_at=p.expires_at,
                        )
                        record = self._change(
                            record,
                            session,
                            "AWAITING_APPROVAL",
                            "approval_required",
                            disposition="approval",
                            approval=approval,
                        )
                # Approval-required waits without destroying the pending request.
                if record.state == "AWAITING_APPROVAL" or record.decision.mode == "OBSERVE_ONLY":
                    try:
                        self._revalidate(record, session, session_token)
                    except (InitiativeError, WorldError, MemoryError) as exc:
                        return self._blocked(record, session, exc.code)
                    return record
        if execute:
            return self.execute(initiative_id, session_token=session_token)
        return record

    def approve(self, initiative_id, *, session_token, expected_revision=None):
        with self._store._claim(initiative_id, access=self._access):
            with self._request(session_token, "initiative.approve") as session:
                self._require(session, session_token, ("initiatives.approve",), uuid4())
                record = self._load(initiative_id, session)
                if expected_revision is not None and (
                    type(expected_revision) is not int or record.revision != expected_revision
                ):
                    raise InitiativeError("initiative_revision_conflict")
                if record.state != "AWAITING_APPROVAL" or record.approval.state != "PENDING":
                    raise InitiativeError("approval_replay_rejected")
                try:
                    self._revalidate(record, session, session_token)
                except (InitiativeError, WorldError, MemoryError) as exc:
                    return self._blocked(record, session, exc.code)
                names = tuple(
                    dict.fromkeys(
                        (
                            *record.proposal.required_permissions,
                            "initiatives.approve",
                            "initiatives.evaluate",
                            "initiatives.execute",
                        )
                    )
                )
                grants = self.permissions.current_grants(session.principal_id, names)
                if len(grants) != len(names):
                    return self._blocked(record, session, "approval_authority_denied")
                approval = record.approval.model_copy(
                    update={
                        "state": "APPROVED",
                        "approving_principal_id": session.principal_id,
                        "session_id": session.session_id,
                        "authentication_source": session.authentication_ref,
                        "grant_refs": tuple(sorted(g.policy_ref for g in grants)),
                        "decided_at": self.clock(),
                    }
                )
                return self._change(
                    record, session, "APPROVED", "approved_exact_proposal", approval=approval
                )

    def deny(self, initiative_id, *, session_token, expected_revision=None):
        return self.cancel(
            initiative_id,
            session_token=session_token,
            denied=True,
            expected_revision=expected_revision,
        )

    def cancel(self, initiative_id, *, session_token, denied=False, expected_revision=None):
        with self._store._claim(initiative_id, access=self._access):
            with self._request(session_token, "initiative.cancel") as session:
                self._require(session, session_token, ("initiatives.approve",), uuid4())
                record = self._load(initiative_id, session)
                if expected_revision is not None and (
                    type(expected_revision) is not int or record.revision != expected_revision
                ):
                    raise InitiativeError("initiative_revision_conflict")
                if record.state in TERMINAL:
                    return record
                if record.execution:
                    task = self.tasks.cancel(record.execution.task_id, session_token=session_token)
                    if task.state in {"COMPLETED", "NEEDS_REVIEW"}:
                        return self._outcome(record, session, task, recovery=True)
                    if task.state != "CANCELLED":
                        return record
                approval = record.approval
                if approval:
                    approval = approval.model_copy(
                        update={
                            "state": "DENIED" if denied else "REVOKED",
                            "approving_principal_id": session.principal_id,
                            "session_id": session.session_id,
                            "authentication_source": session.authentication_ref,
                            "decided_at": self.clock(),
                        }
                    )
                return self._change(
                    record,
                    session,
                    "CANCELLED",
                    "denied" if denied else "cancelled",
                    approval=approval,
                )

    def _task_guard(self, task, token):
        # This guard also protects callers using ordinary TaskManager.run/recover directly.
        try:
            with self._request(token, "initiative.task_guard") as session:
                initiative_id = UUID(task.execution_guard_ref.split(":", 1)[1])
                record = self._load(initiative_id, session)
                if (
                    record.state != "EXECUTING"
                    or record.execution is None
                    or record.execution.task_id != task.task_id
                    or len(task.steps) != 1
                    or task.owner_id != record.owner_id
                    or task.correlation_id != record.correlation_id
                    or task.steps[0].capability_id != record.proposal.action.capability_id
                    or task.steps[0].arguments != record.proposal.action.arguments
                ):
                    raise InitiativeError("task_initiative_binding_invalid")
                policy = self._revalidate(record, session, token, execution=True)
                if record.execution.admitted_at is None:
                    self._store._execution_limits(record, policy, self.clock(), access=self._access)
                    self._change(
                        record,
                        session,
                        "EXECUTING",
                        "execution_admitted",
                        execution=record.execution.model_copy(update={"admitted_at": self.clock()}),
                    )
        except (InitiativeError, WorldError, MemoryError) as exc:
            raise StorageError(exc.code) from None

    def _outcome(self, record, session, task, *, recovery=False):
        # A trusted guard can commit admission during TaskManager invocation.
        record = self._load(record.initiative_id, session)
        result = task.steps[0].result
        execution = InitiativeExecutionRecord(
            task_id=task.task_id,
            task_state=task.state,
            result=result,
            recovery=recovery,
            admitted_at=record.execution.admitted_at if record.execution else None,
        )
        states = {
            "COMPLETED": "COMPLETED",
            "FAILED": "FAILED",
            "CANCELLED": "CANCELLED",
            "NEEDS_REVIEW": "NEEDS_REVIEW",
        }
        return self._change(
            record,
            session,
            states.get(task.state, "EXECUTING"),
            "verified_success"
            if task.state == "COMPLETED"
            else task.error.code
            if task.error
            else "task_pending",
            disposition="execute",
            execution=execution,
        )

    def execute(self, initiative_id, *, session_token, after_checkpoint=None):
        with self._store._claim(initiative_id, access=self._access):
            with self._request(session_token, "initiative.execute") as session:
                self._require(session, session_token, ("initiatives.execute",), uuid4())
                record = self._load(initiative_id, session)
                if record.state in TERMINAL:
                    return record
                if record.state not in {"PROPOSED", "APPROVED", "EXECUTING"}:
                    raise InitiativeError("approval_required")
                previous = (
                    self.tasks.store.get(record.execution.task_id) if record.execution else None
                )
                # Preserve known outcomes; no new handler call is needed even if policy has changed.
                if previous and previous.state in {
                    "COMPLETED",
                    "NEEDS_REVIEW",
                    "FAILED",
                    "CANCELLED",
                }:
                    return self._outcome(record, session, previous, recovery=True)
                if previous and all(step.state == "COMPLETED" for step in previous.steps):
                    # A crash after the last result checkpoint only needs task finalization.
                    run = self.tasks.run(
                        previous.task_id, session_token=session_token, recovery=True
                    )
                    if run.task:
                        return self._outcome(record, session, run.task, recovery=True)
                    raise InitiativeError(run.error.code if run.error else "task_failed")
                # Ambiguous attempts must be classified by the existing runner before cancellation.
                if previous and any(
                    s.state in {"RUNNING", "OUTCOME_UNKNOWN"} for s in previous.steps
                ):
                    run = self.tasks.run(
                        previous.task_id,
                        session_token=session_token,
                        recovery=True,
                        after_checkpoint=after_checkpoint,
                    )
                    if run.task and run.task.state in {
                        "NEEDS_REVIEW",
                        "COMPLETED",
                        "FAILED",
                        "CANCELLED",
                    }:
                        return self._outcome(record, session, run.task, recovery=True)
                try:
                    self._revalidate(record, session, session_token, execution=True)
                except (InitiativeError, WorldError, MemoryError) as exc:
                    if previous:
                        self.tasks.cancel(previous.task_id, session_token=session_token)
                    if exc.code in {"approval_required", "observe_only"}:
                        raise InitiativeError(exc.code) from None
                    return self._blocked(record, session, exc.code)
                if record.execution is None:
                    p = record.proposal
                    task = self.tasks.submit(
                        (
                            TaskStep(
                                step_id="initiative",
                                capability_id=p.action.capability_id,
                                arguments=p.action.arguments,
                            ),
                        ),
                        session_token=session_token,
                        correlation_id=record.correlation_id,
                        idempotency_key=f"initiative:{record.initiative_id}",
                        execution_guard_ref=f"initiative:{record.initiative_id}",
                    )
                    approval = record.approval
                    if approval:
                        approval = approval.model_copy(update={"state": "CONSUMED"})
                    record = self._change(
                        record,
                        session,
                        "EXECUTING",
                        "task_submitted",
                        disposition="execute",
                        approval=approval,
                        execution=InitiativeExecutionRecord(
                            task_id=task.task_id, task_state=task.state
                        ),
                    )
                run = self.tasks.run(
                    record.execution.task_id,
                    session_token=session_token,
                    recovery=record.state == "EXECUTING",
                    after_checkpoint=after_checkpoint,
                )
                if run.task:
                    return self._outcome(record, session, run.task, recovery=run.task.attempt > 1)
                if run.status == "busy":
                    return self._load(initiative_id, session)
                if run.error and run.error.code in {"execution_rate_limit", "execution_cooldown"}:
                    self.tasks.cancel(record.execution.task_id, session_token=session_token)
                    record = self._load(initiative_id, session)
                    return self._blocked(record, session, run.error.code)
                raise InitiativeError(run.error.code if run.error else "task_failed")

    def recover(self, *, session_token, limit=100):
        records = self.list(session_token=session_token, limit=limit, active=True)
        return tuple(self.evaluate(r.initiative_id, session_token=session_token) for r in records)

    def context(self, text, *, session_token, limit=3):
        if not is_initiative_question(text):
            return ()
        if type(limit) is not int or not 1 <= limit <= 3:
            raise InitiativeError("initiative_validation_failure")
        words = list(dict.fromkeys(re.findall(r"[a-z0-9][a-z0-9_.:-]{2,95}", text.casefold())))[:32]
        words = [w.rstrip(".:-") for w in words]
        words = [w for w in words if len(w) >= 3]
        generic = set(
            (
                "what which why did didn you jarvis actions action are await awaiting "
                "pending approval "
                "approved approve initiatives initiative exist evidence caused that this "
                "recommendation recommendations flag the already been resolved has issue owner "
                "ignore permission policies permissions everything restart automatically "
                "when my for in and"
            ).split()
        )
        terms = tuple(w for w in words if w not in generic)
        with self._request(session_token, "initiative.context") as session:
            self._require(session, session_token, ("initiatives.read",), uuid4())
            records = self._store._list(
                session.principal_id,
                limit=limit,
                state="AWAITING_APPROVAL"
                if any(w in {"await", "awaiting", "pending"} for w in words)
                else None,
                terms=terms or None,
                access=self._access,
            )
        contexts = []
        for r in records:
            try:
                states = tuple(
                    self.world.get(ref.state_id, session_token=session_token)
                    for ref in r.proposal.world_refs
                )
            except WorldError:
                continue  # Revoked world access cannot leak derived facts into a model.
            current = ",".join(s.status for s in states)
            if any(
                s.revision != ref.revision
                for s, ref in zip(states, r.proposal.world_refs, strict=True)
            ):
                current += ":changed_since_proposal"
            contexts.append(
                InitiativeContext(
                    initiative_id=r.initiative_id,
                    revision=r.revision,
                    state=r.state,
                    explanation=r.proposal.explanation,
                    reason_code=r.decision.reason_code,
                    approval_status=r.approval.state if r.approval else None,
                    task_state=r.execution.task_state if r.execution else None,
                    result_status=r.execution.result.status
                    if r.execution and r.execution.result
                    else None,
                    evidence_ids=r.proposal.evidence_ids,
                    current_evidence_status=current,
                    world_refs=tuple(
                        WorldReference(state_id=s.state_id, revision=s.revision) for s in states
                    ),
                )
            )
        return tuple(contexts)
