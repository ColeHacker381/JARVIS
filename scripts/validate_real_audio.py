"""Explicit real STT/TTS acceptance probe; no downloads, hardware or cloud access.

Run with `uv run python scripts/validate_real_audio.py --help` after installation.
All cognition in this probe is explicitly deterministic. Gemma/hardware acceptance
is a separate operator procedure. Reports contain only public/generated fixtures.
"""

import argparse
import array
import asyncio
import contextlib
import hashlib
import json
import random
import re
import resource
import subprocess
import sys
import time
import wave
from pathlib import Path
from uuid import uuid4

from jarvis.application import build_application
from jarvis.audio_frontend import AudioError
from jarvis.local_credentials import read_private_json, write_private_json
from jarvis.runtime_cli import initialize_development
from jarvis.runtime_config import RuntimeSettings
from jarvis.runtime_demo import api_data, running_runtime
from jarvis.speech import (
    SystemSpeechSynthesizer,
    WhisperCppRecognizer,
    pcm_wave,
    validate_wave,
)
from jarvis.voice_cli import host_control, read_stt_wav, segment_from_pcm

MODEL_HASHES = {
    "ggml-tiny.en.bin": "921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f",
    "ggml-base.en.bin": "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002",
}
UPSTREAM_COMMIT = "4979e04f5dcaccb36057e059bbaed8a2f5288315"
JFK_TEXT = (
    "And so my fellow Americans ask not what your country can do for you "
    "ask what you can do for your country"
)


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def word_error_rate(reference, observed):
    expected, actual = words(reference), words(observed)
    previous = list(range(len(actual) + 1))
    for i, word in enumerate(expected, 1):
        current = [i]
        for j, candidate in enumerate(actual, 1):
            current.append(
                min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (word != candidate))
            )
        previous = current
    return previous[-1] / len(expected) if expected else None


async def worker(spec):
    """One adapter invocation per worker gives an independent child CPU/RSS sample."""
    started = time.perf_counter()
    try:
        if spec["kind"] == "stt":
            result = await WhisperCppRecognizer(
                spec["binary"], spec["model"], timeout=spec.get("timeout", 60)
            ).transcribe(segment_from_pcm(read_stt_wav(Path(spec["wav"])), "fixture", uuid4()))
            result = result.model_dump(mode="json")
        else:
            audio = await SystemSpeechSynthesizer(
                executable=spec.get("binary"),
                voice=spec.get("voice"),
                rate=spec.get("rate", 175),
                timeout=spec.get("timeout", 60),
            ).synthesize(spec["text"])
            rate, pcm = validate_wave(audio.wav)
            if spec.get("output"):
                file = Path(spec["output"])
                file.write_bytes(audio.wav)
                file.chmod(0o600)
            result = {
                "provider": audio.provider,
                "simulated": audio.simulated,
                "duration_ms": audio.duration_ms,
                "rate_hz": rate,
                "audio_seconds": len(pcm) / (rate * 2),
            }
        result["status"] = "ok"
    except AudioError as exc:
        result = {"status": "failed", "error": exc.code}
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    result.update(
        elapsed_ms=(time.perf_counter() - started) * 1000,
        child_cpu_ms=(usage.ru_utime + usage.ru_stime) * 1000,
        child_peak_rss_kib=usage.ru_maxrss / 1024 if sys.platform == "darwin" else usage.ru_maxrss,
    )
    return result


def measure(spec):
    reply = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--worker", json.dumps(spec)],
        capture_output=True,
        text=True,
        check=True,
        timeout=90,
    )
    return json.loads(reply.stdout)


def mono_16k(data):
    rate, pcm = validate_wave(data)
    values = array.array("h", pcm)
    if sys.byteorder != "little":
        values.byteswap()
    # Linear resampling for these generated diagnostic fixtures only, not live capture.
    samples = []
    for i in range(int(len(values) * 16000 / rate)):
        position = i * rate / 16000
        left = int(position)
        fraction = position - left
        samples.append(
            round(values[left] * (1 - fraction) + values[min(left + 1, len(values) - 1)] * fraction)
        )
    converted = array.array("h", samples)
    if sys.byteorder != "little":
        converted.byteswap()
    return converted.tobytes()


