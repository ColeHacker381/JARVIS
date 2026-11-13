"""Provisional v1 cognitive/conversation contracts; intent never carries authority."""

import json
import types
from typing import Annotated, Literal, Self, Union, get_args, get_origin
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from jarvis.core.contracts import (
    ActionProposal,
    Capability,
    Confidence,
    Contract,
    ErrorDetail,
    ExecutionResult,
    Name,
)
from jarvis.core.initiative_records import InitiativeContext, InitiativeReference
from jarvis.core.notification_records import NotificationContext, NotificationReference
from jarvis.core.task_records import TaskState
from jarvis.core.time import utc_now
from jarvis.core.world_records import WorldContext, WorldReference

Text = Annotated[str, Field(min_length=1, max_length=8192)]


class ModelMessage(Contract):
    role: Literal["system", "developer", "user", "assistant"]
    content: Text


class MemoryContext(Contract):
    memory_id: UUID
    revision: Annotated[int, Field(ge=0)]
    content: dict[str, JsonValue]
    source_ref: Name
    source_category: Name
    confidence: Confidence | None = None
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    status: Literal["current"] = "current"


class IntentInput(Contract):
    contract_type: Literal["intent_input"] = "intent_input"
    intent_index: Annotated[int, Field(ge=0, le=3)]
    text: Annotated[str, Field(min_length=1, max_length=4096)]
    action_requested: bool = False


class ModelRequest(Contract):
    contract_type: Literal["model_request"] = "model_request"
    request_id: UUID
    session_id: UUID
    correlation_id: UUID
    messages: Annotated[tuple[ModelMessage, ...], Field(min_length=1, max_length=43)]
    memories: Annotated[tuple[MemoryContext, ...], Field(max_length=10)] = ()
    capabilities: Annotated[tuple[Capability, ...], Field(max_length=32)] = ()
    tasks: Annotated[tuple["TaskContext", ...], Field(max_length=3)] = ()
    world_states: Annotated[tuple[WorldContext, ...], Field(max_length=5)] = ()
    initiatives: Annotated[tuple[InitiativeContext, ...], Field(max_length=3)] = ()
    notifications: Annotated[tuple[NotificationContext, ...], Field(max_length=3)] = ()
    max_output_tokens: Annotated[int, Field(ge=64, le=4096)] = 1024
    intent_inputs: Annotated[tuple[IntentInput, ...], Field(max_length=4)] = ()


class TaskContext(Contract):
    task_id: UUID
    state: TaskState
    updated_at: AwareDatetime


ModelRequest.model_rebuild()


class ModelIdentity(Contract):
    provider_id: Name
    model_id: Name
    location: Literal["fake", "local", "cloud"]
    destination: Name


class ModelProvenance(ModelIdentity):
    invoked_at: AwareDatetime = Field(default_factory=utc_now)
    duration_ms: Annotated[int, Field(ge=0)] = 0


class CognitiveDecision(Contract):
    contract_type: Literal["cognitive_decision"] = "cognitive_decision"
    kind: Literal[
        "direct",
        "memory_grounded",
        "world_grounded",
        "action",
        "clarification",
        "unsupported",
        "failure",
    ]
    text: Annotated[str, Field(min_length=1, max_length=4096)]
    action: ActionProposal | None = None
    memory_ids: Annotated[tuple[UUID, ...], Field(max_length=10)] = ()
    world_ids: Annotated[tuple[UUID, ...], Field(max_length=5)] = ()
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.kind == "action") != (self.action is not None):
            raise ValueError("only action decisions carry one ActionProposal")
        if (self.kind == "failure") != (self.error is not None):
            raise ValueError("only failures carry an error")
        if self.kind == "memory_grounded" and not self.memory_ids:
            raise ValueError("grounded responses require memory references")
        if self.kind == "world_grounded" and not self.world_ids:
            raise ValueError("world replies require evidence references")
        if self.kind != "world_grounded" and self.world_ids:
            raise ValueError("world IDs require world-grounded decision")
        if self.kind == "direct" and self.memory_ids:
            raise ValueError("use memory_grounded for memory references")
        return self


