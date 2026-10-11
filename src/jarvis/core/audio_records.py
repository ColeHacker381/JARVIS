"""Provisional v1 local audio data. Source/speaker IDs never authenticate a principal."""

import base64
import binascii
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from jarvis.core.contracts import Confidence, Contract, Provenance
from jarvis.core.time import utc_now

Text = Annotated[str, Field(min_length=1, max_length=4096)]
SourceID = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")]


class AudioSource(Contract):
    contract_type: Literal["audio_source"] = "audio_source"
    source_id: SourceID
    owner_id: UUID
    device_type: Literal["microphone", "simulated", "phone_future", "wearable_future"]
    location: SourceID
    consent_ref: Text
    enabled: bool = False
    available: bool = True
    continuous_allowed: bool = False
    memory_candidates_allowed: bool = False
    transcript_retention_seconds: Annotated[int, Field(ge=1, le=300)] = 60
    sample_rate: Literal[16000] = 16000
    channels: Literal[1] = 1
    encoding: Literal["pcm_s16le"] = "pcm_s16le"
    clock_ref: Literal["utc"] = "utc"
    registered_at: AwareDatetime = Field(default_factory=utc_now)


class AudioFrame(Contract):
    contract_type: Literal["audio_frame"] = "audio_frame"
    source_id: SourceID
    voice_session_id: UUID
    correlation_id: UUID
    observed_at: AwareDatetime
    sequence: Annotated[int, Field(ge=0)]
    sample_rate: Literal[16000] = 16000
    channels: Literal[1] = 1
    encoding: Literal["pcm_s16le"] = "pcm_s16le"
    duration_ms: Annotated[int, Field(ge=10, le=100)]
    pcm_base64: Annotated[str, Field(min_length=1, max_length=4268, repr=False)]

    @model_validator(mode="after")
    def valid_pcm(self):
        try:
            audio = base64.b64decode(self.pcm_base64, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("audio_encoding_invalid") from None
        if len(audio) != self.sample_rate * self.duration_ms // 1000 * 2:
            raise ValueError("audio_frame_size_invalid")
        return self

    def pcm(self):
        return base64.b64decode(self.pcm_base64, validate=True)


class SpeechSegment(Contract):
    contract_type: Literal["speech_segment"] = "speech_segment"
    segment_id: UUID = Field(default_factory=uuid4)
    source_id: SourceID
    voice_session_id: UUID
    correlation_id: UUID
    started_at: AwareDatetime
    ended_at: AwareDatetime
    first_sequence: Annotated[int, Field(ge=0)]
    last_sequence: Annotated[int, Field(ge=0)]
    sample_rate: Literal[16000] = 16000
    pcm_base64: Annotated[str, Field(min_length=1, max_length=426668, repr=False)]
    vad_confidence: Confidence

    @model_validator(mode="after")
    def bounded(self):
        if self.ended_at < self.started_at or self.last_sequence < self.first_sequence:
            raise ValueError("speech_order_invalid")
        if (self.ended_at - self.started_at).total_seconds() > 10.1:
            raise ValueError("speech_duration_exceeded")
        try:
            audio = base64.b64decode(self.pcm_base64, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("speech_encoding_invalid") from None
        if not audio or len(audio) % 2 or len(audio) > 320000:
            raise ValueError("speech_size_invalid")
        return self

    def pcm(self):
        return base64.b64decode(self.pcm_base64, validate=True)


class TranscriptionResult(Contract):
    contract_type: Literal["transcription_result"] = "transcription_result"
    segment_id: UUID
    text: Text
    provider: SourceID
    model: Text
    language: Annotated[str, Field(min_length=2, max_length=16)] = "en"
    confidence: Confidence | None = None
    duration_ms: Annotated[float, Field(ge=0)] = 0.0
    simulated: bool = False


class SpeakerTurn(Contract):
    contract_type: Literal["speaker_turn"] = "speaker_turn"
    turn_id: UUID = Field(default_factory=uuid4)
    transcription: TranscriptionResult
    source_id: SourceID
    voice_session_id: UUID
    speaker_label: Annotated[str, Field(pattern=r"^speaker_[1-9][0-9]{0,2}$")]
    started_at: AwareDatetime
    ended_at: AwareDatetime
    attribution_confidence: Confidence = 0.0
    overlapping: bool = False
    # No principal field: attribution evidence is never authentication.

    @model_validator(mode="after")
    def ordered_turn(self):
        if self.ended_at < self.started_at:
            raise ValueError("speaker_turn_order_invalid")
        return self


class ConversationSegment(Contract):
    contract_type: Literal["conversation_segment"] = "conversation_segment"
    conversation_id: UUID
    turn_ids: Annotated[tuple[UUID, ...], Field(max_length=16)]
    location: SourceID
    boundary_reason: Literal["new", "continuation", "handoff", "expired"]
    continuity_confidence: Confidence


class AmbientConversation(Contract):
    contract_type: Literal["ambient_conversation"] = "ambient_conversation"
    conversation_id: UUID = Field(default_factory=uuid4)
    voice_session_id: UUID
    owner_id: UUID
    location: SourceID
    source_ids: Annotated[tuple[SourceID, ...], Field(max_length=8)]
    turns: Annotated[tuple[SpeakerTurn, ...], Field(max_length=16)] = ()
    topic: Annotated[str, Field(max_length=120)] = ""
    participating: bool = False
    last_interaction_at: AwareDatetime | None = None
    updated_at: AwareDatetime
    expires_at: AwareDatetime
    pending_initiative_ids: Annotated[tuple[UUID, ...], Field(max_length=4)] = ()
    consent_ref: Text


class AttentionDecision(Contract):
    contract_type: Literal["attention_decision"] = "attention_decision"
    turn_id: UUID
    conversation_id: UUID
    classification: Literal[
        "DIRECT_REQUEST",
        "CONTEXTUAL_FOLLOWUP",
        "INDIRECT_REQUEST",
        "BACKGROUND_CONVERSATION",
        "ENVIRONMENTAL_INFORMATION",
        "UNCERTAIN",
    ]
    decision: Literal[
        "IGNORE", "OBSERVE", "RESPOND", "SUGGEST", "PROPOSE_ACTION", "REQUEST_CLARIFICATION"
    ]
    confidence: Confidence
    reason_code: SourceID


class VoiceSession(Contract):
    contract_type: Literal["voice_session"] = "voice_session"
    voice_session_id: UUID = Field(default_factory=uuid4)
    owner_id: UUID
    source_ids: Annotated[tuple[SourceID, ...], Field(min_length=1, max_length=8)]
    conversation_id: UUID
    mode: Literal["push_to_talk", "ambient"]
    consent_ref: Text
    created_at: AwareDatetime = Field(default_factory=utc_now)
    expires_at: AwareDatetime
    state: Literal["ACTIVE", "CLOSED"] = "ACTIVE"


class SpokenResponse(Contract):
    contract_type: Literal["spoken_response"] = "spoken_response"
    response_id: UUID = Field(default_factory=uuid4)
    conversation_id: UUID
    request_id: UUID
    text: Text
    provider: SourceID
    simulated: bool = False
    duration_ms: Annotated[float, Field(ge=0)] = 0.0
    status: Literal["generated", "played", "cancelled", "failed"] = "generated"


class MemoryCandidate(Contract):
    contract_type: Literal["memory_candidate"] = "memory_candidate"
    candidate_id: UUID = Field(default_factory=uuid4)
    owner_id: UUID
    voice_session_id: UUID
    source_conversation_id: UUID
    source_id: SourceID
    consent_ref: Text
    turn_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=4)]
    claim: Text
    statement_type: Literal["asserted", "questioned", "hypothetical", "correction"]
    transcription_confidence: Confidence | None
    project: SourceID
    proposed_scope: Literal["private"] = "private"
    provenance: Provenance
    expires_at: AwareDatetime
    required_authority: Literal["audio.memory.confirm+memory.write"] = (
        "audio.memory.confirm+memory.write"
    )
    persistence_eligible: bool = False


AUDIO_TYPES = {
    model.model_fields["contract_type"].default: model
    for model in (
        AudioFrame,
        AudioSource,
        SpeechSegment,
        TranscriptionResult,
        SpeakerTurn,
        ConversationSegment,
        AmbientConversation,
        AttentionDecision,
        VoiceSession,
        SpokenResponse,
        MemoryCandidate,
    )
}


def decode_audio(serialized):
    import json

    data = json.loads(serialized)

    if not isinstance(data, dict) or data.get("contract_type") not in AUDIO_TYPES:
        raise ValueError("audio_contract_type_invalid")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("audio_contract_version_invalid")
    from jarvis.core.cognitive_records import explicit_versions

    model = AUDIO_TYPES[data["contract_type"]]
    explicit_versions(data, model)
    return model.model_validate_json(serialized)
