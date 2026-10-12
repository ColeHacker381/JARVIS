"""Authenticated private text history; conversation IDs are references, not credentials."""

import re
from contextlib import contextmanager
from typing import Protocol
from uuid import UUID

from jarvis.action_reporting import reported_turn
from jarvis.core.cognitive_records import Conversation, ConversationTurn
from jarvis.core.contracts import AuditRecord
from jarvis.core.time import utc_now
from jarvis.identity import IdentityError
from jarvis.observability import write_audit

_SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bauthorization\s*:\s*bearer\s+\S+|"
    r"\b(?:api[_ -]?key|password|secret|access[_ -]?token)[\"']?\s*[:=]\s*[\"']?\S+|"
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})",
    re.I,
)


def contains_secret(text: str) -> bool:
    """Conservative common-secret guard, not a universal secret classifier."""
    return bool(_SECRET.search(text))


class ConversationError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ConversationStore(Protocol):
    identities: object
    audit: object

    def _bind(self) -> object: ...
    def _create(self, conversation, *, access) -> Conversation: ...
    def _get(self, conversation_id, owner_id, *, access) -> Conversation | None: ...
    def _recent(
        self, conversation_id, owner_id, limit, *, access
    ) -> tuple[ConversationTurn, ...]: ...
    def _begin(self, turn, *, access) -> tuple[ConversationTurn, bool]: ...
    def _save(self, turn, expected_revision, *, access) -> None: ...
    def _claim(self, conversation_id, *, access): ...


class ConversationService:
    def __init__(self, store: ConversationStore):
        self._store = store
        self.identities = store.identities
        self._audit = store.audit
        self._access = store._bind()

    @contextmanager
    def _authenticated(self, token):
        with self.identities.transaction():
            session = self.identities.resolve_session(token)
            if session.principal_id is None or session.authentication_ref is None:
                raise IdentityError("unauthenticated")
            yield session

    def _owned(self, conversation_id, session):
        record = self._store._get(conversation_id, session.principal_id, access=self._access)
        if record is None:
            write_audit(
                self._audit,
                AuditRecord(
                    principal_id=session.principal_id,
                    session_id=session.session_id,
                    correlation_id=conversation_id,
                    action="conversation.access",
                    outcome="denied",
                    reason_code="conversation_unavailable",
                ),
            )
            raise ConversationError("conversation_unavailable")
        return record

    def start(self, *, session_token: str) -> Conversation:
        with self._authenticated(session_token) as session:
            return self._store._create(
                Conversation(owner_id=session.principal_id), access=self._access
            )

    def get(self, conversation_id: UUID, *, session_token: str) -> Conversation:
        with self._authenticated(session_token) as session:
            return self._owned(conversation_id, session)

    def recent(self, conversation_id: UUID, *, session_token: str, limit=8):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ConversationError("history_limit_invalid")
        with self._authenticated(session_token) as session:
            self._owned(conversation_id, session)
            return tuple(
                reported_turn(turn)
                for turn in self._store._recent(
                    conversation_id, session.principal_id, limit, access=self._access
                )
            )

    def begin(self, conversation_id, request_id, text, *, session_token):
        if not isinstance(text, str) or not text.strip() or len(text) > 4096:
            raise ConversationError("request_text_invalid")
        if contains_secret(text) or getattr(
            self.identities, "contains_credential", lambda _: False
        )(text):
            raise ConversationError("sensitive_input_rejected")
        with self._authenticated(session_token) as session:
            self._owned(conversation_id, session)
            turn = ConversationTurn(
                request_id=request_id,
                conversation_id=conversation_id,
                owner_id=session.principal_id,
                session_id=session.session_id,
                user_text=text,
            )
            return self._store._begin(turn, access=self._access)

    def _save(self, turn, *, session_token, **changes):
        """Trusted coordinator only; no public mutation/import API for model decisions."""
        with self._authenticated(session_token) as session:
            self._owned(turn.conversation_id, session)
            if turn.owner_id != session.principal_id:
                raise ConversationError("conversation_unavailable")
            updated = ConversationTurn.model_validate(
                {
                    **turn.model_dump(),
                    **changes,
                    "revision": turn.revision + 1,
                    "updated_at": utc_now(),
                }
            ).model_copy(deep=True)
            if contains_secret(updated.model_dump_json()):
                raise ConversationError("sensitive_output_rejected")
            self._store._save(updated, turn.revision, access=self._access)
            return updated

    @contextmanager
    def claim(self, conversation_id, *, session_token):
        self.get(conversation_id, session_token=session_token)
        # No security lock spans model awaits. A separate crash-released local claim
        # serializes this conversation while allowing unrelated users to interact.
        with self._store._claim(conversation_id, access=self._access):
            yield
