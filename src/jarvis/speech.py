"""Optional offline speech adapters. Hardware and model weights are never auto-enabled."""

import asyncio
import io
import shutil
import struct
import sys
import tempfile
import time
import unicodedata
import wave
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from jarvis.audio_frontend import AudioError
from jarvis.core.audio_records import TranscriptionResult


@dataclass(frozen=True)
class SynthesizedAudio:
    wav: bytes
    provider: str
    simulated: bool = False
    duration_ms: float = 0.0


def pcm_wave(pcm, sample_rate=16000):
    output = io.BytesIO()
    with wave.open(output, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(pcm)
    return output.getvalue()


def validate_wave(data):
    if len(data) > 6_000_000:
        raise AudioError("speech_output_oversized")
    try:
        with wave.open(io.BytesIO(data), "rb") as f:
            if (
                f.getnchannels() != 1
                or f.getsampwidth() != 2
                or not 8000 <= f.getframerate() <= 48000
            ):
                raise AudioError("speech_output_format_invalid")
            if f.getnframes() / f.getframerate() > 60:
                raise AudioError("speech_output_duration_exceeded")
            pcm = f.readframes(f.getnframes())
            if not pcm or len(pcm) != f.getnframes() * 2:
                raise AudioError("speech_output_format_invalid")
            return f.getframerate(), pcm
    except (wave.Error, EOFError):
        raise AudioError("speech_output_format_invalid") from None


async def local_process(argv, *, timeout=60, stdin=None, stdout=None):
    """No shell or cloud fallback. Deadline/cancellation waits for the owned child."""
    if not 0 < timeout <= 300:
        raise ValueError("speech_timeout_invalid")
    try:
        process = await asyncio.create_subprocess_exec(
            *map(str, argv),
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=stdout if stdout is not None else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        raise AudioError("speech_provider_unavailable") from None
    try:
        async with asyncio.timeout(timeout):
            await process.communicate(stdin)
        if process.returncode != 0:
            raise AudioError("speech_provider_failed")
    except (TimeoutError, asyncio.CancelledError) as exc:
        if process.returncode is None:
            process.kill()
        await process.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise AudioError("speech_provider_timeout") from None


class FakeSpeechRecognizer:
    def __init__(self, texts):
        self.texts = deque(texts)
        self.calls = 0

    async def transcribe(self, segment):
        self.calls += 1
        if not self.texts:
            raise AudioError("fake_stt_fixture_exhausted")
        return TranscriptionResult(
            segment_id=segment.segment_id,
            text=self.texts.popleft(),
            provider="development.fake-stt",
            model="explicit-fixture-v1",
            simulated=True,
            confidence=1.0,
        )


class WhisperCppRecognizer:
    def __init__(self, executable, model, *, language="en", timeout=60):
        self.executable, self.model = Path(executable).resolve(), Path(model).resolve()
        if not language.isalpha() or not 2 <= len(language) <= 16 or not 0 < timeout <= 300:
            raise ValueError("speech_settings_invalid")
        self.language, self.timeout = language, timeout

    async def transcribe(self, segment):
        import json

        if not self.executable.is_file() or not self.model.is_file():
            raise AudioError("local_stt_model_or_binary_missing")
        start = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="jarvis-stt-") as directory:
            wav, output = Path(directory) / "input.wav", Path(directory) / "result"
            wav.write_bytes(pcm_wave(segment.pcm()))
            wav.chmod(0o600)
            await local_process(
                [
                    self.executable,
                    "-m",
                    self.model,
                    "-f",
                    wav,
                    "-l",
                    self.language,
                    "-oj",
                    "-of",
                    output,
                    "-t",
                    "2",
                ],
                timeout=self.timeout,
            )
            file = output.with_suffix(".json")
            if not file.is_file() or file.stat().st_size > 65536:
                raise AudioError("stt_output_invalid")
            try:
                data = json.loads(file.read_text())
                parts = [x["text"].strip() for x in data["transcription"]]
                # whisper.cpp can emit a non-speech marker even with exit status zero.
                # Such markers must never become conversational/tool requests.
                markers = {"[blank_audio]", "[silence]", "(silence)"}
                text = " ".join(p for p in parts if p.casefold() not in markers).strip()
                # Even non-marker hallucinations (observed: "You") from digital
                # silence must not become a user request. This narrow check does
                # not claim reliable rejection of real microphone noise.
                if not text or not any(segment.pcm()):
                    raise AudioError("stt_no_speech")
                return TranscriptionResult(
                    segment_id=segment.segment_id,
                    text=text,
                    provider="local.whisper-cpp",
                    model=self.model.name,
                    language=self.language,
                    duration_ms=(time.perf_counter() - start) * 1000,
                )
            except (ValueError, TypeError, KeyError, AttributeError):
                raise AudioError("stt_output_invalid") from None


class FakeSpeechSynthesizer:
    async def synthesize(self, text):
        return SynthesizedAudio(pcm_wave(b"\0\0" * 1600), "development.fake-tts", True)


class SystemSpeechSynthesizer:
    """Offline system say (macOS) / espeak-ng (Linux). Voice installation is explicit."""

    def __init__(self, *, executable=None, voice=None, rate=175, timeout=60):
        if not 80 <= rate <= 350 or not 0 < timeout <= 300:
            raise ValueError("tts_settings_invalid")
        self.macos = sys.platform == "darwin"
        self.executable = executable or shutil.which("say" if self.macos else "espeak-ng")
        self.voice = voice or ("Samantha" if self.macos else "en-us")
        if not self.voice or len(self.voice) > 128 or self.voice.startswith("-"):
            raise ValueError("tts_voice_invalid")
        self.rate, self.timeout = rate, timeout

    async def synthesize(self, text):
        if (
            not isinstance(text, str)
            or not 1 <= len(text) <= 4096
            or not text.strip()
            or any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in text)
        ):
            raise AudioError("tts_text_invalid")
        if not self.executable:
            raise AudioError("local_tts_binary_missing")
        start = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="jarvis-tts-") as directory:
            target = Path(directory) / "speech.wav"
            if self.macos:
                # macOS say can silently use its default for an unknown voice.
                # Require an exact installed name before creating speech.
                voices = Path(directory) / "voices.txt"
                with voices.open("wb") as output:
                    await local_process(
                        [self.executable, "-v", "?"], timeout=self.timeout, stdout=output
                    )
                if voices.stat().st_size > 65536:
                    raise AudioError("tts_voice_list_invalid")
                names = set()
                for line in voices.read_text().splitlines():
                    fields = line.split("#", 1)[0].rsplit(None, 1)
                    if len(fields) == 2:
                        names.add(fields[0].strip())
                if self.voice not in names:
                    raise AudioError("tts_voice_unavailable")
                script = Path(directory) / "text.txt"
                script.write_text(text)
                script.chmod(0o600)
                argv = [
                    self.executable,
                    "-v",
                    self.voice,
                    "-r",
                    str(self.rate),
                    "-f",
                    script,
                    "-o",
                    target,
                    "--file-format=WAVE",
                    "--data-format=LEI16@16000",
                ]
                await local_process(argv, timeout=self.timeout)
            else:
                await local_process(
                    [
                        self.executable,
                        "-v",
                        self.voice,
                        "-s",
                        str(self.rate),
                        "-w",
                        target,
                        "--stdin",
                    ],
                    timeout=self.timeout,
                    stdin=text.encode(),
                )
            if not target.is_file() or target.stat().st_size > 6_000_000:
                raise AudioError("tts_output_invalid")
            data = target.read_bytes()
            validate_wave(data)
            return SynthesizedAudio(
                data,
                "local.say" if self.macos else "local.espeak-ng",
                duration_ms=(time.perf_counter() - start) * 1000,
            )


