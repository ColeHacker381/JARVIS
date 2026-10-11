"""Explicit trusted-host audio controls and push-to-talk client of the existing local API."""

import argparse
import asyncio
import base64
import json
import threading
from pathlib import Path
from uuid import UUID, uuid4

import httpx

from jarvis.audio_frontend import AudioError
from jarvis.conversations import ConversationService
from jarvis.core.audio_records import AudioSource, SpeechSegment
from jarvis.core.time import utc_now
from jarvis.local_credentials import LocalDevelopmentIdentityRepository, read_private_json
from jarvis.runtime_config import RuntimeSettings
from jarvis.runtime_demo import api_data
from jarvis.speech import (
    FakeAudioPlayback,
    FakeSpeechRecognizer,
    FakeSpeechSynthesizer,
    LocalAudioPlayback,
    LocalMicrophone,
    SystemSpeechSynthesizer,
    WhisperCppRecognizer,
    sounddevice_module,
    validate_wave,
)
from jarvis.storage.audio import SQLiteAudioStore
from jarvis.storage.conversations import SQLiteConversationStore
from jarvis.storage.security import SQLitePermissionStore
from jarvis.storage.sqlite import SQLiteAuditSink, SQLiteDatabase
from jarvis.voice import AudioControl

VOICE_COMMANDS = {"voice", "audio", "voice-demo", "ambient-demo"}


def parser():
    p = argparse.ArgumentParser(description="JARVIS local voice; explicit microphone consent only")
    p.add_argument("command", choices=sorted(VOICE_COMMANDS))
    p.add_argument(
        "operation",
        nargs="?",
        choices=["devices", "register", "mute", "sessions", "mic-test", "stt-test", "tts-test"],
    )
    p.add_argument("--config", type=Path)
    p.add_argument("--credential-file", type=Path)
    p.add_argument("--source", default="local.microphone")
    p.add_argument("--location", default="computer")
    p.add_argument("--consent-ref")
    p.add_argument("--continuous", action="store_true")
    p.add_argument("--memory-candidates", action="store_true")
    p.add_argument("--device", type=int, help="explicit input device index (voice/mic-test)")
    p.add_argument("--output-device", type=int)
    p.add_argument("--seconds", type=float, default=5)
    p.add_argument("--stt-binary", type=Path)
    p.add_argument("--stt-model", type=Path)
    p.add_argument("--language", default="en")
    p.add_argument("--tts-voice")
    p.add_argument("--tts-rate", type=int, default=175)
    p.add_argument("--conversation", type=UUID)
    p.add_argument(
        "--fake", action="store_true", help="DEVELOPMENT simulation, no real speech reasoning"
    )
    p.add_argument("--text", help="literal fake transcript or standalone TTS text")
    p.add_argument("--wav", type=Path, help="local mono PCM WAV for stt-test")
    p.add_argument("--no-playback", action="store_true")
    return p


def host_control(settings):
    # Reuse current persisted authority/metadata; no second cognitive runtime/executor.
    database = SQLiteDatabase(settings.resolved_runtime_database_path)
    identities = LocalDevelopmentIdentityRepository(database)
    audit = SQLiteAuditSink(database)
    permissions = SQLitePermissionStore(identities, audit)
    conversations = ConversationService(SQLiteConversationStore(database, identities, audit))
    return AudioControl(SQLiteAudioStore(database, identities), permissions, conversations)


