"""Provisional initiative v1. Trusted services assign authority and outcomes."""

import hashlib
import json
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from jarvis.core.contracts import ActionProposal, Confidence, Contract, Event, ExecutionResult, Name
from jarvis.core.time import utc_now
from jarvis.core.world_records import Key, WorldReference

Mode = Literal["OBSERVE_ONLY", "APPROVAL_REQUIRED", "AUTO_EXECUTE"]
Risk = Literal["INFORMATIONAL", "LOW", "MODERATE", "HIGH", "CRITICAL"]
State = Literal[
    "DETECTED",
    "EVALUATING",
    "PROPOSED",
    "SUPPRESSED",
    "AWAITING_APPROVAL",
    "APPROVED",
    "EXECUTING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "EXPIRED",
    "NEEDS_REVIEW",
]
TERMINAL = frozenset({"SUPPRESSED", "COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "NEEDS_REVIEW"})
TRANSITIONS = {
    "DETECTED": {"EVALUATING", "CANCELLED", "EXPIRED"},
    "EVALUATING": {"PROPOSED", "SUPPRESSED", "FAILED", "EXPIRED", "CANCELLED"},
    "PROPOSED": {"AWAITING_APPROVAL", "EXECUTING", "CANCELLED", "EXPIRED", "FAILED"},
    "AWAITING_APPROVAL": {"APPROVED", "CANCELLED", "EXPIRED", "FAILED"},
    "APPROVED": {"EXECUTING", "CANCELLED", "EXPIRED", "FAILED"},
    "EXECUTING": {"EXECUTING", "COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW", "EXPIRED"},
    **{state: set() for state in TERMINAL},
}


class InitiativePolicy(Contract):
    contract_type: Literal["initiative_policy"] = "initiative_policy"
    policy_id: Key
    principal_id: UUID
    target_id: UUID
    source_id: Key
    entity_id: Key
    location: Key
    attribute: Literal["occupancy", "motion", "online"]
    expected_value: bool
    mode: Mode = "OBSERVE_ONLY"
    enabled: bool = True
    capability_id: Name | None = None
    # Exact trusted argument allowlist, not model-produced constraints or expressions.
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    min_confidence: Confidence = 0.5
    allow_inferred: bool = False
    max_age_seconds: Annotated[int, Field(ge=1, le=86400)] = 60
    expires_seconds: Annotated[int, Field(ge=1, le=86400)] = 300
    cooldown_seconds: Annotated[int, Field(ge=1, le=86400)] = 60
    rate_limit: Annotated[int, Field(ge=1, le=100)] = 10
    rate_window_seconds: Annotated[int, Field(ge=1, le=86400)] = 3600
    max_active: Annotated[int, Field(ge=1, le=100)] = 10
    allowed_hours_utc: Annotated[
        tuple[Annotated[int, Field(ge=0, le=23)], ...], Field(max_length=24)
    ] = ()
    revision: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.target_id != self.principal_id:
            raise ValueError("v0 supports only authority-owned local initiatives")
        if self.mode != "OBSERVE_ONLY" and self.capability_id is None:
            raise ValueError("execution policies require an exact capability")
        if self.capability_id is None and self.arguments:
            raise ValueError("notification-only proposals have no arguments")
        if len(json.dumps(self.arguments)) > 4096:
            raise ValueError("arguments too large")
        return self


class InitiativeTrigger(Contract):
    contract_type: Literal["initiative_trigger"] = "initiative_trigger"
    event: Event
    sequence: Annotated[int, Field(ge=1)]
    source_id: Key
    source_principal_id: UUID


class InitiativeCandidate(Contract):
    contract_type: Literal["initiative_candidate"] = "initiative_candidate"
    # Advisory intent only: no principal, approval, risk, or execution claims.
    capability_id: Name | None = None
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class InitiativeProposal(Contract):
    contract_type: Literal["initiative_proposal"] = "initiative_proposal"
    initiative_id: UUID = Field(default_factory=uuid4)
    owner_id: UUID
    target_id: UUID
    policy_id: Key
    policy_revision: Annotated[int, Field(ge=0)]
    entity_id: Key
    location: Key
    attribute: Literal["occupancy", "motion", "online"]
    expected_value: bool
    trigger: InitiativeTrigger
    correlation_id: UUID
    causation_id: UUID
    action: ActionProposal | None = None
    evidence_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=64)]
    world_refs: Annotated[tuple[WorldReference, ...], Field(min_length=1, max_length=5)]
    memory_ids: Annotated[tuple[UUID, ...], Field(max_length=5)] = ()
    confidence: Confidence
    observed_at: AwareDatetime
    evidence_expires_at: AwareDatetime
    evidence_status: Literal[
        "observed", "inferred", "unknown", "conflicting", "stale", "unavailable"
    ]
    risk: Risk
    required_permissions: Annotated[tuple[Name, ...], Field(max_length=16)]
    approval_required: bool
    deduplication_key: Name
    explanation: Annotated[str, Field(min_length=1, max_length=2048)]
    created_at: AwareDatetime = Field(default_factory=utc_now)
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.owner_id != self.target_id or self.causation_id != self.trigger.event.event_id:
            raise ValueError("proposal identity/causation mismatch")
        if self.correlation_id != self.trigger.event.correlation_id:
            raise ValueError("correlation mismatch")
        if self.expires_at <= self.created_at:
            raise ValueError("invalid expiry")
        if self.action and (
            self.action.origin != "initiative"
            or self.action.task_id is not None
            or self.action.correlation_id != self.correlation_id
        ):
            raise ValueError("invalid initiative action")
        return self


