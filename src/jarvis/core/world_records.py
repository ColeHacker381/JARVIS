"""Explicit evidence and projection contracts. IDs and scope labels are never credentials."""

import hashlib
import json
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, field_validator, model_validator

from jarvis.core.contracts import AccessScope, Confidence, Contract, Observation

Key = Annotated[str, Field(min_length=1, max_length=96, pattern=r"^[a-z0-9][a-z0-9_.:-]*$")]
ObservationType = Literal["person_detected", "occupancy", "motion", "availability"]


class SensorPayload(Contract):
    value: bool | None


class ObservationInput(Contract):
    contract_type: Literal["observation_input"] = "observation_input"
    observation_id: UUID = Field(default_factory=uuid4)
    event_id: UUID = Field(default_factory=uuid4)
    correlation_id: UUID = Field(default_factory=uuid4)
    source_id: Key  # A routing claim checked against the authenticated source registration.
    observation_type: ObservationType
    entity_id: Key
    location: Key
    subject_id: Key | None = None  # Not a principal identity or recognition result.
    observed_at: AwareDatetime
    confidence: Confidence
    payload: SensorPayload
    source_available: bool = True
    expires_at: AwareDatetime | None = None
    raw_evidence_refs: Annotated[tuple[Key, ...], Field(max_length=8)] = ()

    @model_validator(mode="after")
    def valid_time(self) -> Self:
        if self.expires_at is not None and self.expires_at <= self.observed_at:
            raise ValueError("expiration must follow observation")
        return self


class SourceRegistration(Contract):
    contract_type: Literal["observation_source"] = "observation_source"
    source_id: Key
    principal_id: UUID
    access_scope: AccessScope
    observation_types: Annotated[tuple[ObservationType, ...], Field(min_length=1, max_length=4)]
    entities: Annotated[tuple[Key, ...], Field(min_length=1, max_length=16)]
    location: Key
    ttl_seconds: Annotated[int, Field(ge=1, le=86400)] = 60
    reliability: Confidence = 1.0
    enabled: bool = True
    revision: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def bounded_scope(self) -> Self:
        if len(self.access_scope.shared_with) > 32:
            raise ValueError("too many source readers")
        if self.access_scope.policy_ref is not None and len(self.access_scope.policy_ref) > 256:
            raise ValueError("source policy reference too large")
        return self


class ObservationRecord(Observation):
    schema_version: Literal[2] = 2
    source_id: Key
    source_principal_id: UUID
    observation_type: ObservationType
    entity_id: Key
    location: Key
    subject_id: Key | None = None
    event_id: UUID
    correlation_id: UUID
    source_available: bool
    expires_at: AwareDatetime
    raw_evidence_refs: tuple[Key, ...] = ()
    confidence: Confidence
    evidence_kind: Literal["observed", "unknown"] = "observed"

    @field_validator("schema_version", mode="before")
    @classmethod
    def supported_version(cls, value):
        if type(value) is not int or value != 2:
            raise ValueError("explicit observation version 2 required")
        return value

    @model_validator(mode="after")
    def interval(self) -> Self:
        if self.recorded_at < self.observed_at or self.expires_at <= self.observed_at:
            raise ValueError("invalid observation interval")
        if set(self.payload) != {"schema_version", "value"}:
            raise ValueError("invalid sensor payload")
        SensorPayload.model_validate(self.payload)
        if self.source.category != "observation" or self.source.source_ref != self.source_id:
            raise ValueError("source provenance mismatch")
        if self.source_available and self.payload["value"] is not None:
            if self.evidence_kind != "observed":
                raise ValueError("direct evidence mislabeled")
        elif self.evidence_kind != "unknown":
            raise ValueError("missing evidence is unknown")
        return self


class WorldReference(Contract):
    state_id: UUID
    revision: Annotated[int, Field(ge=0)]


class WorldContext(Contract):
    """Minimum authorized projection supplied to a model, without ACL/principal metadata."""

    state_id: UUID
    entity_id: Key
    attribute: Literal["occupancy", "motion", "online"]
    location: Key
    value: bool | None
    status: Literal["observed", "inferred", "unknown", "conflicting", "stale", "unavailable"]
    confidence: Confidence
    evidence_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=64)]
    inference_ref: Literal["camera-presence-implies-possible-occupancy-v1"] | None = None
    last_observed_at: AwareDatetime
    updated_at: AwareDatetime
    expires_at: AwareDatetime
    evaluated_at: AwareDatetime
    revision: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.status in {"unknown", "conflicting", "stale", "unavailable"}:
            if self.value is not None or self.confidence != 0:
                raise ValueError("uncertain state cannot assert a current value")
        elif self.value is None:
            raise ValueError("supported state needs a value")
        if (self.status == "inferred") != (self.inference_ref is not None):
            raise ValueError("inference must have explicit rule provenance")
        if self.updated_at < self.last_observed_at:
            raise ValueError("invalid projection timestamps")
        return self


class WorldState(WorldContext):
    contract_type: Literal["world_state"] = "world_state"
    access_scope: AccessScope


def decode_world(serialized):
    data = json.loads(serialized)
    models = {
        "observation_input": ObservationInput,
        "observation_source": SourceRegistration,
        "observation": ObservationRecord,
        "world_state": WorldState,
    }
    model = models.get(data.get("contract_type")) if isinstance(data, dict) else None
    if model is None or type(data.get("schema_version")) is not int:
        raise ValueError("explicit supported contract required")
    version = 2 if model is ObservationRecord else 1
    if data["schema_version"] != version:
        raise ValueError("unsupported world contract")
    for key in ("payload", "source", "access_scope"):
        if key in data and isinstance(data[key], dict):
            if type(data[key].get("schema_version")) is not int or data[key]["schema_version"] != 1:
                raise ValueError("explicit nested version required")
    return model.model_validate_json(serialized)


def partition(scope):
    data = (str(scope.owner_id), scope.kind, sorted(map(str, scope.shared_with)), scope.policy_ref)
    return hashlib.sha256(json.dumps(data).encode()).hexdigest()


def attribute(kind):
    return {
        "person_detected": "occupancy",
        "occupancy": "occupancy",
        "motion": "motion",
        "availability": "online",
    }[kind]
