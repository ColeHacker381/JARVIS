"""Trusted process hooks; barriers observe actual claims/checkpoint/effect windows."""

import json
import os
import sys
from pathlib import Path
from uuid import UUID

from jarvis.cognitive_runtime import development_runtime
from jarvis.core.time import utc_now
from jarvis.initiative_demo import FixtureClock
from jarvis.notifications import NotificationError

mode, database, delivery_id = sys.argv[1:4]
database = Path(database)
clock = FixtureClock(utc_now())
rt = development_runtime(database, clock=clock)
owner = rt.credentials("owner")
if mode == "contender":
    print("ready", flush=True)
    assert sys.stdin.readline().strip() == "continue"


def effect(request, result):
    with database.with_suffix(".effects").open("a") as file:
        file.write(str(request.delivery_id) + "\n")
        file.flush()
        os.fsync(file.fileno())
    if mode == "race":
        print("effect-held", flush=True)
        assert sys.stdin.readline().strip() == "continue"
    elif mode == "effect-crash":
        os._exit(17)


def checkpoint(task):
    if mode == "attempt-crash" and task.steps[0].state == "RUNNING":
        os._exit(18)
    if mode == "result-crash" and task.steps[0].state == "COMPLETED":
        os._exit(19)


for channel in rt.notifications.channels.values():
    if mode == "attempt-crash" and hasattr(channel, "after_effect"):

        def before_effect(request):
            os._exit(18)

        channel.send = before_effect
    if hasattr(channel, "after_effect"):
        channel.after_effect = effect
try:
    result = rt.notifications.deliver(
        UUID(delivery_id), session_token=owner.token, after_checkpoint=checkpoint
    )
    print(
        json.dumps(
            {
                "state": result.state,
                "calls": sum(
                    len(getattr(c, "calls", ())) for c in rt.notifications.channels.values()
                ),
            }
        ),
        flush=True,
    )
except NotificationError as exc:
    print(json.dumps({"error": exc.code}), flush=True)
