"""Durable event repository boundary; ordered reads use committed insertion order."""

from typing import Protocol
from uuid import UUID

from jarvis.core.contracts import Event


class EventStore(Protocol):
    def append(self, event: Event) -> None: ...

    def get(self, event_id: UUID) -> Event | None: ...

    def read(
        self,
        *,
        event_type: str | None = None,
        correlation_id: UUID | None = None,
        after_sequence: int = 0,
    ) -> tuple[Event, ...]: ...

    def by_correlation(self, correlation_id: UUID) -> tuple[Event, ...]: ...