def sounddevice_module():
    try:
        import sounddevice

        return sounddevice
    except (ImportError, OSError):
        raise AudioError("audio_dependency_or_portaudio_missing") from None


class LocalMicrophone:
    """Explicit device, visible push-to-talk capture; closes stream on every exit."""

    def __init__(self, device, *, sample_rate=16000, max_seconds=10):
        if device is None or sample_rate != 16000 or not 0 < max_seconds <= 10:
            raise ValueError("microphone_settings_invalid")
        self.device, self.max_seconds = device, max_seconds
        self.cancelled = False
        self.level = 0.0

    def cancel(self):
        self.cancelled = True

    def capture(self, *, stop=None, status=None):
        sd = sounddevice_module()
        self.cancelled = False
        frames = []
        started = time.monotonic()
        try:
            with sd.RawInputStream(
                device=self.device, samplerate=16000, channels=1, dtype="int16", blocksize=1600
            ) as stream:
                while not self.cancelled and time.monotonic() - started < self.max_seconds:
                    if stop is not None and stop.is_set():
                        break
                    data, overflow = stream.read(1600)
                    if overflow:
                        raise AudioError("microphone_overflow")
                    frame = bytes(data)
                    values = struct.unpack("<" + "h" * (len(frame) // 2), frame)
                    self.level = max((abs(x) / 32768 for x in values), default=0)
                    frames.append(frame)
                    if status:
                        status(self.level)
        except sd.PortAudioError:
            raise AudioError("microphone_unavailable_or_permission_denied") from None
        if not frames:
            raise AudioError("microphone_empty_capture")
        return b"".join(frames)


class LocalAudioPlayback:
    def __init__(self, device=None):
        self.device = device
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    async def play(self, audio):
        rate, pcm = validate_wave(audio.wav)
        if self.cancelled:
            raise AudioError("playback_cancelled")
        # Native stop/close can deadlock inside a device driver. A thread cannot
        # be safely interrupted there. Only an owned, reaped child opens playback;
        # success requires that child to finish normal stop AND close.
        with tempfile.TemporaryDirectory(prefix="jarvis-playback-") as directory:
            cancel = Path(directory) / "cancel"
            launch = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "jarvis.audio_playback",
                    str(self.device) if self.device is not None else "default",
                    str(cancel),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            )
            try:
                child = await asyncio.shield(launch)
            except asyncio.CancelledError:
                child = await launch
                if child.returncode is None:
                    child.kill()
                await child.wait()
                raise
            except OSError:
                raise AudioError("playback_process_unavailable") from None
            work = asyncio.create_task(child.communicate(audio.wav))
            deadline = time.monotonic() + len(pcm) / (rate * 2) + 10

            async def finish_cancel():
                cancel.touch(mode=0o600)
                try:
                    await asyncio.wait_for(asyncio.shield(work), 3)
                except TimeoutError:
                    if child.returncode is None:
                        child.kill()
                    await work
                    raise AudioError("playback_cleanup_timeout") from None

            try:
                while not work.done():
                    if self.cancelled:
                        await finish_cancel()
                        break
                    if time.monotonic() >= deadline:
                        if child.returncode is None:
                            child.kill()
                        await work
                        raise AudioError("playback_device_timeout")
                    await asyncio.sleep(0.02)
                await work
            except asyncio.CancelledError:
                self.cancelled = True
                await finish_cancel()
                raise
            finally:
                if child.returncode is None:
                    child.kill()
                await work
            errors = {
                2: "playback_cancelled",
                3: "playback_device_unavailable",
                4: "audio_dependency_or_portaudio_missing",
                5: "playback_device_timeout",
                6: "playback_underflow",
            }
            if child.returncode:
                raise AudioError(errors.get(child.returncode, "playback_process_failed"))
            if self.cancelled:
                raise AudioError("playback_cancelled")


class FakeAudioPlayback:
    def __init__(self):
        self.cancelled = False
        self.played = 0

    def cancel(self):
        self.cancelled = True

    async def play(self, audio):
        validate_wave(audio.wav)
        if self.cancelled:
            raise AudioError("playback_cancelled")
        self.played += 1
