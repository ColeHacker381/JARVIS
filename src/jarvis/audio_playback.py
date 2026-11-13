"""Private playback worker: one owner for start, drain/abort, stop and close.

No authority or model runtime is instantiated here. Parent supplies bounded WAV
over stdin and a private cancellation marker. Parent owns deadlines and reaping.
"""

import sys
import time
from pathlib import Path

from jarvis.audio_frontend import AudioError
from jarvis.speech import sounddevice_module, validate_wave


def play_wave(data, device, cancelled, *, backend=None):
    rate, pcm = validate_wave(data)
    sd = backend if backend is not None else sounddevice_module()
    position = 0
    failed = False
    requested = cancelled()

    def callback(output, frames, timing, status):
        nonlocal position, failed
        output[:] = b"\0" * len(output)
        if requested:
            raise sd.CallbackAbort
        if status.output_underflow:
            failed = True
            raise sd.CallbackAbort
        count = min(len(output), len(pcm) - position)
        output[:count] = pcm[position : position + count]
        position += count
        if position == len(pcm):
            # Drain on the audio callback's own completion path before stopping
            # the device. No PortAudio control API is called from this callback.
            raise sd.CallbackStop

    stream = None
    try:
        stream = sd.RawOutputStream(
            device=device, samplerate=rate, channels=1, dtype="int16", callback=callback
        )
        stream.start()
        deadline = time.monotonic() + len(pcm) / (rate * 2) + 5
        while stream.active:
            requested = cancelled()
            if time.monotonic() >= deadline:
                raise AudioError("playback_device_timeout")
            time.sleep(0.01)
        if cancelled() or failed:
            stream.abort(ignore_errors=False)
        else:
            stream.stop(ignore_errors=False)
    except sd.PortAudioError:
        raise AudioError("playback_device_unavailable") from None
    finally:
        if stream is not None:
            try:
                stream.close(ignore_errors=False)
            except sd.PortAudioError:
                raise AudioError("playback_device_unavailable") from None
    if failed:
        raise AudioError("playback_underflow")
    if cancelled():
        raise AudioError("playback_cancelled")
    if position != len(pcm):
        raise AudioError("playback_incomplete")


def main():
    codes = {
        "playback_cancelled": 2,
        "playback_device_unavailable": 3,
        "audio_dependency_or_portaudio_missing": 4,
        "playback_device_timeout": 5,
        "playback_underflow": 6,
    }
    try:
        device = None if sys.argv[1] == "default" else int(sys.argv[1])
        marker = Path(sys.argv[2])
        play_wave(sys.stdin.buffer.read(6_000_001), device, marker.exists)
    except AudioError as exc:
        raise SystemExit(codes.get(exc.code, 7)) from None


if __name__ == "__main__":
    main()