def fixtures(args):
    with wave.open(str(args.whisper_root / "samples/jfk.wav"), "rb") as f:
        assert (f.getnchannels(), f.getsampwidth(), f.getframerate()) == (1, 2, 16000)
        clear = f.readframes(160000)  # Contract is <=10s; upstream sample is 11s.
    values = array.array("h", clear)
    rng = random.Random(10)
    cases = {
        "clear_jfk": (clear, JFK_TEXT, "public human JFK speech, first 10s"),
        "quiet_jfk": (
            array.array("h", [int(v * 0.05) for v in values]).tobytes(),
            JFK_TEXT,
            "JFK at 5% amplitude",
        ),
        "background_noise": (
            array.array(
                "h", [max(-32768, min(32767, int(v + rng.gauss(0, 1500)))) for v in values]
            ).tobytes(),
            JFK_TEXT,
            "JFK plus seeded Gaussian noise, sigma=1500 PCM units",
        ),
        "silence": (b"\0\0" * 48000, "", "3s digital silence"),
        "noise_only": (
            array.array(
                "h", [max(-32768, min(32767, int(rng.gauss(0, 1500)))) for _ in range(48000)]
            ).tobytes(),
            "",
            "3s seeded Gaussian noise",
        ),
    }
    synthesis = []
    for name, text, voice in (
        ("short_command", "Please adjust the lights to 25.", "en-us"),
        ("jarvis", "Jarvis, what does Slipstream use?", "en-us"),
        ("slipstream", "What do you remember about Slipstream?", "en-gb"),
    ):
        output = args.output / (name + "-tts.wav")
        result = measure(
            {
                "kind": "tts",
                "binary": args.tts_binary,
                "voice": args.tts_voice or voice,
                "text": text,
                "output": str(output),
            }
        )
        assert result["status"] == "ok", result
        synthesis.append(
            {"fixture": name, "voice": args.tts_voice or voice, "rate_wpm": 175, **result}
        )
        cases[name] = (
            mono_16k(output.read_bytes()),
            text,
            "actual local eSpeak/say synthesis; not human speech",
        )
    records = []
    for name, (pcm, reference, provenance) in cases.items():
        output = args.output / (name + ".wav")
        output.write_bytes(pcm_wave(pcm))
        output.chmod(0o600)
        records.append(
            {
                "name": name,
                "file": str(output),
                "sha256": digest(output),
                "ground_truth": reference,
                "provenance": provenance,
                "audio_seconds": len(pcm) / 32000,
            }
        )
    return records, synthesis


