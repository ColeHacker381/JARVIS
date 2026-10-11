"""A real loopback OpenAI HTTP protocol server, explicitly not genuine inference."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from jarvis.core.cognitive_records import CognitiveDecision
from jarvis.local_api import create_api
from jarvis.local_credentials import read_private_json
from jarvis.models import OpenAILocalModel
from jarvis.runtime_cli import initialize_development
from jarvis.runtime_config import RuntimeSettings
from jarvis.runtime_service import RuntimeService


@pytest.mark.parametrize(
    "payload",
    [
        {"data": [{"id": "different"}]},
        {"data": "invalid"},
        {"data": [None]},
        {"wrong": []},
        {"data": [{"id": "x" * 128}] * 1025},
    ],
)
def test_model_probe_rejects_malformed_unavailable_or_oversized_catalog(payload):
    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    provider = OpenAILocalModel(transport=httpx.MockTransport(server))
    assert not asyncio.run(provider.available())
    assert len(requests) == 1 and requests[0].method == "GET"
    assert "authorization" not in requests[0].headers and not requests[0].content


def test_local_provider_health_and_cognition_use_existing_contract(tmp_path):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            assert self.path == "/v1/models"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"protocol-model"}]}')

        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(data)
            serialized = CognitiveDecision(
                kind="direct", text="Local protocol fixture response"
            ).model_dump_json()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps({"choices": [{"message": {"content": serialized}}]}).encode()
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = RuntimeSettings(
            data_dir=tmp_path / "dev",
            model_provider="local",
            local_model_endpoint=f"http://127.0.0.1:{server.server_port}/v1",
            local_model_name="protocol-model",
        )
        manifest = initialize_development(settings)
        runtime = RuntimeService(RuntimeSettings.load(Path(manifest["config"])))
        token = read_private_json(Path(manifest["credentials"]["owner"]["path"]))["token"]
        headers = {"Authorization": "Bearer " + token}
        with TestClient(create_api(runtime), base_url="http://127.0.0.1:8765") as client:
            assert client.get("/health/ready").json()["model_available"]
            conv = client.post(
                "/api/v1/conversations", headers=headers, json={"schema_version": 1}
            ).json()["data"]
            response = client.post(
                f"/api/v1/conversations/{conv['conversation_id']}/messages",
                headers=headers,
                json={"schema_version": 1, "request_id": str(uuid4()), "text": "Hello"},
            )
            result = response.json()["data"]
            assert result["text"] == "Local protocol fixture response"
            assert result["provenance"]["provider_id"] == "local.openai"
            assert result["provenance"]["model_id"] == "protocol-model"
            assert len(seen) == 1 and seen[0]["model"] == "protocol-model"
            assert seen[0]["response_format"]["type"] == "json_object"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
