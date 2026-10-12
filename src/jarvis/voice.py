"""Local voice integration around existing cognition, identity, memory and notifications."""

import asyncio
import threading
import time
from collections import OrderedDict
from datetime import timedelta

from jarvis.ambient import (
    AmbientContextManager,
    DeterministicAudioRouter,
    MemoryCandidateEvaluator,
    RuleAttentionClassifier,
)
from jarvis.audio_frontend import AudioError, AudioFrontEnd, FixtureDiarizer
from jarvis.core.audio_records import SpokenResponse, VoiceSession
from jarvis.core.contracts import AuditRecord
from jarvis.core.memory_records import MemoryDraft
from jarvis.core.time import utc_now
from jarvis.observability import write_audit
from jarvis.storage.audio import SQLiteAudioStore


class AudioControl:
    def __init__(self, store, permissions, conversations, *, clock=utc_now):
        self.store, self.permissions, self.conversations = store, permissions, conversations
        self.identities, self.clock = store.identities, clock
        self.access = store._bind()

    def _authorized(self, token, permission):
        session = self.identities.resolve_session(token)
        if not self.permissions.current_grants(session.principal_id, (permission,)):
            raise AudioError("audio_permission_denied")
        return session

    def register(self, source, *, session_token):
        with self.identities.transaction():
            session = self._authorized(session_token, "audio.sources.manage")
            if session.principal_id != self.identities.owner_id:
                raise AudioError("audio_source_administration_denied")
            principal = self.identities.get_principal(source.owner_id)
            if principal is None or principal.status != "active":
                raise AudioError("audio_source_owner_invalid")
            self.store._register(source, session, access=self.access)

    def source(self, source_id, *, session_token):
        with self.identities.transaction():
            session = self._authorized(session_token, "audio.process")
            source = self.store._source(source_id, session.principal_id, access=self.access)
            if source is None:
                raise AudioError("audio_source_unavailable")
            return source

    def start(
        self, source_ids, conversation_id, *, session_token, mode="push_to_talk", seconds=300
    ):
        if not 1 <= seconds <= 300:
            raise AudioError("voice_session_duration_invalid")
        with self.identities.transaction():
            session = self._authorized(session_token, "audio.process")
            self.conversations.get(conversation_id, session_token=session_token)
            for source_id in source_ids:
                source = self.source(source_id, session_token=session_token)
                if (
                    not source.enabled
                    or not source.available
                    or (mode == "ambient" and not source.continuous_allowed)
                ):
                    raise AudioError("audio_processing_disabled")
            record = VoiceSession(
                owner_id=session.principal_id,
                source_ids=tuple(source_ids),
                conversation_id=conversation_id,
                mode=mode,
                consent_ref="consent:explicit_authenticated_activation",
                expires_at=self.clock() + timedelta(seconds=seconds),
            )
            self.store._save_session(record, session, access=self.access)
            return record

    def get(self, voice_session_id, *, session_token):
        with self.identities.transaction():
            session = self._authorized(session_token, "audio.process")
            record = self.store._session(voice_session_id, session.principal_id, access=self.access)
            if record is None or record.state != "ACTIVE" or record.expires_at <= self.clock():
                raise AudioError("voice_session_unavailable")
            return record

    def validate(self, source_id, session_id, token):
        with self.identities.transaction():
            record = self.get(session_id, session_token=token)
            source = self.source(source_id, session_token=token)
            if source_id not in record.source_ids:
                raise AudioError("audio_session_source_mismatch")
            if (
                not source.enabled
                or not source.available
                or source.device_type in {"phone_future", "wearable_future"}
            ):
                raise AudioError("audio_processing_disabled")
            if record.mode == "ambient" and not source.continuous_allowed:
                raise AudioError("audio_processing_disabled")
            return source, record

    def close(self, session_id, *, session_token, delete=False):
        with self.identities.transaction():
            session = self._authorized(session_token, "audio.process")
            record = self.store._session(session_id, session.principal_id, access=self.access)
            if record is None:
                raise AudioError("voice_session_unavailable")
            self.store._save_session(
                record.model_copy(update={"state": "CLOSED"}), session, access=self.access
            )
            if delete:
                self.store._delete_session(session_id, session.principal_id, access=self.access)

    def sessions(self, *, session_token):
        with self.identities.transaction():
            session = self._authorized(session_token, "audio.process")
            return self.store._sessions(session.principal_id, access=self.access)


