"""Native lifecycle fakes and owned subprocess supervision, without audio hardware."""

import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest

from jarvis.audio_frontend import AudioError
from jarvis.audio_playback import play_wave
from jarvis.speech import LocalAudioPlayback, SynthesizedAudio, SystemSpeechSynthesizer, pcm_wave


def backend(*, cancel_at=None, failure=None):
    calls = []
    outputs = []
    owner = threading.get_ident()
    state = {"cancelled": False}

    class Stop(Exception):
        pass

    class Abort(Exception):
        pass

    class Stream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.running = True
            calls.append("open")
            if failure == "open":
                raise OSError()

        def start(self):
            assert threading.get_ident() == owner
            calls.append("start")

        @property
        def active(self):
            if not self.running:
                return False
            if cancel_at is not None and len(outputs) == cancel_at:
                state["cancelled"] = True
            out = bytearray(6)
            try:
                self.callback(
                    out, 3, None, SimpleNamespace(output_underflow=failure == "underflow")
                )
            except (Stop, Abort):
                self.running = False
            outputs.append(bytes(out))
            return self.running

        def stop(self, *, ignore_errors):
            assert threading.get_ident() == owner and not self.running
            assert not ignore_errors
            calls.append("stop")
            if failure == "stop":
                raise OSError()

        def abort(self, *, ignore_errors):
            assert threading.get_ident() == owner and not ignore_errors
            calls.append("abort")

        def close(self, *, ignore_errors):
            assert threading.get_ident() == owner and not ignore_errors
            calls.append("close")
            if failure == "close":
                raise OSError()

    return (
        SimpleNamespace(
            RawOutputStream=Stream, CallbackStop=Stop, CallbackAbort=Abort, PortAudioError=OSError
        ),
        calls,
        outputs,
        state,
    )


def test_callback_drains_pads_and_closes_repeatedly_on_owner_thread():
    sd, calls, outputs, state = backend()
    pcm = b"\x01\x02" * 5
    for _ in range(2):
        play_wave(pcm_wave(pcm), 4, lambda: state["cancelled"], backend=sd)
    assert calls == ["open", "start", "stop", "close"] * 2
    assert outputs == [pcm[:6], pcm[6:] + b"\0\0"] * 2


def test_callback_cancellation_aborts_then_closes():
    sd, calls, _, state = backend(cancel_at=1)
    with pytest.raises(AudioError, match="playback_cancelled"):
        play_wave(pcm_wave(b"\x01\x02" * 100), 4, lambda: state["cancelled"], backend=sd)
    assert calls == ["open", "start", "abort", "close"]


@pytest.mark.parametrize("failure", ["open", "stop", "close", "underflow"])
def test_device_errors_are_visible_and_opened_streams_are_closed(failure):
    sd, calls, _, _ = backend(failure=failure)
    with pytest.raises(AudioError, match="underflow" if failure == "underflow" else "unavailable"):
        play_wave(pcm_wave(b"\x01\x02" * 5), 4, lambda: False, backend=sd)
    assert ("close" in calls) == (failure != "open")


CREATE_PROCESS = asyncio.create_subprocess_exec


def child_launcher(monkeypatch, code):
    original = CREATE_PROCESS
    children = []

    async def launch(*args, **kwargs):
        child = await original(sys.executable, "-c", code, args[-1], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr("jarvis.speech.asyncio.create_subprocess_exec", launch)
    return children


def test_parent_success_requires_child_exit_and_errors_are_reported(monkeypatch):
    async def run():
        children = child_launcher(
            monkeypatch, "import sys; assert sys.stdin.buffer.read()[:4] == b'RIFF'"
        )
        player = LocalAudioPlayback(4)
        for _ in range(2):
            await player.play(SynthesizedAudio(pcm_wave(b"\0\0" * 100), "test"))
        assert len(children) == 2 and all(c.returncode == 0 for c in children)
        child_launcher(monkeypatch, "import sys; sys.stdin.buffer.read(); sys.exit(3)")
        with pytest.raises(AudioError, match="device_unavailable"):
            await player.play(SynthesizedAudio(pcm_wave(b"\0\0" * 100), "test"))

    asyncio.run(run())


@pytest.mark.parametrize("task_cancel", [False, True])
def test_parent_cancellation_waits_for_worker_cleanup(monkeypatch, task_cancel):
    async def run():
        children = child_launcher(
            monkeypatch,
            "import sys,time; from pathlib import Path; sys.stdin.buffer.read(); "
            "p=Path(sys.argv[1]);\nwhile not p.exists(): time.sleep(.005)\nsys.exit(2)",
        )
        player = LocalAudioPlayback(4)
        work = asyncio.create_task(player.play(SynthesizedAudio(pcm_wave(b"\0\0" * 100), "test")))
        while not children:
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.03)
        if task_cancel:
            work.cancel()
        else:
            player.cancel()
        with pytest.raises(asyncio.CancelledError if task_cancel else AudioError):
            await work
        assert children[0].returncode == 2

    asyncio.run(run())


def test_hung_cleanup_is_killed_reaped_and_never_success(monkeypatch):
    async def run():
        children = child_launcher(
            monkeypatch, "import sys,time; sys.stdin.buffer.read(); time.sleep(60)"
        )
        player = LocalAudioPlayback(4)
        task = asyncio.create_task(player.play(SynthesizedAudio(pcm_wave(b"\0\0" * 100), "test")))
        while not children:
            await asyncio.sleep(0.005)
        player.cancel()
        with pytest.raises(AudioError, match="playback_cleanup_timeout"):
            await task
        assert children[0].returncode is not None and children[0].returncode != 0

    asyncio.run(run())


def test_cancel_during_child_launch_reaps_spawned_process(monkeypatch):
    async def run():
        created = asyncio.Event()
        children = []

        async def launch(*args, **kwargs):
            child = await CREATE_PROCESS(
                sys.executable, "-c", "import time; time.sleep(60)", **kwargs
            )
            children.append(child)
            created.set()
            await asyncio.sleep(0.05)
            return child

        monkeypatch.setattr("jarvis.speech.asyncio.create_subprocess_exec", launch)
        task = asyncio.create_task(
            LocalAudioPlayback(4).play(SynthesizedAudio(pcm_wave(b"\0\0" * 100), "test"))
        )
        await created.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert children[0].returncode is not None

    asyncio.run(run())


def test_exact_macos_voice_is_required_before_synthesis(tmp_path):
    executable = tmp_path / "say"
    executable.write_text(f"""#!{sys.executable}
import sys,wave
if sys.argv[1:] == ['-v','?']:
 print('Samantha   en_US # hello')
 print('Eddy (English (US))   en_US # hello')
else:
 assert sys.argv[sys.argv.index('-v')+1] == 'Eddy (English (US))'
 with wave.open(sys.argv[sys.argv.index('-o')+1], 'wb') as f:
  f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(b'\\0\\0'*100)
""")
    executable.chmod(0o700)
    for voice in ("jarvis-invalid-voice", "Eddy", "en_US"):
        tts = SystemSpeechSynthesizer(executable=str(executable), voice=voice)
        tts.macos = True
        with pytest.raises(AudioError, match="tts_voice_unavailable"):
            asyncio.run(tts.synthesize("hello"))
    tts = SystemSpeechSynthesizer(executable=str(executable), voice="Eddy (English (US))")
    tts.macos = True
    assert asyncio.run(tts.synthesize("hello")).provider == "local.say"