def pipeline(args):
    """Actual installed voice client -> owned child API -> core -> real local TTS."""
    settings = RuntimeSettings(data_dir=args.output / "runtime", model_provider="fake")
    manifest = initialize_development(settings)
    config = Path(manifest["config"])
    owner_file, guest_file = (Path(manifest["credentials"][k]["path"]) for k in ("owner", "guest"))
    tokens = {
        k: read_private_json(Path(manifest["credentials"][k]["path"]))["token"]
        for k in ("owner", "guest")
    }
    control = host_control(RuntimeSettings.load(config))
    from jarvis.core.audio_records import AudioSource

    for who in ("owner", "guest"):
        principal = control.identities.resolve_session(tokens[who]).principal_id
        for permission in (
            ("audio.process", "audio.sources.manage") if who == "owner" else ("audio.process",)
        ):
            control.permissions.grant(
                tokens["owner"], principal, permission, correlation_id=uuid4()
            )
        control.register(
            AudioSource(
                source_id=who + ".fixture",
                owner_id=principal,
                device_type="simulated",
                location="validation",
                consent_ref="consent:public-fixtures",
                enabled=True,
            ),
            session_token=tokens["owner"],
        )
    records = []
    with running_runtime(config, None, args.output / "runtime.log") as (client, _):
        configured = RuntimeSettings.load(config).model_copy(
            update={"runtime_port": client.base_url.port}
        )
        client_config = args.output / "client.json"
        write_private_json(client_config, configured.model_dump(mode="json"))
        conversation = None
        for who, name in (
            ("owner", "slipstream"),
            ("owner", "short_command"),
            ("owner", "slipstream"),
            ("guest", "short_command"),
        ):
            command = [
                sys.executable,
                "-m",
                "jarvis",
                "voice",
                "--config",
                str(client_config),
                "--credential-file",
                str(owner_file if who == "owner" else guest_file),
                "--source",
                who + ".fixture",
                "--wav",
                str(args.output / (name + ".wav")),
                "--stt-binary",
                str(args.whisper_root / "build/bin/whisper-cli"),
                "--stt-model",
                str(args.models[0]),
                "--no-playback",
            ]
            if who == "owner" and conversation:
                command += ["--conversation", conversation]
            if args.tts_voice:
                command += ["--tts-voice", args.tts_voice]
            result = subprocess.run(command, capture_output=True, text=True, timeout=90, check=True)
            events = [
                json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
            ]
            assert not any(e.get("status") == "failed" for e in events), events
            if who == "owner":
                conversation = events[0]["conversation_id"]
            records.append(
                {"principal": who, "fixture": name, "output": result.stdout.splitlines()}
            )
        headers = {"Authorization": "Bearer " + tokens["owner"]}
        history = api_data(
            client.get(f"/api/v1/conversations/{conversation}/history", headers=headers)
        )
        status = api_data(client.get("/api/v1/status", headers=headers))
        assert len(history) == 3 and status["simulated_invocations"]["lighting"] == 1
        assert api_data(
            client.get(
                f"/api/v1/conversations/{conversation}/history",
                headers={"Authorization": "Bearer " + tokens["guest"]},
            ),
            status=404,
        )["error"]["code"]
        # Assertions use actual API outcomes, not the recognizer's claimed intent.
        decisions = [
            json.loads(next(line for line in r["output"] if '"kind"' in line)) for r in records
        ]
        assert (
            decisions[0]["kind"] == "memory_grounded" and decisions[2]["kind"] == "memory_grounded"
        )
        assert decisions[1]["task_state"] == "COMPLETED" and decisions[3]["kind"] == "unsupported"
    with running_runtime(config, None, args.output / "runtime.log") as (client, _):
        continued = api_data(
            client.get(f"/api/v1/conversations/{conversation}/history", headers=headers)
        )
        assert len(continued) == 3
    # Separate explicit development scheduling choice exercises real pending state.
    app = build_application(args.output / "pending.sqlite3", bootstrap=True, defer_actions=True)
    owner = app.credentials("owner")
    conversation = app.conversations.start(session_token=owner.token)
    pending = asyncio.run(
        app.coordinator.respond(
            conversation.conversation_id, "Set lights to 25", session_token=owner.token
        )
    )
    assert pending.kind == "pending" and pending.task_state == "READY" and app.lighting.calls == []
    pending_speech = measure(
        {
            "kind": "tts",
            "binary": args.tts_binary,
            "text": pending.text,
            "voice": args.tts_voice,
            "output": str(args.output / "pending.wav"),
        }
    )
    assert pending_speech["status"] == "ok"
    app.audio.stop()
    return {
        "cognition": "DETERMINISTIC_FAKE, not Gemma",
        "hardware": "not accessed",
        "turns": records,
        "restart_turns": len(continued),
        "lighting_invocations": status["simulated_invocations"]["lighting"],
        "pending": {"response": pending.model_dump(mode="json"), "speech": pending_speech},
    }


