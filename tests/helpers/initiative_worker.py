"""Separate-process initiative crash/race fixture; production code remains unchanged."""

import json
import os
import sys
from pathlib import Path
from uuid import UUID

from jarvis.cognitive_runtime import development_runtime
from jarvis.initiative import InitiativeError
from jarvis.storage.errors import StorageError

path, operation, target = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
runtime = development_runtime(path)
owner = runtime.credentials("owner")
engine = runtime.initiatives

if operation == "race_second":
    path.with_suffix(".second-ready").write_text("ready")
    sys.stdin.readline()
    try:
        result = engine.execute(UUID(target), session_token=owner.token)
        print(json.dumps({"state": result.state}))
    except (InitiativeError, StorageError) as exc:
        print(json.dumps({"error": exc.code}))
elif operation == "crash_accept":
    engine.process(session_token=owner.token, limit=100, after_accept=lambda _: os._exit(17))
elif operation == "crash_submit":
    original = engine._change

    def change(record, session, state, reason, **updates):
        if state == "EXECUTING":
            os._exit(17)
        return original(record, session, state, reason, **updates)

    engine._change = change
    engine.execute(UUID(target), session_token=owner.token)
elif operation in {"crash_effect", "race", "crash_completed"}:
    # Replace only the trusted fixture callable with an equivalent simulated handler.
    # Invocation still goes through the actual executor/checkpoint/policy path.
    registered = runtime.coordinator.context.registry._capabilities["development.simulated_notice"]
    effect_path = path.with_suffix(".effects")
    original_provider = registered.provider

    def provider(arguments):
        with effect_path.open("a") as stream:
            stream.write("invoked\n")
            stream.flush()
            os.fsync(stream.fileno())
        if operation == "crash_effect":
            os._exit(17)
        if operation == "race":
            path.with_suffix(".entered").write_text("ready")
            # Parent supplies release via stdin: no timing-based race assumption.
            sys.stdin.readline()
        return original_provider(arguments)

    from dataclasses import replace

    runtime.coordinator.context.registry._capabilities["development.simulated_notice"] = replace(
        registered, provider=provider
    )

    def checkpoint(task):
        if operation == "crash_completed":
            os._exit(17)

    try:
        result = engine.execute(
            UUID(target), session_token=owner.token, after_checkpoint=checkpoint
        )
        print(json.dumps({"state": result.state}))
    except (InitiativeError, StorageError) as exc:
        print(json.dumps({"error": exc.code}))
else:
    if operation == "recover":
        result = engine.recover(session_token=owner.token)
        print(json.dumps([r.model_dump(mode="json") for r in result]))
    elif operation == "execute":
        result = engine.execute(UUID(target), session_token=owner.token)
        print(result.model_dump_json())
    elif operation == "read":
        print(engine.get(UUID(target), session_token=owner.token).model_dump_json())