def segment_from_pcm(pcm, source_id, session_id):
    from datetime import timedelta

    now = utc_now()
    return SpeechSegment(
        source_id=source_id,
        voice_session_id=session_id,
        correlation_id=uuid4(),
        started_at=now,
        ended_at=now + timedelta(seconds=len(pcm) / 32000),
        first_sequence=0,
        last_sequence=max(0, len(pcm) // 3200 - 1),
        pcm_base64=base64.b64encode(pcm).decode(),
        vad_confidence=0.5,
    )


async def read_line(prompt=""):
    import select
    import sys

    print(prompt, end="", flush=True)
    while not select.select([sys.stdin], [], [], 0)[0]:
        await asyncio.sleep(0.05)
    line = sys.stdin.readline()
    if not line:
        raise EOFError()
    return line.rstrip("\n")


def providers(args):
    if args.fake:
        return (
            FakeSpeechRecognizer([args.text or "JARVIS, what does Slipstream use?"]),
            FakeSpeechSynthesizer(),
            FakeAudioPlayback(),
        )
    if args.stt_binary is None or args.stt_model is None:
        raise AudioError("local_stt_configuration_required")
    return (
        WhisperCppRecognizer(args.stt_binary, args.stt_model, language=args.language),
        SystemSpeechSynthesizer(voice=args.tts_voice, rate=args.tts_rate),
        LocalAudioPlayback(device=args.output_device),
    )


async def voice_client(args):
    if args.config is None or args.credential_file is None:
        raise AudioError("runtime_config_and_client_credential_required")
    if not args.fake and args.device is None:
        raise AudioError("explicit_microphone_device_required")
    settings = RuntimeSettings.load(args.config)
    token = read_private_json(args.credential_file)["token"]
    control = host_control(settings)
    control.source(args.source, session_token=token)
    stt, tts, playback = providers(args)
    host = "[::1]" if settings.runtime_host == "::1" else settings.runtime_host
    async with httpx.AsyncClient(
        base_url=f"http://{host}:{settings.runtime_port}",
        headers={"Authorization": "Bearer " + token},
        timeout=90,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        conversation_id = args.conversation
        if conversation_id is None:
            conversation_id = UUID(
                api_data(await client.post("/api/v1/conversations", json={"schema_version": 1}))[
                    "conversation_id"
                ]
            )
        else:
            api_data(await client.get(f"/api/v1/conversations/{conversation_id}/history"))
        print(
            json.dumps(
                {
                    "conversation_id": str(conversation_id),
                    "mode": "DEVELOPMENT_FAKE" if args.fake else "local_push_to_talk",
                }
            )
        )
        session = control.start((args.source,), conversation_id, session_token=token)
        microphone = (
            LocalMicrophone(args.device, max_seconds=args.seconds) if not args.fake else None
        )
        try:
            while True:
                if args.fake:
                    pcm = b"\0\0" * 1600
                else:
                    answer = await read_line("Enter to speak; /exit to quit: ")
                    if answer.strip() == "/exit":
                        break
                    stop = threading.Event()
                    # Visible bounded capture; press Enter again to release push-to-talk.
                    print("Microphone ON; Enter to stop (maximum configured duration).", flush=True)
                    capture = asyncio.create_task(
                        asyncio.to_thread(
                            microphone.capture,
                            stop=stop,
                            status=lambda level: print(
                                f"input_level={level:.3f}", end="\r", flush=True
                            ),
                        )
                    )
                    try:
                        await read_line()
                        stop.set()
                        pcm = await asyncio.shield(capture)
                    except BaseException:
                        microphone.cancel()
                        stop.set()
                        await capture
                        raise
                    print("\nMicrophone OFF", flush=True)
                segment = segment_from_pcm(pcm, args.source, session.voice_session_id)
                try:
                    control.validate(args.source, session.voice_session_id, token)
                    result = await stt.transcribe(segment)
                    print("You: " + result.text)
                    # The existing authenticated API performs all current memory/action checks.
                    control.validate(args.source, session.voice_session_id, token)
                    response = api_data(
                        await client.post(
                            f"/api/v1/conversations/{conversation_id}/messages",
                            json={
                                "schema_version": 1,
                                "request_id": str(segment.segment_id),
                                "text": result.text,
                            },
                        )
                    )
                    print("JARVIS: " + response["text"])
                    print(
                        json.dumps(
                            {
                                "kind": response["kind"],
                                "task_state": response.get("task_state"),
                                "request_id": str(segment.segment_id),
                                "model": response.get("provenance"),
                            }
                        )
                    )
                    audio = await tts.synthesize(response["text"])
                    control.validate(args.source, session.voice_session_id, token)
                    if not args.no_playback:
                        # Enter cancels playback; stdin worker is polled, never left blocked.
                        import select

                        playback.cancelled = False
                        task = asyncio.create_task(playback.play(audio))
                        while not task.done():
                            if (
                                not args.fake
                                and select.select([__import__("sys").stdin], [], [], 0)[0]
                            ):
                                __import__("sys").stdin.readline()
                                playback.cancel()
                            await asyncio.sleep(0.05)
                        await task
                except (AudioError, AssertionError, httpx.HTTPError) as exc:
                    code = exc.code if isinstance(exc, AudioError) else "voice_api_unavailable"
                    print(json.dumps({"status": "failed", "error": {"code": code}}))
                if args.fake:
                    break
                # Reopen a short-lived capture session without changing the durable conversation.
                control.close(session.voice_session_id, session_token=token, delete=True)
                session = control.start((args.source,), conversation_id, session_token=token)
        finally:
            if microphone is not None:
                microphone.cancel()
            playback.cancel()
            control.close(session.voice_session_id, session_token=token, delete=True)


def run_voice_cli(argv):
    args = parser().parse_args(argv)
    try:
        if args.command in {"voice-demo", "ambient-demo"}:
            from jarvis.voice_demo import run_voice_demo

            print(json.dumps(run_voice_demo(ambient=args.command == "ambient-demo")))
            return
        if args.command == "voice":
            asyncio.run(voice_client(args))
            return
        if args.operation == "devices":
            print(json.dumps({"devices": [dict(x) for x in sounddevice_module().query_devices()]}))
        elif args.operation == "mic-test":
            if args.device is None or not args.consent_ref:
                raise AudioError("explicit_device_and_consent_required")
            print("Microphone ON (bounded test)", flush=True)
            mic = LocalMicrophone(args.device, max_seconds=args.seconds)
            pcm = mic.capture(status=lambda level: print(f"input_level={level:.3f}", end="\r"))
            print(json.dumps({"status": "ok", "duration_ms": len(pcm) / 32, "microphone": "OFF"}))
        elif args.operation == "tts-test":
            if not args.text:
                raise AudioError("tts_text_required")

            async def speak():
                audio = await SystemSpeechSynthesizer(
                    voice=args.tts_voice, rate=args.tts_rate
                ).synthesize(args.text)
                if not args.no_playback:
                    await LocalAudioPlayback(args.output_device).play(audio)
                print(
                    json.dumps(
                        {"status": "ok", "provider": audio.provider, "bytes": len(audio.wav)}
                    )
                )

            asyncio.run(speak())
        elif args.operation == "stt-test":
            if args.wav is None or args.wav.stat().st_size > 320044:
                raise AudioError("bounded_stt_wav_required")
            rate, pcm = validate_wave(args.wav.read_bytes())
            if rate != 16000:
                raise AudioError("stt_requires_mono_16khz_pcm16")
            stt, _, _ = providers(args)
            result = asyncio.run(stt.transcribe(segment_from_pcm(pcm, args.source, uuid4())))
            print(result.model_dump_json())
        elif args.operation in {"register", "mute", "sessions"}:
            if args.config is None or args.credential_file is None:
                raise AudioError("runtime_config_and_client_credential_required")
            control = host_control(RuntimeSettings.load(args.config))
            token = read_private_json(args.credential_file)["token"]
            if args.operation == "sessions":
                print(
                    json.dumps(
                        {
                            "sessions": [
                                s.model_dump(mode="json")
                                for s in control.sessions(session_token=token)
                            ]
                        }
                    )
                )
            else:
                owner = control.identities.resolve_session(token).principal_id
                if args.operation == "register":
                    if not args.consent_ref:
                        raise AudioError("explicit_source_consent_required")
                    source = AudioSource(
                        source_id=args.source,
                        owner_id=owner,
                        device_type="microphone",
                        location=args.location,
                        consent_ref=args.consent_ref,
                        enabled=True,
                        continuous_allowed=args.continuous,
                        memory_candidates_allowed=args.memory_candidates,
                    )
                else:
                    source = control.source(args.source, session_token=token).model_copy(
                        update={"enabled": False}
                    )
                control.register(source, session_token=token)
                print(
                    json.dumps(
                        {
                            "status": "configured",
                            "source_id": source.source_id,
                            "enabled": source.enabled,
                        }
                    )
                )
        else:
            raise AudioError("audio_operation_required")
    except (
        AudioError,
        httpx.HTTPError,
        ValueError,
        OSError,
        PermissionError,
        AssertionError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": {
                        "code": getattr(exc, "code", "voice_configuration_or_provider_failed")
                    },
                }
            )
        )
        raise SystemExit(1) from None
    except (KeyboardInterrupt, EOFError):
        print(json.dumps({"status": "stopped", "microphone": "OFF"}))
