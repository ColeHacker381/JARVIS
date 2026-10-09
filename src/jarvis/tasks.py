"""Task state store boundary; scheduler, transitions, and recovery are deferred."""

from typing import Protocol
from uuid import UUID

from jarvis.core.contracts import Task


class TaskStore(Protocol):
    def save(self, task: Task) -> None: ...

    def get(self, task_id: UUID) -> Task | None: ...
