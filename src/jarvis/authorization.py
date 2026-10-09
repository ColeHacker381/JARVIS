"""Trusted policy boundary. No grant model or privileged executor is implemented yet."""

from typing import Protocol

from jarvis.core.contracts import ActionProposal, Capability, PolicyDecision, Session


class PolicyEvaluator(Protocol):
    def evaluate(
        self, session: Session, proposal: ActionProposal, capability: Capability
    ) -> PolicyDecision: ...
