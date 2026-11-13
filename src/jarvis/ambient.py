"""Ephemeral, consent-scoped ambient awareness. Attention is advice, never authority."""

import hashlib
from collections import OrderedDict
from datetime import timedelta

from jarvis.core.audio_records import (
    AmbientConversation,
    AttentionDecision,
    ConversationSegment,
    MemoryCandidate,
)
from jarvis.core.contracts import Provenance
from jarvis.core.time import utc_now


class AmbientContextManager:
    def __init__(self, *, clock=utc_now, max_conversations=16, max_turns=16):
        if not 1 <= max_conversations <= 16 or not 1 <= max_turns <= 16:
            raise ValueError("ambient_limits_invalid")
        self.clock, self.max_conversations, self.max_turns = clock, max_conversations, max_turns
        self.contexts = OrderedDict()

    def expire(self):
        now = self.clock()
        for key, context in tuple(self.contexts.items()):
            if context.expires_at <= now:
                del self.contexts[key]

    def delete(self, session_id):
        for key in tuple(self.contexts):
            if key[0] == session_id:
                del self.contexts[key]

    def group(self, turn, source, session):
        self.expire()
        key = (session.voice_session_id, source.location)
        old = self.contexts.get(key)
        now = self.clock()
        if old is not None and old.consent_ref != source.consent_ref:
            old = None
        if old is None:
            context = AmbientConversation(
                voice_session_id=session.voice_session_id,
                owner_id=session.owner_id,
                location=source.location,
                source_ids=(source.source_id,),
                updated_at=now,
                expires_at=min(
                    session.expires_at, now + timedelta(seconds=source.transcript_retention_seconds)
                ),
                consent_ref=source.consent_ref,
            )
        else:
            context = old
        sources = tuple(dict.fromkeys((*context.source_ids, source.source_id)))[:8]
        context = context.model_copy(
            update={
                "turns": (*context.turns, turn)[-self.max_turns :],
                "source_ids": sources,
                "topic": "project:slipstream"
                if "slipstream" in turn.transcription.text.casefold()
                else context.topic,
                "updated_at": now,
                "expires_at": min(
                    session.expires_at, now + timedelta(seconds=source.transcript_retention_seconds)
                ),
            }
        )
        self.contexts[key] = context
        self.contexts.move_to_end(key)
        while len(self.contexts) > self.max_conversations:
            self.contexts.popitem(last=False)
        return context, ConversationSegment(
            conversation_id=context.conversation_id,
            turn_ids=tuple(t.turn_id for t in context.turns),
            location=source.location,
            boundary_reason="new" if old is None else "continuation",
            continuity_confidence=0.5,
        )

    def participating(self, context):
        key = (context.voice_session_id, context.location)
        if key in self.contexts:
            self.contexts[key] = context.model_copy(
                update={"participating": True, "last_interaction_at": self.clock()}
            )

    def handoff(self, session, old_location, new_source):
        # Explicit trusted routing only; microphone proximity never establishes continuity.
        key = (session.voice_session_id, old_location)
        self.expire()
        if key not in self.contexts or new_source.owner_id != session.owner_id:
            return False
        if self.contexts[key].consent_ref != new_source.consent_ref or not new_source.enabled:
            return False
        context = self.contexts.pop(key)
        self.contexts[(session.voice_session_id, new_source.location)] = context.model_copy(
            update={
                "location": new_source.location,
                "source_ids": tuple(dict.fromkeys((*context.source_ids, new_source.source_id)))[:8],
            }
        )
        return True


class RuleAttentionClassifier:
    def __init__(self, *, clock=utc_now, followup_seconds=30):
        self.clock, self.followup_seconds = clock, followup_seconds

    def classify(self, turn, conversation, *, authenticated=False, quiet=False):
        text = turn.transcription.text.strip().casefold()
        addressed = text.startswith(("jarvis", "hey jarvis"))
        followup = (
            conversation.participating
            and conversation.last_interaction_at is not None
            and (self.clock() - conversation.last_interaction_at).total_seconds()
            < self.followup_seconds
        )
        classification = (
            "DIRECT_REQUEST"
            if addressed
            else "CONTEXTUAL_FOLLOWUP"
            if followup
            else (
                "INDIRECT_REQUEST"
                if "jarvis" in text
                else "ENVIRONMENTAL_INFORMATION"
                if "slipstream" in text
                else "BACKGROUND_CONVERSATION"
            )
        )
        relevant = addressed or followup
        decision = (
            "RESPOND"
            if relevant and authenticated and not quiet
            else ("REQUEST_CLARIFICATION" if relevant and not authenticated else "OBSERVE")
        )
        return AttentionDecision(
            turn_id=turn.turn_id,
            conversation_id=conversation.conversation_id,
            classification=classification,
            decision=decision,
            confidence=0.8,
            reason_code="authenticated_interaction"
            if decision == "RESPOND"
            else (
                "trusted_confirmation_required"
                if relevant and not authenticated
                else "participation_suppressed"
            ),
        )


class DeterministicAudioRouter:
    def __init__(self, *, clock=utc_now):
        self.clock = clock
        self.seen = OrderedDict()
        self.suppressed = 0

    def duplicate(self, turn, session, source):
        now = self.clock()
        for key, (_, at) in tuple(self.seen.items()):
            if (now - at).total_seconds() > 2:
                del self.seen[key]
        digest = hashlib.sha256(turn.transcription.text.strip().casefold().encode()).digest()
        key = (session.voice_session_id, source.location, source.consent_ref, digest)
        old = self.seen.get(key)
        duplicate = old is not None and old[0] != source.source_id
        if duplicate:
            self.suppressed += 1
            return True
        self.seen[key] = (source.source_id, now)
        self.seen.move_to_end(key)
        while len(self.seen) > 128:
            self.seen.popitem(last=False)
        return False

    def delete(self, session_id):
        for key in tuple(self.seen):
            if key[0] == session_id:
                del self.seen[key]


class MemoryCandidateEvaluator:
    def __init__(self, *, clock=utc_now):
        self.clock = clock

    def evaluate(self, turn, context, source, session):
        text = turn.transcription.text
        folded = text.casefold()
        # Narrow project-only extraction; no person profiles or inferred confirmed facts.
        if not source.memory_candidates_allowed or "slipstream" not in folded:
            return None
        kind = (
            "questioned"
            if "?" in text
            else "hypothetical"
            if any(x in folded for x in ("maybe", "could", "might", "think", "should"))
            else "correction"
            if "correction" in folded
            else "asserted"
        )
        return MemoryCandidate(
            owner_id=session.owner_id,
            voice_session_id=session.voice_session_id,
            source_conversation_id=context.conversation_id,
            source_id=source.source_id,
            consent_ref=source.consent_ref,
            turn_ids=(turn.turn_id,),
            claim=text,
            statement_type=kind,
            transcription_confidence=turn.transcription.confidence,
            project="slipstream",
            provenance=Provenance(
                category="derived",
                source_ref=f"audio:{source.source_id}",
                derived_from=(f"turn:{turn.turn_id}", f"speech:{turn.transcription.segment_id}"),
                model_ref=f"stt:{turn.transcription.provider}/{turn.transcription.model}",
            ),
            expires_at=min(session.expires_at, context.expires_at),
            persistence_eligible=False,
        )
