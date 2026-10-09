"""Bounded initiative interface; continuous scanning is a later milestone."""

from typing import Protocol

from jarvis.core.contracts import ActionProposal, Observation, Session


class InitiativeEvaluator(Protocol):
    def suggest(
        self, session: Session, observations: tuple[Observation, ...]
    ) -> tuple[ActionProposal, ...]: ...
