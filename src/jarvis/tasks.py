"""Task persistence and checkpoint boundaries; no scheduler or SQL in the domain."""

from contextlib import AbstractContextManager
from typing import Protocol
from uuid import UUID

from jarvis.core.contracts import AuditRecord, Event
from jarvis.core.task_records import TaskRecord


class TaskConflictError(RuntimeError):
    pass


class TaskBusyError(TaskConflictError):
    pass


class TaskStore(Protocol):
    def create(self, task: TaskRecord, event: Event, audit: AuditRecord) -> TaskRecord: ...

    def save(
        self,
        task: TaskRecord,
        *,
        expected_revision: int,
        event: Event,
        audit: AuditRecord,
    ) -> None: ...

    def get(self, task_id: UUID) -> TaskRecord | None: ...

    def unfinished(self) -> tuple[TaskRecord, ...]: ...

    def history(self, task_id: UUID) -> tuple[TaskRecord, ...]: ...

    def claim(self, task_id: UUID) -> AbstractContextManager[None]: ...

    def request_cancel(self, task_id: UUID) -> None: ...

    def cancellation_requested(self, task_id: UUID) -> bool: ...
