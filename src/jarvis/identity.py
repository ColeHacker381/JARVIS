"""Identity storage boundary; authentication adapters remain an open decision."""

from typing import Protocol
from uuid import UUID

from jarvis.core.contracts import Principal, Session


class IdentityRepository(Protocol):
    def get_principal(self, principal_id: UUID) -> Principal | None: ...

    def get_session(self, session_id: UUID) -> Session | None: ...
