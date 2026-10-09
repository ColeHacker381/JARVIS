"""Provider-independent proposal interface; no execution/authorization methods."""

from typing import Protocol

from jarvis.core.contracts import ActionProposal, Session


class ProposalSource(Protocol):
    def propose(self, session: Session, request: str) -> tuple[ActionProposal, ...]: ...
