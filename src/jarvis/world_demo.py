"""Real two-process offline world/evidence demonstration; no physical sensor or AI claims."""

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import mkdtemp
from uuid import UUID, uuid4

from jarvis.cognitive_runtime import development_runtime
from jarvis.config import Settings
from jarvis.core.contracts import AccessScope
from jarvis.core.world_records import SourceRegistration, decode_world
from jarvis.sensors import FakeCamera, FakeDeviceStatus, FakeOccupancySensor


class DemoClock:
    def __init__(self, now=None):
        self.now = now or datetime(2026, 10, 10, 12, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def _worker(path, stage):
    clock = DemoClock()
    runtime = development_runtime(path / "journal.sqlite3", bootstrap=stage == "first", clock=clock)
    owner, guest = runtime.credentials("owner"), runtime.credentials("guest")
    if stage == "first":
        registrations, tokens = {}, {}
        adapters = {
            "camera": FakeCamera("camera", "porch", "porch"),
            "occupancy": FakeOccupancySensor("occupancy", "porch", "porch"),
            "contradiction": FakeOccupancySensor("contradiction", "porch", "porch"),
            "device": FakeDeviceStatus("device", "garage-camera", "garage"),
        }
        for source_id, sensor in adapters.items():
            principal = runtime.identities.create_principal_for_testing(f"Simulated {source_id}")
            credentials = runtime.identities.establish_session_for_testing(principal.principal_id)
            tokens[source_id] = credentials.token
            runtime.permissions.grant(
                owner.token, principal.principal_id, "world.ingest", correlation_id=uuid4()
            )
            registrations[source_id] = runtime.world.register_source(
                SourceRegistration(
                    source_id=source_id,
                    principal_id=principal.principal_id,
                    access_scope=AccessScope(owner_id=runtime.identities.owner_id),
                    observation_types=tuple(
                        dict.fromkeys((sensor.observation_type, "availability"))
                    ),
                    entities=tuple(dict.fromkeys((sensor.entity_id, source_id))),
                    location=sensor.location,
                ),
                session_token=owner.token,
            )
        camera = adapters["camera"].observation(observed_at=clock())
        runtime.world.ingest(camera, session_token=tokens["camera"])
        inferred = runtime.world.query(entity="porch", attr="occupancy", session_token=owner.token)[
            0
        ]
        adapters["occupancy"].publish(
            runtime.world, session_token=tokens["occupancy"], observed_at=clock()
        )
        supported = runtime.world.query(
            entity="porch", attr="occupancy", session_token=owner.token
        )[0]
        adapters["device"].publish(
            runtime.world, session_token=tokens["device"], observed_at=clock()
        )
        clock.advance(1)
        adapters["camera"].disconnect(
            runtime.world, session_token=tokens["camera"], observed_at=clock()
        )
        disconnected = runtime.world.query(
            entity="porch", attr="occupancy", session_token=owner.token
        )[0]
        assert disconnected.value is True and disconnected.status == "observed"
        adapters["contradiction"].publish(
            runtime.world, session_token=tokens["contradiction"], observed_at=clock(), value=False
        )
        conflict = runtime.world.query(entity="porch", attr="occupancy", session_token=owner.token)[
            0
        ]
        assert conflict.status == "conflicting" and conflict.value is None
        conversation = runtime.conversations.start(session_token=owner.token)
        response = asyncio.run(
            runtime.coordinator.respond(
                conversation.conversation_id, "Is someone on the porch?", session_token=owner.token
            )
        )
        assert response.kind == "world_grounded" and "conflicting" in response.text
        guest_states = runtime.world.query(entity="porch", session_token=guest.token)
        assert guest_states == ()
        clock.advance(61)
        stale = runtime.world.query(entity="porch", attr="occupancy", session_token=owner.token)[0]
        assert stale.status == "stale" and stale.value is None
        metadata = {
            "camera": camera.model_dump(mode="json"),
            "source": registrations["camera"].model_dump(mode="json"),
            "conversation_id": str(conversation.conversation_id),
            "now": clock().isoformat(),
            "state": stale.model_dump(mode="json"),
        }
        (path / "continuation.json").write_text(json.dumps(metadata))
        output = {
            "stage": "first",
            "camera_inference": inferred.model_dump(mode="json"),
            "independent_support": supported.model_dump(mode="json"),
            "after_disconnect": disconnected.model_dump(mode="json"),
            "conflict": conflict.model_dump(mode="json"),
            "stale": stale.model_dump(mode="json"),
            "guest_world_state": [s.model_dump(mode="json") for s in guest_states],
            "cognitive_response": response.model_dump(mode="json"),
            "audit_ids": [str(a.audit_id) for a in runtime.audit.records()],
        }
    else:
        metadata = json.loads((path / "continuation.json").read_text())
        clock.now = datetime.fromisoformat(metadata["now"])
        source = decode_world(json.dumps(metadata["source"]))
        credential = runtime.identities.establish_session_for_testing(source.principal_id)
        evidence = runtime.world.query(
            entity="porch", history=True, session_token=owner.token, limit=20
        )
        current = runtime.world.query(entity="porch", attr="occupancy", session_token=owner.token)[
            0
        ]
        assert current.revision == metadata["state"]["revision"] and current.status == "stale"
        _, duplicate = runtime.world.ingest(
            json.dumps(metadata["camera"]), session_token=credential.token
        )
        replayed = runtime.world.query(entity="porch", attr="occupancy", session_token=owner.token)[
            0
        ]
        assert duplicate and replayed.revision == current.revision
        history = runtime.conversations.recent(
            UUID(metadata["conversation_id"]), session_token=owner.token
        )
        response = asyncio.run(
            runtime.coordinator.respond(
                UUID(metadata["conversation_id"]),
                "How certain is porch occupancy now?",
                session_token=owner.token,
            )
        )
        assert "stale" in response.text and response.kind == "world_grounded"
        output = {
            "stage": "reopened",
            "observation_count": len(evidence),
            "observation_ids": [str(e.observation_id) for e in evidence],
            "world_state": replayed.model_dump(mode="json"),
            "duplicate": duplicate,
            "conversation_turns_before_continuation": len(history),
            "cognitive_response": response.model_dump(mode="json"),
            "audit_ids": [str(a.audit_id) for a in runtime.audit.records()],
        }
    output["metrics"] = runtime.metrics.samples
    runtime.identities.close_session_for_testing(owner.token)
    runtime.identities.close_session_for_testing(guest.token)
    return output


def run_world_demo():
    parent = Settings.from_environment().data_dir / "world-demo"
    parent.mkdir(parents=True, exist_ok=True)
    path = Path(mkdtemp(prefix="run-", dir=parent))
    stages = []
    for stage in ("first", "reopened"):
        process = subprocess.run(
            [sys.executable, "-m", "jarvis.world_demo", str(path), stage],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        stages.append(json.loads(process.stdout))
    return {
        "status": "ok",
        "development_only": True,
        "fake_model": True,
        "database_path": str(path / "journal.sqlite3"),
        "stages": stages,
    }


if __name__ == "__main__":
    print(json.dumps(_worker(Path(sys.argv[1]), sys.argv[2])))