class PlannedIntent(CognitiveDecision):
    contract_type: Literal["planned_intent"] = "planned_intent"
    intent_index: Annotated[int, Field(ge=0, le=3)]
    purpose: Literal["conversation", "memory", "reasoning", "action", "clarification"]
    text: Annotated[str, Field(min_length=1, max_length=1200)]

    @model_validator(mode="after")
    def purpose_binding(self) -> Self:
        if (self.purpose == "action") != (self.action is not None):
            raise ValueError("intent action purpose mismatch")
        if (
            self.kind in {"unsupported", "clarification", "failure"}
            and self.purpose != "clarification"
        ):
            raise ValueError("unresolved intents require clarification purpose")
        return self

    def decision(self) -> CognitiveDecision:
        return CognitiveDecision.model_validate(
            self.model_dump(exclude={"contract_type", "intent_index", "purpose"})
        )


class IntentPlan(Contract):
    contract_type: Literal["intent_plan"] = "intent_plan"
    intents: Annotated[tuple[PlannedIntent, ...], Field(min_length=2, max_length=4)]

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if tuple(i.intent_index for i in self.intents) != tuple(range(len(self.intents))):
            raise ValueError("intent indexes must be complete and ordered")
        return self


class CognitiveResponse(Contract):
    contract_type: Literal["cognitive_response"] = "cognitive_response"
    conversation_id: UUID
    request_id: UUID
    correlation_id: UUID
    kind: Literal[
        "direct",
        "memory_grounded",
        "world_grounded",
        "action",
        "clarification",
        "unsupported",
        "failure",
        "pending",
    ]
    text: Text
    provenance: ModelProvenance | None = None
    memory_ids: tuple[UUID, ...] = ()
    world_refs: Annotated[tuple[WorldReference, ...], Field(max_length=105)] = ()
    initiative_refs: Annotated[tuple[InitiativeReference, ...], Field(max_length=63)] = ()
    notification_refs: Annotated[tuple[NotificationReference, ...], Field(max_length=63)] = ()
    task_id: UUID | None = None
    task_state: Name | None = None
    # ExecutionResult is the existing authoritative result envelope, not model text.
    execution: ExecutionResult | None = None
    error: ErrorDetail | None = None
    audit_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.kind == "failure" and self.error is None:
            raise ValueError("failures require a structured error")
        if self.kind == "action" and (
            self.execution is None
            or self.execution.status != "success"
            or self.task_state != "COMPLETED"
            or self.task_id is None
        ):
            # Large/sensitive success output may be omitted; use a task reference instead.
            if not (self.execution is None and self.task_state == "COMPLETED" and self.task_id):
                raise ValueError("action success requires an observed completed task")
        if self.execution and (
            self.execution.task_id != self.task_id
            or self.execution.correlation_id != self.correlation_id
        ):
            raise ValueError("response execution binding mismatch")
        return self


class IntentOutcome(Contract):
    contract_type: Literal["intent_outcome"] = "intent_outcome"
    intent_index: Annotated[int, Field(ge=0, le=3)]
    purpose: Literal["conversation", "memory", "reasoning", "action", "clarification"]
    response: CognitiveResponse


class ComposedResponse(CognitiveResponse):
    """New explicit envelope; existing single-decision response v1 is unchanged."""

    contract_type: Literal["composed_response"] = "composed_response"
    kind: Literal["composed"] = "composed"
    status: Literal["completed", "partial", "pending", "failed"]
    spoken_text: Text
    intents: Annotated[tuple[IntentOutcome, ...], Field(min_length=2, max_length=4)]

    @model_validator(mode="after")
    def outcome_bindings(self) -> Self:
        if self.task_id is not None or self.task_state is not None or self.execution is not None:
            raise ValueError("composed tasks belong to individual outcomes")
        if tuple(i.intent_index for i in self.intents) != tuple(range(len(self.intents))):
            raise ValueError("composed outcome indexes mismatch")
        if any(
            i.response.conversation_id != self.conversation_id
            or i.response.correlation_id != self.correlation_id
            for i in self.intents
        ):
            raise ValueError("composed outcome identity mismatch")
        return self


CognitiveReply = Annotated[
    CognitiveResponse | ComposedResponse, Field(discriminator="contract_type")
]


class Conversation(Contract):
    contract_type: Literal["conversation"] = "conversation"
    conversation_id: UUID = Field(default_factory=uuid4)
    owner_id: UUID
    created_at: AwareDatetime = Field(default_factory=utc_now)


