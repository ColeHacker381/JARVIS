"""Provisional strict v1 HTTP envelopes; domain models remain framework-independent."""

import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, JsonValue, model_validator

from jarvis.core.cognitive_records import CognitiveReply, explicit_versions
from jarvis.core.contracts import Contract, ErrorDetail
from jarvis.core.memory_records import MemoryCursor
from jarvis.core.notification_records import Notification
from jarvis.core.world_records import ObservationInput, ObservationRecord


class RequestV1(Contract):
    # HTTP UUIDs are JSON strings; integer versions/revisions still reject coercion.
    model_config = ConfigDict(extra="forbid", frozen=True, strict=False, allow_inf_nan=False)
    schema_version: Literal[1]


class MessageRequest(RequestV1):
    request_id: UUID
    text: Annotated[str, Field(min_length=1, max_length=4096)]


class RevisionRequest(RequestV1):
    expected_revision: Annotated[int, Field(strict=True, ge=0)]


class MemorySearchRequest(RequestV1):
    text: Annotated[str, Field(max_length=512)] = ""
    kind: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    project_ref: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 20
    cursor: MemoryCursor | None = None

    @model_validator(mode="before")
    @classmethod
    def versions(cls, data):
        explicit_versions(data, cls)
        if data.get("cursor") is not None:
            data = {**data, "cursor": MemoryCursor.model_validate_json(json.dumps(data["cursor"]))}
        return data


class ObservationRequest(RequestV1):
    observation: ObservationInput

    @model_validator(mode="before")
    @classmethod
    def versions(cls, data):
        explicit_versions(data, cls)
        if "observation" not in data:
            return data
        return {
            **data,
            "observation": ObservationInput.model_validate_json(json.dumps(data["observation"])),
        }


class ObservationAcceptance(Contract):
    record: ObservationRecord
    replayed: bool


class ApiResponse[T](Contract):
    request_id: UUID
    data: T


class ApiFailure(Contract):
    request_id: UUID
    error: ErrorDetail


class HistoryEntry(Contract):
    request_id: UUID
    created_at: AwareDatetime
    user_text: str
    state: str
    response: CognitiveReply | None
    task_id: UUID | None


class NotificationPage(Contract):
    records: tuple[Notification, ...]
    next_cursor: int


class Health(Contract):
    state: Literal["STARTING", "READY", "DEGRADED", "STOPPING", "STOPPED", "FAILED"]
    live: bool
    ready: bool
    database_available: bool
    schema_compatible: bool
    model_available: bool
    scheduler_running: bool
    last_worker_success: AwareDatetime | None


class JobSummary(Contract):
    running: bool
    passes: int
    failures: int
    last_success: AwareDatetime | None
    duration_ms: float
    error: str | None


class BacklogSample(Contract):
    unfinished_tasks: int
    pending_deliveries: int
    task_sample_may_be_truncated: bool
    notification_sample_may_be_truncated: bool
    sample_limit: int


class OperationalStatus(Health):
    storage_schema: int
    startup_ms: float
    workers: int
    jobs: dict[str, JobSummary]
    metrics: list[tuple[str, float]]
    simulated_invocations: dict[str, int]
    owned_backlog_sample: BacklogSample


class TaskSummary(Contract):
    task_id: UUID
    correlation_id: UUID
    state: str
    revision: int
    completed_steps: int
    steps: tuple[dict[str, JsonValue], ...]

    @classmethod
    def from_task(cls, task):
        return cls(
            task_id=task.task_id,
            correlation_id=task.correlation_id,
            state=task.state,
            revision=task.revision,
            completed_steps=sum(s.state == "COMPLETED" for s in task.steps),
            steps=tuple(
                {
                    "step_id": s.step_id,
                    "state": s.state,
                    "result_status": s.result.status if s.result else None,
                    "error_code": s.result.error.code if s.result and s.result.error else None,
                }
                for s in task.steps
            ),
        )
