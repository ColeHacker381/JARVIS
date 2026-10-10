"""Provisional task v2: explicit sequential checkpoints; legacy Task v1 remains readable."""

import json
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, JsonValue, field_validator, model_validator

from jarvis.core.contracts import Contract, ExecutionResult, Name, Task
from jarvis.core.time import utc_now

TaskState = Literal[
    "CREATED", "READY", "RUNNING", "WAITING", "COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"
]
StepState = Literal["NOT_STARTED", "RUNNING", "COMPLETED", "FAILED", "OUTCOME_UNKNOWN"]
TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"})
TRANSITIONS = {
    "CREATED": {"READY", "CANCELLED"},
    "READY": {"RUNNING", "CANCELLED"},
    "RUNNING": {"RUNNING", "WAITING", "COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"},
    "WAITING": {"RUNNING", "CANCELLED", "NEEDS_REVIEW"},
    "COMPLETED": set(),
    "FAILED": set(),
    "CANCELLED": set(),
    "NEEDS_REVIEW": set(),
}


class StepAttempt(Contract):
    attempt_id: UUID = Field(default_factory=uuid4)
    proposal_id: UUID
    session_id: UUID
    idempotency_key: Name
    state: Literal["RUNNING", "COMPLETED", "FAILED", "OUTCOME_UNKNOWN"] = "RUNNING"
    started_at: AwareDatetime = Field(default_factory=utc_now)
    finished_at: AwareDatetime | None = None
    result: ExecutionResult | None = None
    retry_safe: bool
    capability_fingerprint: Name
    policy_refs: tuple[Name, ...] = ()

    @model_validator(mode="after")
    def consistent_attempt(self) -> Self:
        if (self.state == "RUNNING") != (self.finished_at is None):
            raise ValueError("only running attempts lack a finish time")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("attempt finish precedes start")
        if self.state in {"COMPLETED", "FAILED"} and self.result is None:
            raise ValueError("known outcomes require an execution result")
        if self.result is not None:
            if self.result.proposal_id != self.proposal_id:
                raise ValueError("attempt proposal binding mismatch")
            if (self.state == "COMPLETED") != (self.result.status == "success"):
                raise ValueError("attempt outcome mismatch")
        return self


class TaskStep(Contract):
    step_id: Name
    capability_id: Name
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    depends_on: tuple[Name, ...] = ()
    state: StepState = "NOT_STARTED"
    attempts: tuple[StepAttempt, ...] = ()
    result: ExecutionResult | None = None

    @model_validator(mode="after")
    def consistent_step(self) -> Self:
        if self.state == "NOT_STARTED" and (self.attempts or self.result is not None):
            raise ValueError("unstarted step cannot have execution history")
        if self.state != "NOT_STARTED" and not self.attempts:
            raise ValueError("started step requires an attempt")
        if self.attempts and self.state != self.attempts[-1].state:
            raise ValueError("step must match its latest attempt")
        if self.result != (self.attempts[-1].result if self.attempts else None):
            raise ValueError("step result must match its latest attempt")
        ids = [attempt.attempt_id for attempt in self.attempts]
        if len(set(ids)) != len(ids):
            raise ValueError("attempt identifiers must be unique")
        return self


class TaskRecord(Task):
    """The existing task envelope extended explicitly as v2, without guessing v1 plans."""

    schema_version: Literal[2] = 2
    state: TaskState = "CREATED"
    steps: Annotated[tuple[TaskStep, ...], Field(min_length=1)]
    revision: Annotated[int, Field(ge=0)] = 0
    authority_refs: tuple[Name, ...]
    cancel_requested: bool = False

    @field_validator("schema_version", mode="before")
    @classmethod
    def supported_version(cls, value: object) -> int:
        if type(value) is not int or value != 2:
            raise ValueError("only integer task schema_version 2 is supported")
        return value

    @model_validator(mode="after")
    def valid_state(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("task update precedes creation")
        needs_error = self.state in {"FAILED", "WAITING", "NEEDS_REVIEW"}
        if needs_error != (self.error is not None):
            raise ValueError("failure, waiting and review states require structured errors")
        if self.state == "WAITING" and not self.error.retryable:
            raise ValueError("waiting requires a retryable failure")
        if f"principal:{self.owner_id}" not in self.authority_refs:
            raise ValueError("task must retain its principal reference (never a credential)")
        seen = set()
        pending_seen = False
        for step in self.steps:
            if step.step_id in seen or any(dep not in seen for dep in step.depends_on):
                raise ValueError("steps need unique IDs and dependencies on earlier steps only")
            seen.add(step.step_id)
            if step.state == "COMPLETED" and pending_seen:
                raise ValueError("sequential checkpoints cannot skip an unfinished step")
            if step.state != "COMPLETED":
                pending_seen = True
            for attempt in step.attempts:
                if attempt.idempotency_key != f"task:{self.task_id}/step:{step.step_id}":
                    raise ValueError("attempt idempotency scope mismatch")
                if attempt.result is not None and (
                    attempt.result.task_id != self.task_id
                    or attempt.result.correlation_id != self.correlation_id
                ):
                    raise ValueError("execution result task/correlation binding mismatch")
        if self.state == "COMPLETED" and any(s.state != "COMPLETED" for s in self.steps):
            raise ValueError("completed task requires every checkpoint")
        if self.state in {"CREATED", "READY"} and any(s.attempts for s in self.steps):
            raise ValueError("unstarted tasks cannot contain execution attempts")
        if self.current_step is not None and self.current_step not in seen:
            raise ValueError("current step is unknown")
        attempts = [attempt.attempt_id for step in self.steps for attempt in step.attempts]
        if self.attempt != len(attempts) or len(set(attempts)) != len(attempts):
            raise ValueError("task attempt count/identifiers inconsistent")
        return self

    def transition(self, state: TaskState, **changes: object) -> "TaskRecord":
        if state not in TRANSITIONS[self.state]:
            raise ValueError(f"invalid task transition: {self.state} -> {state}")
        return TaskRecord.model_validate(
            {
                **self.model_dump(),
                **changes,
                "state": state,
                "revision": self.revision + 1,
                "updated_at": utc_now(),
            }
        )


def decode_task(serialized: str) -> TaskRecord:
    data = json.loads(serialized)
    if not isinstance(data, dict) or data.get("contract_type") != "task":
        raise ValueError("task envelope required")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 2:
        raise ValueError("explicit task version 2 required; v1 has no recoverable plan")
    # Persistent nested versions are explicit too; construction defaults are not wire migration.
    for step in data.get("steps", []):
        for record in [step, *step.get("attempts", [])]:
            if type(record.get("schema_version")) is not int or record["schema_version"] != 1:
                raise ValueError("explicit nested version 1 required")
            result = record.get("result")
            if result is not None and (
                type(result.get("schema_version")) is not int or result["schema_version"] != 1
            ):
                raise ValueError("unsupported execution result version")
    return TaskRecord.model_validate_json(serialized)
