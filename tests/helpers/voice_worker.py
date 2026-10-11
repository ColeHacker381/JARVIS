"""Actual speech-triggered crash after a non-idempotent simulated effect."""

import asyncio
import json
import os
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from uuid import UUID

from jarvis.application import build_application
from jarvis.speech import FakeSpeechRecognizer
from jarvis.voice_cli import segment_from_pcm

mode, directory = sys.argv[1], Path(sys.argv[2])
info = json.load(sys.stdin)
app = build_application(directory / "audio.sqlite3", initialize=False)
actor = app.credentials("owner")
segment = segment_from_pcm(b"\x00\x20" * 1600, "living", UUID(info["voice_session_id"]))
segment = segment.model_copy(update={"segment_id": UUID(info["request_id"])})
if mode == "crash":
    registration = app.registry._capabilities["home.lighting.set_brightness"]

    def effect(arguments):
        with sqlite3.connect(directory / "voice-effects.sqlite3") as c:
            c.execute("CREATE TABLE effects(brightness INTEGER)")
            c.execute("INSERT INTO effects VALUES(?)", (arguments["brightness"],))
        os._exit(76)

    app.registry._capabilities["home.lighting.set_brightness"] = replace(
        registration, provider=effect
    )
response = asyncio.run(
    app.audio.process(
        segment,
        session_token=actor.token,
        interaction_token=actor.token,
        recognizer=FakeSpeechRecognizer(["JARVIS, set lights to 25"]),
    )
)
print(
    json.dumps(
        {
            "response": response["response"].model_dump(mode="json"),
            "lighting_calls": app.lighting.calls,
            "pid": os.getpid(),
        }
    )
)
