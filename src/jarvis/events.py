"""Event store boundary; durable implementation is Phase 3."""

from typing import Protocol
from uuid import UUID

from jarvis.core.contracts import Event


class EventStore(Protocol):
    def append(self, event: Event) -> None: ...

    def by_correlation(self, correlation_id: UUID) -> tuple[Event, ...]: ...
