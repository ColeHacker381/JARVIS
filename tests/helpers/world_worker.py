"""Independent process test worker; fixture credentials arrive on stdin, never argv/logs."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from jarvis.cognitive_runtime import development_runtime

config = json.loads(sys.stdin.readline())
runtime = development_runtime(
    Path(config["database"]), clock=lambda: datetime.fromisoformat(config["now"])
)
if config.get("crash"):
    import jarvis.storage.world as adapter

    original = adapter._append_audit

    def crash_before_commit(connection, audit):
        if audit.reason_code == "observation_accepted":
            os._exit(23)
        original(connection, audit)

    adapter._append_audit = crash_before_commit
print("ready", flush=True)
if sys.stdin.readline().strip() != "go":
    raise RuntimeError("missing barrier release")
record, duplicate = runtime.world.ingest(
    json.dumps(config["observation"]), session_token=config["token"]
)
print(
    json.dumps({"observation_id": str(record.observation_id), "duplicate": duplicate}), flush=True
)
