"""Observation adapter boundary; world-state inference/storage is not implemented."""

from typing import Protocol

from jarvis.core.contracts import Observation, Session


class ObservationSource(Protocol):
    def observe(self, session: Session) -> tuple[Observation, ...]:
        """Return only observations the session is authorized to consume."""
        ...
