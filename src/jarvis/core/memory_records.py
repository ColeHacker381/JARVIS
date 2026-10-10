"""Explicit memory v2; v1 remains readable without inventing ownership or consent."""

import json
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from jarvis.core.contracts import AccessScope, Confidence, Contract, Memory, Name, Provenance


class MemoryScope(AccessScope):
    kind: Literal["private", "shared", "household"] = "private"

    @model_validator(mode="after")
    def household_is_explicit(self) -> Self:
        if len(set(self.shared_with)) != len(self.shared_with):
            raise ValueError("duplicate recipients")
        if self.owner_id in self.shared_with:
            raise ValueError("owner is implicit, not a recipient")
        if self.kind == "household" and (
            not self.shared_with or self.policy_ref != "policy:memory.household"
        ):
            raise ValueError("household requires explicit members and established policy")
        return self


class MemoryDraft(Contract):
    """Untrusted data, never creator identity, revisions, or proof of permission."""

    kind: Annotated[Name, StringConstraints(max_length=100)]
    content: dict[str, JsonValue] = Field(default_factory=dict)
    source: Provenance
    subject_ids: tuple[UUID, ...] = ()
    project_ref: Annotated[Name, StringConstraints(max_length=255)] | None = None
    access_scope: MemoryScope | None = None
    consent_ref: Name
    retention_ref: Name = "retention.manual"
    expires_at: AwareDatetime | None = None
    confidence: Confidence | None = None
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None

    @model_validator(mode="after")
    def truthful_provenance(self) -> Self:
        if self.source.category != "derived" and (
            self.source.derived_from or self.source.model_ref is not None
        ):
            raise ValueError("derived evidence/model provenance cannot claim direct testimony")
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("invalid validity interval")
        if len(json.dumps(self.content, ensure_ascii=False).encode()) > 65536:
            raise ValueError("memory payload exceeds 64 KiB")
        return self


class MemoryRecord(Memory):
    schema_version: Literal[2] = 2
    kind: Annotated[Name, StringConstraints(max_length=100)]
    access_scope: MemoryScope
    creator_id: UUID
    consent_ref: Name
    retention_ref: Name
    project_ref: Annotated[Name, StringConstraints(max_length=255)] | None = None
    revision: Annotated[int, Field(ge=0)] = 0
    updated_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    deleted_at: AwareDatetime | None = None
    superseded_at: AwareDatetime | None = None
    superseded_by: UUID | None = None
    supersedes: UUID | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def supported_version(cls, value: object) -> int:
        if type(value) is not int or value != 2:
            raise ValueError("only integer memory schema_version 2 is supported")
        return value

    @model_validator(mode="after")
    def lifecycle(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("update precedes creation")
        if (self.deletion_state == "tombstoned") != (self.deleted_at is not None):
            raise ValueError("tombstone needs deletion time")
        if (self.superseded_at is None) != (self.superseded_by is None):
            raise ValueError("supersession needs time and successor")
        if self.superseded_at is not None and self.valid_to is None:
            raise ValueError("superseded fact needs validity end")
        for timestamp in (self.deleted_at, self.superseded_at):
            if timestamp is not None and not self.created_at <= timestamp <= self.updated_at:
                raise ValueError("invalid lifecycle time")
        if self.memory_id in (self.supersedes, self.superseded_by):
            raise ValueError("self supersession")
        if self.source.category != "derived" and (
            self.source.derived_from or self.source.model_ref is not None
        ):
            raise ValueError("derived provenance cannot be direct testimony")
        return self


class MemoryCursor(Contract):
    created_at: AwareDatetime
    memory_id: UUID


class MemoryQuery(Contract):
    text: Annotated[str, StringConstraints(max_length=512)] = ""
    kind: Annotated[Name, StringConstraints(max_length=100)] | None = None
    subject_id: UUID | None = None
    project_ref: Annotated[Name, StringConstraints(max_length=255)] | None = None
    at: AwareDatetime | None = None
    historical: bool = False
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    cursor: MemoryCursor | None = None


class MemorySearchResult(Contract):
    contract_type: Literal["memory_search_result"] = "memory_search_result"
    records: Annotated[tuple[MemoryRecord, ...], Field(max_length=100)]
    next_cursor: MemoryCursor | None = None


def decode_memory(serialized: str) -> MemoryRecord:
    data = json.loads(serialized)
    if not isinstance(data, dict) or data.get("contract_type") != "memory":
        raise ValueError("memory envelope required")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 2:
        raise ValueError("explicit memory version 2 required")
    for name in ("source", "access_scope"):
        nested = data.get(name)
        if not isinstance(nested, dict) or type(nested.get("schema_version")) is not int:
            raise ValueError("explicit nested version required")
        if nested["schema_version"] != 1:
            raise ValueError("unsupported nested version")
    return MemoryRecord.model_validate_json(serialized)
