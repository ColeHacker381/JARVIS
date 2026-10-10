"""Independent-process crash checkpoints. Trusted development fixture, no persisted tokens."""

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path
from uuid import UUID

from jarvis.cognitive_runtime import development_runtime
from jarvis.models import FakeModel, ModelRouter

mode, directory = sys.argv[1], Path(sys.argv[2])
info = json.load(sys.stdin)
runtime = development_runtime(directory / "journal.sqlite3", bootstrap=mode.startswith("seed"))
credentials = runtime.credentials("owner")
if mode.startswith("seed"):
    conversation_id = runtime.conversations.start(session_token=credentials.token).conversation_id
    info["conversation_id"] = str(conversation_id)
else:
    conversation_id = UUID(info["conversation_id"])

if mode == "seed-before-link":
    submit = runtime.tasks.submit

    def interrupt(*args, **kwargs):
        submit(*args, **kwargs)
        # Persist routing information for the harness, never a bearer credential.
        (directory / "info.json").write_text(json.dumps(info))
        os._exit(75)

    runtime.tasks.submit = interrupt
elif mode == "seed-ambiguous":
    registration = runtime.coordinator.context.registry._capabilities[
        "home.lighting.set_brightness"
    ]

    def external(arguments):
        with sqlite3.connect(directory / "effects.sqlite3") as connection:
            connection.execute("CREATE TABLE effects(brightness INTEGER)")
            connection.execute("INSERT INTO effects VALUES(?)", (arguments["brightness"],))
        (directory / "info.json").write_text(json.dumps(info))
        os._exit(76)

    # Trusted provider substitution only; metadata stays identical and non-idempotent.
    from dataclasses import replace

    runtime.coordinator.context.registry._capabilities["home.lighting.set_brightness"] = replace(
        registration, provider=external
    )
elif mode == "seed-before-response":
    save = runtime.conversations._save

    def interrupt_response(turn, **changes):
        if changes.get("state") == "COMPLETED":
            (directory / "info.json").write_text(json.dumps(info))
            os._exit(77)
        return save(turn, **changes)

    runtime.conversations._save = interrupt_response
elif mode == "reopen":

    class NeverAgain(FakeModel):
        async def complete(self, request):
            raise AssertionError("accepted intent must not repeat model reasoning")

    runtime.coordinator.models = ModelRouter({"fake": NeverAgain()}, selected="fake")

response = asyncio.run(
    runtime.coordinator.respond(
        conversation_id,
        "Set lights to 50",
        session_token=credentials.token,
        request_id=UUID(info["request_id"]),
    )
)
print(
    json.dumps(
        {
            "info": info,
            "response": response.model_dump(mode="json"),
            "lighting_calls": runtime.lighting.calls,
            "pid": os.getpid(),
            "history": [
                t.model_dump(mode="json")
                for t in runtime.conversations.recent(
                    conversation_id, session_token=credentials.token
                )
            ],
        }
    )
)