class ConversationTurn(Contract):
    contract_type: Literal["conversation_turn"] = "conversation_turn"
    request_id: UUID
    conversation_id: UUID
    owner_id: UUID
    session_id: UUID
    correlation_id: UUID = Field(default_factory=uuid4)
    user_text: Text
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)
    revision: Annotated[int, Field(ge=0)] = 0
    state: Literal["RECEIVED", "DECIDED", "ACTION_PENDING", "COMPLETED", "FAILED"] = "RECEIVED"
    decision: CognitiveDecision | None = None
    plan: IntentPlan | None = None
    intent_inputs: Annotated[tuple[IntentInput, ...], Field(max_length=4)] = ()
    parent_request_id: UUID | None = None
    provenance: ModelProvenance | None = None
    # All context dependencies, including those inherited through recent history.
    memory_ids: tuple[UUID, ...] = ()
    world_refs: Annotated[tuple[WorldReference, ...], Field(max_length=105)] = ()
    initiative_refs: Annotated[tuple[InitiativeReference, ...], Field(max_length=63)] = ()
    notification_refs: Annotated[tuple[NotificationReference, ...], Field(max_length=63)] = ()
    task_id: UUID | None = None
    response: CognitiveReply | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("turn update precedes creation")
        if self.decision is not None and self.plan is not None:
            raise ValueError("a turn cannot carry both a decision and plan")
        if self.plan is not None and len(self.plan.intents) != len(self.intent_inputs):
            raise ValueError("plan input count mismatch")
        if (
            self.state in {"DECIDED", "ACTION_PENDING", "COMPLETED"}
            and self.decision is None
            and self.plan is None
        ):
            raise ValueError("decided turns require validated intent")
        if self.state == "ACTION_PENDING" and self.task_id is None:
            raise ValueError("pending actions require a durable task")
        if (self.state in {"COMPLETED", "FAILED"}) != (self.response is not None):
            raise ValueError("only finished turns carry a final response")
        if self.response and (
            self.response.conversation_id != self.conversation_id
            or self.response.request_id != self.request_id
            or self.response.correlation_id != self.correlation_id
        ):
            raise ValueError("turn response binding mismatch")
        if self.response and (
            self.response.task_id != self.task_id
            or self.response.provenance != self.provenance
            or not set(self.response.memory_ids) <= set(self.memory_ids)
            or not set(self.response.world_refs) <= set(self.world_refs)
            or not set(self.response.initiative_refs) <= set(self.initiative_refs)
            or not set(self.response.notification_refs) <= set(self.notification_refs)
        ):
            raise ValueError("turn outcome/provenance binding mismatch")
        return self


COGNITIVE_TYPES = {
    model.model_fields["contract_type"].default: model
    for model in (
        ModelRequest,
        CognitiveDecision,
        CognitiveResponse,
        Conversation,
        ConversationTurn,
        IntentPlan,
        PlannedIntent,
        ComposedResponse,
        IntentInput,
        IntentOutcome,
    )
}


def decode_cognitive(serialized: str) -> Contract:
    data = json.loads(serialized)

    if (
        not isinstance(data, dict)
        or not isinstance(data.get("contract_type"), str)
        or data["contract_type"] not in COGNITIVE_TYPES
    ):
        raise ValueError("unknown cognitive contract")
    model = COGNITIVE_TYPES[data["contract_type"]]
    explicit_versions(data, model)
    return model.model_validate_json(serialized)


def explicit_versions(value, model):
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int:
        raise ValueError("explicit cognitive version required")
    if value["schema_version"] != 1:
        raise ValueError("unsupported cognitive version")
    for name, field in model.model_fields.items():
        nested = value.get(name)
        if nested is None:
            continue
        # Only declared contracts; content/argument objects are data, not envelopes.
        annotation = field.annotation
        origin = get_origin(annotation)
        if origin in (types.UnionType, Union):
            candidates = [a for a in get_args(annotation) if a is not type(None)]
            expanded = []
            for candidate in candidates:
                if get_origin(candidate) is Annotated:
                    candidate = get_args(candidate)[0]
                if get_origin(candidate) in (types.UnionType, Union):
                    expanded.extend(get_args(candidate))
                else:
                    expanded.append(candidate)
            candidates = expanded
            annotation = next(
                (
                    a
                    for a in candidates
                    if isinstance(a, type)
                    and issubclass(a, Contract)
                    and isinstance(nested, dict)
                    and "contract_type" in a.model_fields
                    and a.model_fields["contract_type"].default == nested.get("contract_type")
                ),
                candidates[0],
            )
            origin = get_origin(annotation)
        if origin is tuple:
            item_type = get_args(annotation)[0]
            if isinstance(item_type, type) and issubclass(item_type, Contract):
                if not isinstance(nested, list):
                    raise ValueError("contract array required")
                for item in nested:
                    explicit_versions(item, item_type)
        elif isinstance(annotation, type) and issubclass(annotation, Contract):
            explicit_versions(nested, annotation)
