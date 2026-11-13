"""Provisional v1 schemas. See docs/FOUNDATION.md for scope and trust boundaries."""

import json
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from jarvis.core.time import utc_now

Name = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")]
Confidence = Annotated[float, Field(ge=0, le=1)]


class Contract(BaseModel):
    """Strict construction, unknown-field rejection, and explicit serialized versions.

    Frozen fields prevent reassignment, but nested JSON payloads are not deep-frozen.
    Contract instances and IDs are data, never proof of authentication/authorization.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
    schema_version: Literal[1] = 1

    @field_validator("schema_version", mode="before")
    @classmethod
    def supported_version(cls, value: object) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("only integer schema_version 1 is supported")
        return value


class Provenance(Contract):
    category: Literal["user_statement", "observation", "capability_result", "derived", "import"]
    source_ref: Name
    derived_from: tuple[Name, ...] = ()
    model_ref: Name | None = None

    @model_validator(mode="after")
    def derived_has_evidence(self) -> Self:
        if self.category == "derived" and not self.derived_from:
            raise ValueError("derived content requires evidence references")
        return self


class AccessScope(Contract):
    """A provisional data label, not an access-control implementation."""

    owner_id: UUID
    kind: Literal["private", "shared"] = "private"
    shared_with: tuple[UUID, ...] = ()
    policy_ref: Name | None = None

    @model_validator(mode="after")
    def explicit_sharing(self) -> Self:
        if self.kind == "private" and self.shared_with:
            raise ValueError("private scope cannot declare shared recipients")
        if self.kind == "shared" and not self.shared_with:
            raise ValueError("shared scope requires explicit recipients")
        return self


class Principal(Contract):
    contract_type: Literal["principal"] = "principal"
    principal_id: UUID = Field(default_factory=uuid4)
    display_name: Name
    kind: Literal["owner", "user", "guest"] = "user"
    status: Literal["active", "disabled"] = "active"
    relationship_ref: Name | None = None
    policy_refs: tuple[Name, ...] = ()


class Session(Contract):
    contract_type: Literal["session"] = "session"
    session_id: UUID = Field(default_factory=uuid4)
    principal_id: UUID | None = None
    authentication_ref: Name | None = None
    interface: Name
    started_at: AwareDatetime = Field(default_factory=utc_now)
    expires_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_lifetime(self) -> Self:
        if self.principal_id is None and self.authentication_ref is not None:
            raise ValueError("unknown principal cannot carry authentication evidence")
        for timestamp in (self.expires_at, self.ended_at):
            if timestamp is not None and timestamp < self.started_at:
                raise ValueError("session end/expiry precedes start")
        return self


class Event(Contract):
    contract_type: Literal["event"] = "event"
    event_id: UUID = Field(default_factory=uuid4)
    event_type: Name
    occurred_at: AwareDatetime = Field(default_factory=utc_now)
    correlation_id: UUID
    causation_id: UUID | None = None
    principal_id: UUID | None = None
    source: Name
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class Capability(Contract):
    contract_type: Literal["capability"] = "capability"
    capability_id: Name
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]
    required_permissions: tuple[Name, ...] = ()
    side_effect: Literal["none", "local", "external"] = "none"
    adapter_ref: Name

    @model_validator(mode="after")
    def side_effect_requires_permission(self) -> Self:
        if self.side_effect != "none" and not self.required_permissions:
            raise ValueError("side-effecting capabilities require permissions")
        return self


class ActionProposal(Contract):
    """Untrusted intent: deliberately cannot carry a policy decision or authorization."""

    contract_type: Literal["action_proposal"] = "action_proposal"
    proposal_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    task_id: UUID | None = None
    capability_id: Name
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    correlation_id: UUID
    origin: Literal["user", "model", "initiative", "rule"]
    source_ref: Name
    created_at: AwareDatetime = Field(default_factory=utc_now)


class ErrorDetail(Contract):
    code: Name
    message: Name
    retryable: bool = False


class Task(Contract):
    contract_type: Literal["task"] = "task"
    task_id: UUID = Field(default_factory=uuid4)
    owner_id: UUID
    state: Literal["pending", "running", "succeeded", "failed", "cancelled"] = "pending"
    correlation_id: UUID
    current_step: Name | None = None
    input_refs: tuple[Name, ...] = ()
    output_refs: tuple[Name, ...] = ()
    attempt: Annotated[int, Field(ge=0)] = 0
    idempotency_key: Name | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def valid_state(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("task update precedes creation")
        if (self.state == "failed") != (self.error is not None):
            raise ValueError("only failed tasks must include an error")
        return self


class Memory(Contract):
    contract_type: Literal["memory"] = "memory"
    memory_id: UUID = Field(default_factory=uuid4)
    kind: Literal["interaction", "person", "task", "episode", "semantic"]
    subject_ids: tuple[UUID, ...] = ()
    content: dict[str, JsonValue]
    source: Provenance
    access_scope: AccessScope
    consent_ref: Name | None = None
    retention_ref: Name | None = None
    deletion_state: Literal["active", "tombstoned"] = "active"
    confidence: Confidence | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_interval(self) -> Self:
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_to < self.valid_from:
                raise ValueError("memory validity end precedes start")
        return self


class Observation(Contract):
    contract_type: Literal["observation"] = "observation"
    observation_id: UUID = Field(default_factory=uuid4)
    source: Provenance
    observed_at: AwareDatetime
    recorded_at: AwareDatetime = Field(default_factory=utc_now)
    payload: dict[str, JsonValue]
    confidence: Confidence | None = None
    evidence_kind: Literal["observed", "inferred", "unknown"] = "observed"
    access_scope: AccessScope

    @model_validator(mode="after")
    def inference_has_provenance(self) -> Self:
        if self.evidence_kind == "inferred" and self.source.category != "derived":
            raise ValueError("inferred observations require derived provenance")
        if self.evidence_kind == "observed" and self.source.category == "derived":
            raise ValueError("derived evidence cannot be labeled directly observed")
        return self


class PolicyDecision(Contract):
    """Decision data must come from a trusted policy adapter, never a model response."""

    contract_type: Literal["policy_decision"] = "policy_decision"
    decision_id: UUID = Field(default_factory=uuid4)
    proposal_id: UUID
    session_id: UUID
    principal_id: UUID | None
    correlation_id: UUID
    decision: Literal["allow", "deny"]
    reason_code: Name
    policy_refs: tuple[Name, ...]
    decided_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def allow_has_policy_basis(self) -> Self:
        if self.decision == "allow" and (self.principal_id is None or not self.policy_refs):
            raise ValueError("allow requires a known principal and explicit policy basis")
        return self


class AuditRecord(Contract):
    """Payload-free trace; callers must not put sensitive arguments in action/reason."""

    contract_type: Literal["audit_record"] = "audit_record"
    audit_id: UUID = Field(default_factory=uuid4)
    occurred_at: AwareDatetime = Field(default_factory=utc_now)
    principal_id: UUID | None
    session_id: UUID | None = None
    proposal_id: UUID | None = None
    task_id: UUID | None = None
    decision_id: UUID | None = None
    correlation_id: UUID
    action: Name
    outcome: Literal["allowed", "denied", "succeeded", "failed", "cancelled"]
    reason_code: Name
    policy_refs: tuple[Name, ...] = ()


class ExecutionResult(Contract):
    contract_type: Literal["execution_result"] = "execution_result"
    proposal_id: UUID
    task_id: UUID | None = None
    correlation_id: UUID
    status: Literal[
        "success", "denied", "validation_failure", "execution_failure", "timeout", "cancelled"
    ]
    output: dict[str, JsonValue] | None = None
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def consistent_result(self) -> Self:
        if self.status == "success":
            if self.error is not None:
                raise ValueError("successful result cannot contain an error")
        elif self.error is None or self.output is not None:
            raise ValueError("unsuccessful result requires an error and cannot contain output")
        return self


CONTRACT_TYPES: dict[str, type[Contract]] = {
    model.model_fields["contract_type"].default: model
    for model in (
        Principal,
        Session,
        Event,
        Capability,
        ActionProposal,
        Task,
        Memory,
        Observation,
        PolicyDecision,
        AuditRecord,
        ExecutionResult,
    )
}


def decode_contract(serialized: str) -> Contract:
    """External/persistent boundary: never assume a missing or unsupported version."""
    data = json.loads(serialized)
    from jarvis.core.audio_records import AUDIO_TYPES, decode_audio

    if (
        isinstance(data, dict)
        and isinstance(data.get("contract_type"), str)
        and data["contract_type"] in AUDIO_TYPES
    ):
        return decode_audio(serialized)
    if not isinstance(data, dict):
        raise ValueError("contract must be a JSON object")
    if isinstance(data.get("contract_type"), str) and (
        data["contract_type"] == "notification" or data["contract_type"].startswith("notification_")
    ):
        from jarvis.core.notification_records import decode_notification

        return decode_notification(serialized)
    if isinstance(data.get("contract_type"), str) and data["contract_type"].startswith(
        "initiative_"
    ):
        from jarvis.core.initiative_records import decode_initiative

        return decode_initiative(serialized)
    if data.get("contract_type") in ("observation_input", "observation_source", "world_state") or (
        data.get("contract_type") == "observation" and data.get("schema_version") == 2
    ):
        from jarvis.core.world_records import decode_world

        return decode_world(serialized)
    if data.get("contract_type") in (
        "model_request",
        "cognitive_decision",
        "cognitive_response",
        "conversation",
        "conversation_turn",
    ):
        from jarvis.core.cognitive_records import decode_cognitive

        return decode_cognitive(serialized)
    if (
        data.get("contract_type") == "task"
        and type(data.get("schema_version")) is int
        and data["schema_version"] == 2
    ):
        from jarvis.core.task_records import decode_task

        return decode_task(serialized)
    if (
        data.get("contract_type") == "memory"
        and type(data.get("schema_version")) is int
        and data["schema_version"] == 2
    ):
        from jarvis.core.memory_records import decode_memory

        return decode_memory(serialized)
    if data.get("contract_type") == "memory_search_result":
        from jarvis.core.memory_records import MemorySearchResult, decode_memory

        if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise ValueError("explicit search result version 1 required")
        for record in data.get("records", []):
            decode_memory(json.dumps(record))
        cursor = data.get("next_cursor")
        if cursor is not None and (
            not isinstance(cursor, dict)
            or type(cursor.get("schema_version")) is not int
            or cursor["schema_version"] != 1
        ):
            raise ValueError("explicit cursor version 1 required")
        return MemorySearchResult.model_validate_json(serialized)
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("explicit integer schema_version 1 is required")
    kind = data.get("contract_type")
    if not isinstance(kind, str) or kind not in CONTRACT_TYPES:
        raise ValueError("unknown or missing contract_type")
    return CONTRACT_TYPES[kind].model_validate_json(serialized)