async def cancellation(args):
    processes = []
    spawned = asyncio.Event()
    original = asyncio.create_subprocess_exec

    async def record(*a, **kw):
        child = await original(*a, **kw)
        processes.append(child)
        spawned.set()
        return child

    # Observe real child creation before cancelling; no timing-based guess.
    from unittest.mock import patch

    results = []
    for label, work in (
        (
            "stt",
            lambda: WhisperCppRecognizer(
                args.whisper_root / "build/bin/whisper-cli", args.models[0]
            ).transcribe(
                segment_from_pcm(read_stt_wav(args.output / "clear_jfk.wav"), "fixture", uuid4())
            ),
        ),
        (
            "tts",
            lambda: SystemSpeechSynthesizer(
                executable=args.tts_binary, voice=args.tts_voice
            ).synthesize("Local speech cancellation test."),
        ),
    ):
        spawned.clear()
        with patch("asyncio.create_subprocess_exec", record):
            task = asyncio.create_task(work())
            await asyncio.wait_for(spawned.wait(), 10)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        assert processes[-1].returncode is not None
        results.append(
            {
                "provider": label,
                "child_pid": processes[-1].pid,
                "exit_code": processes[-1].returncode,
                "reaped": True,
            }
        )
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--whisper-root", type=Path)
    p.add_argument("--models", nargs="+", type=Path)
    p.add_argument("--tts-binary")
    p.add_argument("--tts-voice", help="An installed local voice; required on macOS")
    p.add_argument("--output", type=Path)
    p.add_argument("--worker", help=argparse.SUPPRESS)
    args = p.parse_args()
    if args.worker:
        print(json.dumps(asyncio.run(worker(json.loads(args.worker)))))
        return
    if not args.whisper_root or not args.models or not args.output:
        p.error("--whisper-root, --models and a new --output directory are required")
    if sys.platform == "darwin" and not args.tts_voice:
        p.error("macOS requires --tts-voice selected from say -v '?' ")
    args.whisper_root = args.whisper_root.resolve()
    args.models = [model.resolve() for model in args.models]
    args.output = args.output.resolve()
    commit = subprocess.check_output(
        ["git", "-C", str(args.whisper_root), "rev-parse", "HEAD"], text=True
    ).strip()
    assert commit == UPSTREAM_COMMIT, "Unsupported whisper.cpp source commit"
    for model in args.models:
        assert digest(model) == MODEL_HASHES[model.name], "Model checksum mismatch"
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    fixture_records, tts = fixtures(args)
    stt = []
    report_path = args.output / "report.json"
    report = {
        "status": "incomplete",
        "stage": "recognition",
        "validation": "real STT/TTS, fake cognition; physical hardware not accessed",
        "whisper_commit": commit,
        "model_checksums": {m.name: digest(m) for m in args.models},
        "public_jfk_sha256": digest(args.whisper_root / "samples/jfk.wav"),
        "fixtures": fixture_records,
        "stt": stt,
        "tts": tts,
    }
    write_private_json(report_path, report)

    def save():
        report_path.write_text(json.dumps(report, indent=2) + "\n")

    for model in args.models:
        for fixture in fixture_records:
            result = measure(
                {
                    "kind": "stt",
                    "binary": str(args.whisper_root / "build/bin/whisper-cli"),
                    "model": str(model),
                    "wav": fixture["file"],
                }
            )
            stt.append(
                {
                    "fixture": fixture["name"],
                    "model_file": model.name,
                    **result,
                    "word_error_rate": word_error_rate(
                        fixture["ground_truth"], result.get("text", "")
                    ),
                }
            )
            print(model.name, fixture["name"], result.get("text", result.get("error")), flush=True)
            save()
    report["stage"] = "synthesis_and_failure_probes"
    save()
    for rate in (100, 250):
        tts.append(
            {
                "rate_wpm": rate,
                **measure(
                    {
                        "kind": "tts",
                        "binary": args.tts_binary,
                        "text": "This is a local speech rate test.",
                        "rate": rate,
                        "voice": args.tts_voice,
                    }
                ),
            }
        )
    for invalid in ("", "   ", "\u0000", "text\u001b"):
        result = measure({"kind": "tts", "binary": args.tts_binary, "text": invalid})
        assert result.get("error") == "tts_text_invalid", result
        tts.append({"invalid_input": repr(invalid), **result})
    timeout = measure(
        {
            "kind": "stt",
            "binary": str(args.whisper_root / "build/bin/whisper-cli"),
            "model": str(args.models[0]),
            "wav": str(args.output / "clear_jfk.wav"),
            "timeout": 0.001,
        }
    )
    report["timeout"] = timeout
    save()
    assert timeout.get("error") == "speech_provider_timeout", timeout
    tts_timeout = measure(
        {
            "kind": "tts",
            "binary": args.tts_binary,
            "voice": args.tts_voice,
            "text": "Timeout test.",
            "timeout": 0.001,
        }
    )
    report["tts_timeout"] = tts_timeout
    save()
    assert tts_timeout.get("error") == "speech_provider_timeout", tts_timeout
    invalid_voice = measure(
        {
            "kind": "tts",
            "binary": args.tts_binary,
            "text": "Voice test.",
            "voice": "jarvis-invalid-voice",
        }
    )
    report["invalid_voice"] = invalid_voice
    save()
    assert invalid_voice.get("error") == (
        "tts_voice_unavailable" if sys.platform == "darwin" else "speech_provider_failed"
    ), invalid_voice
    corrupt = args.output / "deliberately-invalid-model.bin"
    corrupt.write_bytes(b"invalid model: explicit failure probe, not speech weights")
    invalid_model = measure(
        {
            "kind": "stt",
            "binary": str(args.whisper_root / "build/bin/whisper-cli"),
            "model": str(corrupt),
            "wav": str(args.output / "clear_jfk.wav"),
        }
    )
    assert invalid_model.get("error") == "speech_provider_failed", invalid_model
    report["invalid_model"] = invalid_model
    save()
    report["cancellation"] = asyncio.run(cancellation(args))
    report["stage"] = "pipeline"
    save()
    report["pipeline"] = pipeline(args)
    report["stage"] = "completed"
    report["status"] = "passed"
    save()
    print("Measured report:", report_path)


if __name__ == "__main__":
    main()