class VoiceService:
    def __init__(self, application, *, clock=utc_now):
        self.application, self.clock = application, clock
        self.control = AudioControl(
            SQLiteAudioStore(application.database, application.identities),
            application.permissions,
            application.conversations,
            clock=clock,
        )
        self.context = AmbientContextManager(clock=clock)
        self.attention = RuleAttentionClassifier(clock=clock)
        self.router = DeterministicAudioRouter(clock=clock)
        self.evaluator = MemoryCandidateEvaluator(clock=clock)
        self.frontends, self.candidates = OrderedDict(), OrderedDict()
        self.recognizer = None  # No weights/device initialization during core startup.
        self.diarizer = FixtureDiarizer()
        self.closed = False
        self._lock = threading.RLock()
        self._processing = False
        self._confirmed = {}  # bounded durable-write receipts for this ephemeral candidate lifetime
        self.metrics = OrderedDict()

    def expire(self):
        with self._lock:
            self.context.expire()
            for key, frontend in tuple(self.frontends.items()):
                frontend.expire()
                if (
                    frontend.last_arrival is not None
                    and time.monotonic() - frontend.last_arrival > 10
                ):
                    frontend.disconnect()
                    del self.frontends[key]
            for key, candidate in tuple(self.candidates.items()):
                if candidate.expires_at <= self.clock():
                    del self.candidates[key]
                    self._confirmed.pop(key, None)

    def metric(self, name, start):
        # Stage names and durations only. No transcript, audio, source or identity payloads.
        self.metrics[name] = (time.perf_counter() - start) * 1000

    def ingest(self, frame, *, session_token):
        start = time.perf_counter()
        with self._lock:
            if self.closed:
                raise AudioError("voice_processing_stopped")
            self.expire()
            self.control.validate(frame.source_id, frame.voice_session_id, session_token)
            if abs((self.clock() - frame.observed_at).total_seconds()) > 10:
                raise AudioError("audio_timestamp_out_of_window")
            key = (frame.voice_session_id, frame.source_id)
            if key not in self.frontends:
                if len(self.frontends) >= 16:
                    raise AudioError("audio_backpressure")
                self.frontends[key] = AudioFrontEnd()
            result = self.frontends[key].ingest(frame)
            self.metric("ingestion_ms", start)
            self.metrics["vad_ms"] = self.frontends[key].vad_ms
            return result

    def flush(self, source_id, session_id, *, session_token):
        with self._lock:
            self.control.validate(source_id, session_id, session_token)
            frontend = self.frontends.get((session_id, source_id))
            return frontend.flush() if frontend else None

    def current_context(self, session_id, location, *, session_token):
        with self._lock:
            self.expire()
            session = self.control.get(session_id, session_token=session_token)
            context = self.context.contexts.get((session.voice_session_id, location))
            if context is None:
                raise AudioError("ambient_context_unavailable")
            for source_id in context.source_ids:
                source, _ = self.control.validate(source_id, session_id, session_token)
                if source.consent_ref != context.consent_ref:
                    raise AudioError("ambient_consent_changed")
            return context.model_copy(deep=True)

    def disconnect(self, source_id, session_id, *, session_token):
        with self._lock:
            self.control.validate(source_id, session_id, session_token)
            key = (session_id, source_id)
            if key in self.frontends:
                self.frontends[key].disconnect()

    def reconnect(self, source_id, session_id, *, session_token):
        with self._lock:
            self.control.validate(source_id, session_id, session_token)
            key = (session_id, source_id)
            if key in self.frontends:
                self.frontends[key].reconnect()

    def delete_context(self, session_id, *, session_token):
        self.control.get(session_id, session_token=session_token)
        with self._lock:
            self.context.delete(session_id)
            self.router.delete(session_id)
            for key in tuple(self.frontends):
                if key[0] == session_id:
                    self.frontends.pop(key).disconnect()
            for key, candidate in tuple(self.candidates.items()):
                if candidate.voice_session_id == session_id:
                    del self.candidates[key]
                    self._confirmed.pop(key, None)

    def stop(self):
        with self._lock:
            self.closed = True
            for frontend in self.frontends.values():
                frontend.disconnect()
            self.frontends.clear()
            self.context.contexts.clear()
            self.candidates.clear()
            self.router.seen.clear()
            self._confirmed.clear()

    async def process(
        self,
        segment,
        *,
        session_token,
        interaction_token=None,
        recognizer=None,
        synthesizer=None,
        playback=None,
        quiet=False,
        deadline=60,
    ):
        if not 0 < deadline <= 300:
            raise ValueError("audio_processing_deadline_invalid")
        with self._lock:
            if self.closed:
                raise AudioError("voice_processing_stopped")
            if self._processing:
                raise AudioError("audio_backpressure")
            self._processing = True
        try:
            async with asyncio.timeout(deadline):
                return await self._process(
                    segment,
                    session_token=session_token,
                    interaction_token=interaction_token,
                    recognizer=recognizer,
                    synthesizer=synthesizer,
                    playback=playback,
                    quiet=quiet,
                )
        except TimeoutError:
            raise AudioError("audio_processing_timeout") from None
        finally:
            with self._lock:
                self._processing = False

    async def _process(
        self, segment, *, session_token, interaction_token, recognizer, synthesizer, playback, quiet
    ):
        total = time.perf_counter()
        source, session = self.control.validate(
            segment.source_id, segment.voice_session_id, session_token
        )
        recognizer = recognizer or self.recognizer
        if recognizer is None:
            raise AudioError("local_stt_unconfigured")
        start = time.perf_counter()
        result = await recognizer.transcribe(segment)
        self.metric("stt_ms", start)
        if result.segment_id != segment.segment_id:
            raise AudioError("stt_segment_mismatch")
        if self.closed:
            raise AudioError("voice_processing_stopped")
        # Recheck current capture/consent/identity after slow transcription.
        source, session = self.control.validate(
            segment.source_id, segment.voice_session_id, session_token
        )
        turns = self.diarizer.turns(segment, result)
        if len(turns) != 1:
            raise AudioError("multi_turn_confirmation_required")
        turn = turns[0]
        if self.router.duplicate(turn, session, source):
            return {"suppressed_duplicate": True, "source_id": source.source_id}
        with self._lock:
            start = time.perf_counter()
            context, boundary = self.context.group(turn, source, session)
            self.metric("segmentation_ms", start)
            start = time.perf_counter()
            candidate = self.evaluator.evaluate(turn, context, source, session)
            if candidate:
                self.candidates[candidate.candidate_id] = candidate
                while len(self.candidates) > 32:
                    key, _ = self.candidates.popitem(last=False)
                    self._confirmed.pop(key, None)
            self.metric("candidate_ms", start)
        trusted_interaction = False
        if interaction_token is not None:
            actor = self.application.identities.resolve_session(interaction_token)
            if actor.principal_id != session.owner_id:
                raise AudioError("voice_interaction_owner_mismatch")
            self.control._authorized(interaction_token, "audio.process")
            trusted_interaction = not turn.overlapping
        start = time.perf_counter()
        attention = self.attention.classify(
            turn, context, authenticated=trusted_interaction, quiet=quiet
        )
        self.metric("attention_ms", start)
        response, spoken = None, None
        if attention.decision == "RESPOND":
            # Optional cloud routing is forbidden for audio-derived interaction in this phase.
            if self.application.models.identity().location == "cloud":
                raise AudioError("audio_cloud_egress_disabled")
            start = time.perf_counter()
            text = result.text
            for prefix in ("Hey JARVIS,", "JARVIS,", "Jarvis,", "jarvis,"):
                if text.startswith(prefix):
                    text = text[len(prefix) :].strip()
                    break
            response = await self.application.coordinator.respond(
                session.conversation_id,
                text,
                session_token=interaction_token,
                request_id=segment.segment_id,
                response_style="voice",
            )
            self.metric("cognition_ms", start)
            self.context.participating(context)
            if synthesizer is not None:
                start = time.perf_counter()
                spoken_text = getattr(response, "spoken_text", response.text)
                audio = await synthesizer.synthesize(spoken_text)
                self.metric("tts_ms", start)
                spoken = SpokenResponse(
                    conversation_id=response.conversation_id,
                    request_id=response.request_id,
                    text=spoken_text,
                    provider=audio.provider,
                    simulated=audio.simulated,
                    duration_ms=audio.duration_ms,
                )
                # No output after source mute/session or credential revocation during TTS.
                self.control.validate(source.source_id, session.voice_session_id, interaction_token)
                if playback is not None:
                    start = time.perf_counter()
                    try:
                        await playback.play(audio)
                    except AudioError as exc:
                        if exc.code != "playback_cancelled":
                            raise
                        spoken = spoken.model_copy(update={"status": "cancelled"})
                    else:
                        spoken = spoken.model_copy(update={"status": "played"})
                    finally:
                        self.metric("playback_ms", start)
        audit = AuditRecord(
            principal_id=session.owner_id,
            session_id=self.application.identities.resolve_session(session_token).session_id,
            correlation_id=segment.correlation_id,
            action="audio.attention",
            outcome="succeeded",
            reason_code=attention.reason_code,
        )
        write_audit(self.application.audit, audit)
        self.metric("end_to_end_ms", total)
        return {
            "suppressed_duplicate": False,
            "source_id": source.source_id,
            "turn": turn,
            "conversation": boundary,
            "attention": attention,
            "candidate": candidate,
            "response": response,
            "spoken": spoken,
            "audit_id": audit.audit_id,
        }

    def candidate(self, candidate_id, *, session_token):
        with self._lock:
            self.expire()
            actor = self.control._authorized(session_token, "audio.process")
            candidate = self.candidates.get(candidate_id)
            if candidate is None or candidate.owner_id != actor.principal_id:
                raise AudioError("memory_candidate_unavailable")
            session = self.control.get(candidate.voice_session_id, session_token=session_token)
            source, _ = self.control.validate(
                candidate.source_id, session.voice_session_id, session_token
            )
            if not source.memory_candidates_allowed or source.consent_ref != candidate.consent_ref:
                raise AudioError("memory_candidate_consent_changed")
            return candidate

    def confirm_candidate(self, candidate_id, *, session_token, consent_ref):
        # Explicit host confirmation, never invoked by transcript/model output.
        with self._lock, self.application.identities.transaction():
            candidate = self.candidate(candidate_id, session_token=session_token)
            self.control._authorized(session_token, "audio.memory.confirm")
            if candidate_id in self._confirmed:
                return self.application.memory.get(
                    self._confirmed[candidate_id], session_token=session_token
                )
            memory = self.application.memory.create(
                MemoryDraft(
                    kind="audio_candidate",
                    content={
                        "text": candidate.claim,
                        "statement_type": candidate.statement_type,
                        "status": "unverified_candidate",
                    },
                    project_ref="project:" + candidate.project,
                    source=candidate.provenance,
                    consent_ref=consent_ref,
                    confidence=candidate.transcription_confidence,
                    expires_at=self.clock() + timedelta(days=1),
                    retention_ref="retention.audio.confirmed_candidate.1d",
                ),
                session_token=session_token,
            )
            self._confirmed[candidate_id] = memory.memory_id
            return memory
