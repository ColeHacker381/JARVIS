"""Future authorized retrieval boundary. No unscoped retrieval API is provided."""

from typing import Protocol

from jarvis.core.contracts import Memory, Session


class MemoryService(Protocol):
    def retrieve(self, session: Session, query: str) -> tuple[Memory, ...]:
        """Implementations MUST apply current policy before returning any records."""
        ...

    def write(self, session: Session, memory: Memory) -> None:
        """Implementations MUST authorize scope, consent, and data class before storage."""
        ...