class InitiativeDecision(Contract):
    contract_type: Literal["initiative_decision"] = "initiative_decision"
    mode: Mode
    disposition: Literal["observe", "approval", "execute", "suppress", "deny"]
    reason_code: Name
    policy_refs: tuple[Name, ...] = ()


class InitiativeApproval(Contract):
    contract_type: Literal["initiative_approval"] = "initiative_approval"
    approval_id: UUID = Field(default_factory=uuid4)
    initiative_id: UUID
    proposal_hash: Name
    capability_id: Name
    arguments: dict[str, JsonValue]
    state: Literal[
        "PENDING", "APPROVED", "DENIED", "EXPIRED", "INVALIDATED", "REVOKED", "CONSUMED"
    ] = "PENDING"
    approving_principal_id: UUID | None = None
    session_id: UUID | None = None
    authentication_source: Name | None = None
    # Exact grant IDs ensure revoke+regrant cannot resurrect an old approval.
    grant_refs: tuple[Name, ...] = ()
    created_at: AwareDatetime
    expires_at: AwareDatetime
    decided_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def consistent_approval(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("invalid approval lifetime")
        if self.state in {"APPROVED", "CONSUMED"} and (
            self.approving_principal_id is None
            or self.session_id is None
            or self.authentication_source is None
            or not self.grant_refs
            or self.decided_at is None
        ):
            raise ValueError("trusted approval provenance required")
        if self.state == "PENDING" and any(
            (
                self.approving_principal_id,
                self.session_id,
                self.authentication_source,
                self.grant_refs,
                self.decided_at,
            )
        ):
            raise ValueError("pending approval cannot import authority")
        return self


class InitiativeExecutionRecord(Contract):
    contract_type: Literal["initiative_execution_record"] = "initiative_execution_record"
    task_id: UUID
    task_state: Name
    result: ExecutionResult | None = None
    recovery: bool = False
    admitted_at: AwareDatetime | None = None


class InitiativeSuppressionRecord(Contract):
    contract_type: Literal["initiative_suppression_record"] = "initiative_suppression_record"
    suppression_id: UUID = Field(default_factory=uuid4)
    policy_id: Key
    principal_id: UUID
    event_id: UUID
    reason_code: Name
    deduplication_key: Name
    recorded_at: AwareDatetime


class InitiativeRecord(Contract):
    contract_type: Literal["initiative_record"] = "initiative_record"
    initiative_id: UUID
    owner_id: UUID
    correlation_id: UUID
    proposal: InitiativeProposal
    proposal_hash: Name
    state: State = "DETECTED"
    revision: Annotated[int, Field(ge=0)] = 0
    decision: InitiativeDecision
    approval: InitiativeApproval | None = None
    execution: InitiativeExecutionRecord | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    audit_id: UUID

    @model_validator(mode="after")
    def consistent(self) -> Self:
        p = self.proposal
        if (
            p.initiative_id != self.initiative_id
            or p.owner_id != self.owner_id
            or p.correlation_id != self.correlation_id
            or proposal_hash(p) != self.proposal_hash
        ):
            raise ValueError("immutable proposal binding mismatch")
        if self.updated_at < self.created_at:
            raise ValueError("invalid timestamps")
        if self.approval and (
            self.approval.initiative_id != self.initiative_id
            or self.approval.proposal_hash != self.proposal_hash
            or not p.action
            or self.approval.capability_id != p.action.capability_id
            or self.approval.arguments != p.action.arguments
        ):
            raise ValueError("approval binding mismatch")
        if self.state == "COMPLETED" and (
            self.execution is None
            or self.execution.task_state != "COMPLETED"
            or self.execution.result is None
            or self.execution.result.status != "success"
        ):
            raise ValueError("verified result required")
        if (
            self.execution
            and self.execution.result
            and (
                self.execution.result.task_id != self.execution.task_id
                or self.execution.result.correlation_id != self.correlation_id
            )
        ):
            raise ValueError("execution binding mismatch")
        return self


class InitiativeReference(Contract):
    initiative_id: UUID
    revision: Annotated[int, Field(ge=0)]


class InitiativeContext(Contract):
    initiative_id: UUID
    revision: Annotated[int, Field(ge=0)]
    state: State
    explanation: Annotated[str, Field(min_length=1, max_length=2048)]
    reason_code: Name
    approval_status: Name | None = None
    task_state: Name | None = None
    result_status: Name | None = None
    evidence_ids: Annotated[tuple[UUID, ...], Field(max_length=64)] = ()
    world_refs: Annotated[tuple[WorldReference, ...], Field(max_length=5)] = ()
    current_evidence_status: Name = "unavailable"


def proposal_hash(proposal):
    return hashlib.sha256(
        json.dumps(proposal.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


INITIATIVE_TYPES = {
    m.model_fields["contract_type"].default: m
    for m in (
        InitiativePolicy,
        InitiativeTrigger,
        InitiativeCandidate,
        InitiativeProposal,
        InitiativeDecision,
        InitiativeApproval,
        InitiativeExecutionRecord,
        InitiativeSuppressionRecord,
        InitiativeRecord,
    )
}


def decode_initiative(serialized):
    # Reuse the existing strict recursive cognitive v1 decoder, including nested contracts.
    from jarvis.core.cognitive_records import explicit_versions

    data = json.loads(serialized)
    if not isinstance(data, dict) or data.get("contract_type") not in INITIATIVE_TYPES:
        raise ValueError("unknown initiative contract")
    model = INITIATIVE_TYPES[data["contract_type"]]
    explicit_versions(data, model)
    return model.model_validate_json(serialized)
