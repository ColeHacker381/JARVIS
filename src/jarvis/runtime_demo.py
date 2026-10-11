"""Executable quickstart: real local HTTP, scheduler, SIGTERM and separate-process restart."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx

from jarvis.core.time import utc_now
from jarvis.core.world_records import ObservationInput, SensorPayload
from jarvis.local_credentials import read_private_json, write_private_json
from jarvis.runtime_config import RuntimeSettings


def _environment():
    values = os.environ.copy()
    for name in RuntimeSettings.model_fields:
        values.pop("JARVIS_" + name.upper(), None)
    return values


def _wait(check, *, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.05)
    raise RuntimeError("runtime_demo_deadline")


@contextmanager
def running_runtime(
    config, port, log_path, *, expected_exit_codes=(0, -signal.SIGTERM), require_graceful=True
):
    with log_path.open("a") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "jarvis", "serve", "--config", str(config)],
            stdout=log,
            stderr=log,
            env=_environment(),
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                timeout=10,
                trust_env=False,
                follow_redirects=False,
            ) as client:

                def ready():
                    if process.poll() is not None:
                        raise RuntimeError("runtime_demo_startup_failed")
                    try:
                        r = client.get("/health/ready")
                        return r.status_code == 200 and r.json()["ready"]
                    except httpx.HTTPError:
                        return False

                _wait(ready)
                yield client, process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                    raise RuntimeError("runtime_demo_shutdown_timeout") from None
            if process.returncode not in expected_exit_codes:
                raise RuntimeError("runtime_demo_process_failed")
            if require_graceful and "Application shutdown complete" not in log_path.read_text():
                raise RuntimeError("runtime_demo_shutdown_incomplete")


def run_quickstart(directory, *, verbose=False):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    command = [
        sys.executable,
        "-m",
        "jarvis",
        "runtime-init",
        "--data-dir",
        str(directory),
        "--model-provider",
        "fake",
        "--demo-fixtures",
        "--port",
        str(port),
    ]
    initialized = subprocess.run(
        command, capture_output=True, text=True, timeout=30, check=True, env=_environment()
    )
    manifest = json.loads(initialized.stdout)
    settings = RuntimeSettings.load(Path(manifest["config"])).model_copy(
        update={
            "initiative_interval_seconds": 0.1,
            "notification_interval_seconds": 0.1,
            "recovery_interval_seconds": 0.2,
            "maintenance_interval_seconds": 0.2,
            "worker_batch_size": 4,
        }
    )
    config = directory / "demo-runtime.json"
    write_private_json(config, settings.model_dump(mode="json"))
    owner = read_private_json(Path(manifest["credentials"]["owner"]["path"]))["token"]
    guest = read_private_json(Path(manifest["credentials"]["guest"]["path"]))["token"]
    log_path = directory / "runtime.log"
    # The log is private; access logging is disabled and no credential is printed.
    log_path.touch(mode=0o600)
    headers = {"Authorization": "Bearer " + owner}
    guest_headers = {"Authorization": "Bearer " + guest}

    def request(client, method, path, body=None, *, auth=headers, status=200):
        reply = client.request(method, "/api/v1" + path, json=body, headers=auth)
        if reply.status_code != status:
            raise RuntimeError("runtime_demo_api_failure")
        return reply.json().get("data", reply.json())

    with running_runtime(config, port, log_path) as (client, first_process):
        assert client.get("/api/v1/tasks").status_code == 401
        conversation = request(client, "POST", "/conversations", {"schema_version": 1})
        conversation_id = conversation["conversation_id"]
        message_path = f"/conversations/{conversation_id}/messages"
        answer = request(
            client,
            "POST",
            message_path,
            {"schema_version": 1, "request_id": str(uuid4()), "text": "What does Slipstream use?"},
        )
        assert answer["kind"] == "memory_grounded" and "microcontroller B" in answer["text"]
        action_body = {"schema_version": 1, "request_id": str(uuid4()), "text": "Set lights to 25"}
        action = request(client, "POST", message_path, action_body)
        assert action["task_state"] == "COMPLETED" and action["execution"]["status"] == "success"
        task_id, memory_id = action["task_id"], answer["memory_ids"][0]
        task = request(client, "GET", f"/tasks/{task_id}")
        assert task["completed_steps"] == 1
        observation = ObservationInput(
            source_id="runtime.garage.sensor",
            entity_id="garage",
            location="garage",
            observation_type="motion",
            observed_at=utc_now(),
            confidence=0.9,
            payload=SensorPayload(value=True),
        )
        accepted = request(
            client,
            "POST",
            "/world/observations",
            {"schema_version": 1, "observation": observation.model_dump(mode="json")},
        )
        assert accepted["record"]["observation_id"] == str(observation.observation_id)
        world = request(client, "GET", "/world?entity=garage")
        assert world and world[0]["status"] == "observed"
        state_id = world[0]["state_id"]
        initiative = _wait(
            lambda: next(
                (
                    i
                    for i in request(client, "GET", "/initiatives")
                    if i["state"] == "AWAITING_APPROVAL"
                ),
                None,
            )
        )
        initiative_id = initiative["initiative_id"]
        notice = _wait(
            lambda: next(
                (
                    n
                    for n in request(client, "GET", "/notifications")["records"]
                    if str(n["source"]["source_id"]) == initiative_id
                ),
                None,
            )
        )
        notification_id = notice["notification_id"]
        deliveries = _wait(
            lambda: (
                (d if all(x["state"] == "DELIVERED" for x in d) else None)
                if (d := request(client, "GET", f"/notifications/{notification_id}/deliveries"))
                else None
            )
        )
        current = request(client, "GET", f"/notifications/{notification_id}")
        acknowledged = request(
            client,
            "POST",
            f"/notifications/{notification_id}/acknowledge",
            {"schema_version": 1, "expected_revision": current["revision"]},
        )
        assert acknowledged["inbox_state"] == "ACKNOWLEDGED"
        assert (
            request(client, "GET", f"/initiatives/{initiative_id}")["state"] == "AWAITING_APPROVAL"
        )
        for path in (
            f"/tasks/{task_id}",
            f"/conversations/{conversation_id}/history",
            f"/memory/{memory_id}",
            f"/world/{state_id}",
            f"/notifications/{notification_id}",
        ):
            request(client, "GET", path, auth=guest_headers, status=404)
        assert request(client, "GET", "/notifications", auth=guest_headers)["records"] == []
        initial_status = request(client, "GET", "/status")
        assert initial_status["simulated_invocations"]["lighting"] == 1
        assert initial_status["simulated_invocations"]["notice"] == 0
        first_pid = first_process.pid

    with running_runtime(config, port, log_path) as (client, second_process):
        assert second_process.pid != first_pid
        history = request(client, "GET", f"/conversations/{conversation_id}/history")
        assert len(history) == 2
        assert request(client, "GET", f"/memory/{memory_id}")["content"]["text"].endswith(
            "microcontroller B"
        )
        assert request(client, "GET", f"/tasks/{task_id}")["state"] == "COMPLETED"
        assert request(client, "GET", f"/world/{state_id}")["status"] == "observed"
        assert (
            request(client, "GET", f"/initiatives/{initiative_id}")["state"] == "AWAITING_APPROVAL"
        )
        assert (
            request(client, "GET", f"/notifications/{notification_id}")["inbox_state"]
            == "ACKNOWLEDGED"
        )
        replay = request(client, "POST", message_path, action_body)
        assert replay["task_id"] == task_id and replay["task_state"] == "COMPLETED"
        after = request(client, "GET", "/status")
        assert after["simulated_invocations"] == {"lighting": 0, "notice": 0}
        resumed = request(
            client,
            "POST",
            message_path,
            {"schema_version": 1, "request_id": str(uuid4()), "text": "Continue with Slipstream"},
        )
        assert "microcontroller B" in resumed["text"]
    summary = {
        "status": "ok",
        "mode": "offline_simulated",
        "processes": 2,
        "conversation": answer["kind"],
        "task": task["state"],
        "initiative": initiative["state"],
        "notification": acknowledged["inbox_state"],
        "deliveries": [d["state"] for d in deliveries],
        "guest_isolation": "verified",
        "restart_persistence": "verified",
        "replayed_lighting_invocations": after["simulated_invocations"]["lighting"],
        "database": manifest["database"],
    }
    if verbose:
        summary["references"] = {
            "conversation_id": conversation_id,
            "task_id": task_id,
            "memory_id": memory_id,
            "state_id": state_id,
            "initiative_id": initiative_id,
            "notification_id": notification_id,
            "audit_id": notice["audit_id"],
        }
        summary["runtime"] = after
    return summary


def run_runtime_demo(*, verbose=False):
    return run_quickstart(Path.cwd() / ".jarvis" / "runtime-demo" / str(uuid4()), verbose=verbose)
