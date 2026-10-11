"""Bounded explicit local ingestion, energy VAD and deterministic turn attribution."""

import base64
import math
import struct
import time
from collections import deque
from datetime import timedelta

from jarvis.core.audio_records import SpeakerTurn, SpeechSegment


class AudioError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class EnergyVAD:
    """Local PCM RMS heuristic, not a trained VAD or identity classifier."""

    def __init__(self, threshold=0.02):
        if not 0 < threshold < 1:
            raise ValueError("vad_threshold_invalid")
        self.threshold = threshold

    def classify(self, frame):
        values = struct.unpack("<" + "h" * (len(frame.pcm()) // 2), frame.pcm())
        level = math.sqrt(sum((v / 32768) ** 2 for v in values) / len(values))
        return level >= self.threshold, min(1.0, level / self.threshold / 2)


class FixtureVAD:
    def __init__(self, classifications):
        self.classifications = iter(classifications)

    def classify(self, frame):
        speech = next(self.classifications)
        return speech, 1.0 if speech else 0.0


class AudioFrontEnd:
    def __init__(self, vad=None, *, max_frames=100, silence_ms=300, buffer_ttl=2):
        if not 2 <= max_frames <= 100 or not 100 <= silence_ms <= 1000 or not 0 < buffer_ttl <= 10:
            raise ValueError("audio_buffer_limits_invalid")
        self.vad = vad or EnergyVAD()
        self.max_frames, self.silence_ms, self.buffer_ttl = max_frames, silence_ms, buffer_ttl
        self.frames, self.lookback = deque(), deque(maxlen=2)
        self.last_sequence = -1
        self.last_time = None
        self.last_arrival = None
        self.silent_ms = 0
        self.dropped = 0
        self.active = True
        self.stream = None
        self.vad_ms = 0.0

    def expire(self):
        if self.last_arrival is not None and time.monotonic() - self.last_arrival > self.buffer_ttl:
            self.frames.clear()
            self.lookback.clear()
            self.silent_ms = 0

    def ingest(self, frame):
        if not self.active:
            raise AudioError("audio_source_disconnected")
        self.expire()
        binding = (frame.source_id, frame.voice_session_id, frame.correlation_id)
        if self.stream is not None and binding != self.stream:
            raise AudioError("audio_stream_mismatch")
        now = time.monotonic()
        if frame.sequence <= self.last_sequence or (
            self.last_time is not None and frame.observed_at < self.last_time
        ):
            raise AudioError("audio_frame_out_of_order")
        if self.last_sequence >= 0 and frame.sequence != self.last_sequence + 1:
            self.dropped += frame.sequence - self.last_sequence - 1
            self.frames.clear()  # Do not join speech across lost audio.
            self.lookback.clear()
        if self.last_time is not None and (frame.observed_at - self.last_time).total_seconds() > 2:
            self.frames.clear()
            self.lookback.clear()
        self.stream = binding
        self.last_arrival, self.last_sequence, self.last_time = (
            now,
            frame.sequence,
            frame.observed_at,
        )
        begin = time.perf_counter()
        speech, confidence = self.vad.classify(frame)
        self.vad_ms = (time.perf_counter() - begin) * 1000
        if speech and not self.frames:
            self.frames.extend(self.lookback)
            self.lookback.clear()
        if speech or self.frames:
            self.frames.append(frame)
            self.silent_ms = 0 if speech else self.silent_ms + frame.duration_ms
            duration = sum(f.duration_ms for f in self.frames)
            if (
                len(self.frames) >= self.max_frames
                or duration >= 10000
                or self.silent_ms >= self.silence_ms
            ):
                return self.flush(confidence=confidence if speech else 0.5)
        else:
            self.lookback.append(frame)
        return None

    def flush(self, *, confidence=0.5):
        if not self.frames:
            return None
        first, last = self.frames[0], self.frames[-1]
        result = SpeechSegment(
            source_id=first.source_id,
            voice_session_id=first.voice_session_id,
            correlation_id=first.correlation_id,
            started_at=first.observed_at,
            ended_at=last.observed_at + timedelta(milliseconds=last.duration_ms),
            first_sequence=first.sequence,
            last_sequence=last.sequence,
            pcm_base64=base64.b64encode(b"".join(f.pcm() for f in self.frames)).decode(),
            vad_confidence=confidence,
        )
        self.frames.clear()
        self.lookback.clear()
        self.silent_ms = 0
        return result

    def disconnect(self):
        self.frames.clear()
        self.lookback.clear()
        self.active = False

    def reconnect(self):
        self.disconnect()
        self.active = True
        self.last_sequence = -1
        self.last_time = None
        self.stream = None


class FixtureDiarizer:
    """Explicit simulation labels; live backend uses unknown speaker_1 with confidence 0."""

    def __init__(self, labels=(), *, overlapping=False):
        self.labels = iter(labels)
        self.overlapping = overlapping

    def turns(self, segment, result):
        label = next(self.labels, "speaker_1")
        return (
            SpeakerTurn(
                transcription=result,
                source_id=segment.source_id,
                voice_session_id=segment.voice_session_id,
                speaker_label=label,
                started_at=segment.started_at,
                ended_at=segment.ended_at,
                attribution_confidence=1.0 if result.simulated else 0.0,
                overlapping=self.overlapping,
            ),
        )
