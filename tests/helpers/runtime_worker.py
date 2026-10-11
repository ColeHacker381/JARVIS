"""Controlled process death after a real persisted task attempt, before provider entry."""

import os
import sys
from pathlib import Path

from jarvis.application import build_application
from jarvis.core.task_records import TaskStep
from jarvis.local_credentials import (
    LocalDevelopmentIdentityRepository,
    read_private_json,
    write_private_json,
)
from jarvis.runtime_config import RuntimeSettings

settings = RuntimeSettings.load(Path(sys.argv[1]))
token = read_private_json(settings.worker_credentials_file)["tokens"][0]
app = build_application(
    settings.resolved_runtime_database_path,
    identities_factory=LocalDevelopmentIdentityRepository,
    initialize=False,
    provider="fake",
)
lighting = sys.argv[2] == "lighting"
task = app.tasks.submit(
    (
        TaskStep(
            step_id="attempt",
            capability_id="home.lighting.set_brightness" if lighting else "development.echo",
            arguments={"brightness": 20} if lighting else {"text": "recover"},
        ),
    ),
    session_token=token,
)
write_private_json(Path(sys.argv[3]), {"task_id": str(task.task_id)})


original_save = app.tasks.store.save


def crash(record, **kwargs):
    original_save(record, **kwargs)
    if record.steps[0].state == "RUNNING":
        os._exit(31)


app.tasks.store.save = crash
app.tasks.run(task.task_id, session_token=token)
raise RuntimeError("expected persisted attempt was not reached")
