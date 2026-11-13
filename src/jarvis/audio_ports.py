"""Replaceable local audio ports. No adapter may supply trusted identity or permissions."""

from typing import Protocol

from jarvis.core.audio_records import AudioFrame, SpeakerTurn, SpeechSegment, TranscriptionResult


class SpeechRecognizer(Protocol):
    async def transcribe(self, segment: SpeechSegment) -> TranscriptionResult: ...


class SpeechSynthesizer(Protocol):
    async def synthesize(self, text: str): ...


class AudioPlayback(Protocol):
    async def play(self, audio) -> None: ...
    def cancel(self) -> None: ...


class VoiceActivityDetector(Protocol):
    def classify(self, frame: AudioFrame) -> tuple[bool, float]: ...


class SpeakerDiarizer(Protocol):
    def turns(
        self, segment: SpeechSegment, result: TranscriptionResult
    ) -> tuple[SpeakerTurn, ...]: ...


class ConversationSegmenter(Protocol):
    def group(self, turn, source, session): ...


class AttentionClassifier(Protocol):
    def classify(self, turn, conversation, *, authenticated=False, quiet=False): ...


class AudioSessionRouter(Protocol):
    def duplicate(self, turn, session, source) -> bool: ...
