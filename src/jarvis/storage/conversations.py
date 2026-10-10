"""Private scoped SQL conversation adapter; changes/events/audits commit together."""

from contextlib import contextmanager

from jarvis.conversations import ConversationError
from jarvis.core.cognitive_records import Conversation, ConversationTurn, decode_cognitive
from jarvis.core.contracts import AuditRecord, Event
from jarvis.storage.errors import StorageError
from jarvis.storage.sqlite import _append_audit, _append_event, _file_lock
from jarvis.tasks import TaskBusyError

_TRANSITIONS = {
    "RECEIVED": {"DECIDED", "FAILED"},
    "DECIDED": {"ACTION_PENDING", "COMPLETED", "FAILED"},
    "ACTION_PENDING": {"ACTION_PENDING", "COMPLETED", "FAILED"},
    "COMPLETED": set(),
    "FAILED": set(),
}


class SQLiteConversationStore:
    def __init__(self, database, identities, audit):
        if identities.database.path != database.path or audit.database.path != database.path:
            raise ValueError("conversation adapters must share the authority database")
        self.database, self.identities, self.audit = database, identities, audit
        self.__access = None

    def _bind(self):
        if self.__access is not None:
            raise ValueError("conversation store is already bound")
        self.__access = object()
        return self.__access

    def _guard(self, access):
        if self.__access is None or access is not self.__access:
            raise ConversationError("conversation_access_denied")

    @staticmethod
    def _decode(row, model):
        try:
            record = decode_cognitive(row["data"])
            if not isinstance(record, model):
                raise ValueError()
            for key in row.keys():
                if key not in ("sequence", "data") and str(getattr(record, key)) != str(row[key]):
                    raise ValueError()
            return record
        except (ValueError, TypeError, AttributeError):
            raise StorageError("conversation_corrupt") from None

    @staticmethod
    def _journal(connection, record, action):
        correlation = getattr(record, "correlation_id", record.conversation_id)
        task_id = getattr(record, "task_id", None)
        _append_event(
            connection,
            Event(
                event_type=action,
                principal_id=record.owner_id,
                correlation_id=correlation,
                causation_id=getattr(record, "request_id", record.conversation_id),
                source="jarvis.conversations",
                payload={
                    "conversation_id": str(record.conversation_id),
                    "state": getattr(record, "state", "CREATED"),
                    "task_id": str(task_id) if task_id else None,
                },
            ),
        )
        _append_audit(
            connection,
            AuditRecord(
                principal_id=record.owner_id,
                session_id=getattr(record, "session_id", None),
                correlation_id=correlation,
                task_id=task_id,
                action=action,
                outcome="failed" if getattr(record, "state", None) == "FAILED" else "succeeded",
                reason_code=getattr(record, "state", "created").casefold(),
            ),
        )

    def _create(self, conversation, *, access):
        self._guard(access)
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO conversations VALUES(?,?,?)",
                (
                    str(conversation.conversation_id),
                    str(conversation.owner_id),
                    conversation.model_dump_json(),
                ),
            )
            self._journal(connection, conversation, "conversation.created")
        return conversation

    def _get(self, conversation_id, owner_id, *, access):
        self._guard(access)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id=? AND owner_id=?",
                (str(conversation_id), str(owner_id)),
            ).fetchone()
        return self._decode(row, Conversation) if row else None

    def _recent(self, conversation_id, owner_id, limit, *, access):
        self._guard(access)
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM conversation_turns WHERE conversation_id=? AND owner_id=? "
                "ORDER BY sequence DESC LIMIT ?",
                (str(conversation_id), str(owner_id), limit),
            ).fetchall()
        return tuple(self._decode(row, ConversationTurn) for row in reversed(rows))

    def _begin(self, turn, *, access):
        self._guard(access)
        with self.database.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM conversation_turns WHERE request_id=?", (str(turn.request_id),)
            ).fetchone()
            if row:
                if row["owner_id"] != str(turn.owner_id) or (
                    row["conversation_id"] != str(turn.conversation_id)
                ):
                    raise ConversationError("request_conflict")
                previous = self._decode(row, ConversationTurn)
                if previous.user_text != turn.user_text:
                    raise ConversationError("request_conflict")
                return previous, False
            connection.execute(
                "INSERT INTO conversation_turns(request_id,conversation_id,owner_id,"
                "correlation_id,created_at,revision,state,data) VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(turn.request_id),
                    str(turn.conversation_id),
                    str(turn.owner_id),
                    str(turn.correlation_id),
                    str(turn.created_at),
                    turn.revision,
                    turn.state,
                    turn.model_dump_json(),
                ),
            )
            self._journal(connection, turn, "conversation.received")
        return turn, True

    def _save(self, turn, expected_revision, *, access):
        self._guard(access)
        with self.database.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM conversation_turns WHERE request_id=? AND owner_id=?",
                (str(turn.request_id), str(turn.owner_id)),
            ).fetchone()
            if not row:
                raise ConversationError("conversation_unavailable")
            previous = self._decode(row, ConversationTurn)
            if previous.revision != expected_revision or turn.revision != expected_revision + 1:
                raise ConversationError("conversation_revision_conflict")
            for name in (
                "request_id",
                "conversation_id",
                "owner_id",
                "session_id",
                "correlation_id",
                "created_at",
                "user_text",
            ):
                if getattr(previous, name) != getattr(turn, name):
                    raise ConversationError("immutable_turn")
            if turn.state not in _TRANSITIONS[previous.state]:
                raise ConversationError("turn_transition_invalid")
            if previous.decision is not None and previous.decision != turn.decision:
                raise ConversationError("immutable_decision")
            connection.execute(
                "UPDATE conversation_turns SET revision=?,state=?,data=? WHERE request_id=?",
                (turn.revision, turn.state, turn.model_dump_json(), str(turn.request_id)),
            )
            self._journal(connection, turn, "conversation." + turn.state.casefold())

    @contextmanager
    def _claim(self, conversation_id, *, access):
        self._guard(access)
        try:
            with _file_lock(
                self.database.path.with_name(
                    f"{self.database.path.name}.conversation-{conversation_id}.lock"
                )
            ):
                yield
        except TaskBusyError:
            raise ConversationError("conversation_busy") from None
