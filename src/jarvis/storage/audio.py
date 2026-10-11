"""Private source/session metadata adapter. No raw audio or ambient transcript persistence."""

from uuid import UUID

from jarvis.audio_frontend import AudioError
from jarvis.core.audio_records import AudioSource, VoiceSession
from jarvis.core.contracts import AuditRecord
from jarvis.storage.sqlite import _append_audit


class SQLiteAudioStore:
    def __init__(self, database, identities):
        self.database, self.identities = database, identities
        self.__access = object()

    def _bind(self):
        return self.__access

    def _check(self, access):
        if access is not self.__access:
            raise AudioError("audio_store_access_denied")

    def _source(self, source_id, owner_id, *, access):
        self._check(access)
        with self.database.transaction() as c:
            row = c.execute(
                "SELECT data FROM audio_sources WHERE source_id=? AND owner_id=?",
                (source_id, str(owner_id)),
            ).fetchone()
        if row is None:
            return None
        source = self._decode(AudioSource, row)
        if source.source_id != source_id or source.owner_id != owner_id:
            raise AudioError("audio_record_corrupt")
        return source

    @staticmethod
    def _decode(model, row):
        try:
            import json

            data = json.loads(row["data"])
            if data.get("schema_version") != 1 or type(data.get("schema_version")) is not int:
                raise ValueError()
            return model.model_validate_json(row["data"])
        except (ValueError, TypeError, KeyError):
            raise AudioError("audio_record_corrupt") from None

    def _register(self, source, session, *, access):
        self._check(access)
        with self.database.transaction(write=True) as c:
            old = c.execute(
                "SELECT owner_id FROM audio_sources WHERE source_id=?", (source.source_id,)
            ).fetchone()
            if old and old["owner_id"] != str(source.owner_id):
                raise AudioError("audio_source_owner_immutable")
            c.execute(
                "INSERT INTO audio_sources(source_id,owner_id,data) VALUES(?,?,?) "
                "ON CONFLICT(source_id) DO UPDATE SET data=excluded.data",
                (source.source_id, str(source.owner_id), source.model_dump_json()),
            )
            _append_audit(
                c,
                AuditRecord(
                    principal_id=session.principal_id,
                    session_id=session.session_id,
                    correlation_id=session.session_id,
                    action="audio.source.configure",
                    outcome="succeeded",
                    reason_code="source_policy_explicit",
                ),
            )

    def _session(self, session_id, owner_id, *, access):
        self._check(access)
        with self.database.transaction() as c:
            row = c.execute(
                "SELECT data FROM voice_sessions WHERE voice_session_id=? AND owner_id=?",
                (str(session_id), str(owner_id)),
            ).fetchone()
        if row is None:
            return None
        session = self._decode(VoiceSession, row)
        if session.voice_session_id != session_id or session.owner_id != owner_id:
            raise AudioError("audio_record_corrupt")
        return session

    def _sessions(self, owner_id, *, access):
        self._check(access)
        with self.database.transaction() as c:
            rows = c.execute(
                "SELECT data FROM voice_sessions WHERE owner_id=? "
                "ORDER BY voice_session_id LIMIT 16",
                (str(owner_id),),
            ).fetchall()
        return tuple(self._decode(VoiceSession, row) for row in rows)

    def _save_session(self, record, session, *, access):
        self._check(access)
        with self.database.transaction(write=True) as c:
            c.execute(
                "INSERT INTO voice_sessions(voice_session_id,owner_id,data) VALUES(?,?,?) "
                "ON CONFLICT(voice_session_id) DO UPDATE SET data=excluded.data "
                "WHERE owner_id=excluded.owner_id",
                (str(record.voice_session_id), str(record.owner_id), record.model_dump_json()),
            )
            _append_audit(
                c,
                AuditRecord(
                    principal_id=session.principal_id,
                    session_id=session.session_id,
                    correlation_id=record.voice_session_id,
                    action="audio.session.state",
                    outcome="succeeded",
                    reason_code="voice_session_" + record.state.casefold(),
                ),
            )

    def _delete_session(self, session_id: UUID, owner_id, *, access):
        self._check(access)
        with self.database.transaction(write=True) as c:
            c.execute(
                "DELETE FROM voice_sessions WHERE voice_session_id=? AND owner_id=?",
                (str(session_id), str(owner_id)),
            )
