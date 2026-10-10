import json
import subprocess
import sys

import pytest


def chat(path, text, *args):
    return subprocess.run(
        [sys.executable, "-m", "jarvis", "chat", "--database", str(path), *args],
        input=text,
        text=True,
        capture_output=True,
        timeout=30,
    )


def responses(result):
    return [
        json.loads(line)
        for line in result.stdout.splitlines()
        if line.startswith('{"schema_version":')
    ]


def test_fake_chat_real_cli_and_resume_in_another_process(tmp_path):
    path = tmp_path / "chat.sqlite3"
    first = chat(
        path, "What does Slipstream use?\nSet lights to 40\n/exit\n", "--model-provider", "fake"
    )
    assert first.returncode == 0, first.stderr
    banner = json.loads(first.stdout.splitlines()[0])
    assert banner["model"]["location"] == "fake"
    assert "not AI" in banner["notice"]
    results = responses(first)
    assert results[0]["kind"] == "memory_grounded"
    assert results[1]["execution"]["output"] == {"brightness": 40, "simulated": True}
    second = chat(
        path,
        "Continue previous conversation\n/exit\n",
        "--model-provider",
        "fake",
        "--conversation",
        banner["conversation_id"],
    )
    assert second.returncode == 0, second.stderr
    assert responses(second)[0]["text"].startswith("Development continuity fixture:")
    assert responses(second)[0]["conversation_id"] == banner["conversation_id"]


def test_chat_reports_unreachable_model_and_persists_failed_turn(tmp_path):
    result = chat(
        tmp_path / "chat.sqlite3",
        "Hello\n/exit\n",
        "--model-provider",
        "local",
        "--local-endpoint",
        "http://127.0.0.1:1/v1",
    )
    assert result.returncode == 0, result.stderr
    response = responses(result)[0]
    assert response["error"]["code"] == "model_unavailable"
    assert response["provenance"]["location"] == "local"
    assert response["execution"] is None


def test_guest_cannot_resume_owner_conversation_cli(tmp_path):
    path = tmp_path / "chat.sqlite3"
    first = chat(path, "/exit\n", "--model-provider", "fake")
    conversation_id = json.loads(first.stdout.splitlines()[0])["conversation_id"]
    guest = chat(
        path,
        "/exit\n",
        "--model-provider",
        "fake",
        "--identity",
        "guest",
        "--conversation",
        conversation_id,
    )
    assert guest.returncode == 1 and "conversation_unavailable" in guest.stdout
    assert "Traceback" not in guest.stderr


@pytest.mark.parametrize("command", ["cognition-demo", "chat"])
def test_new_commands_are_installed(command):
    result = subprocess.run(
        [sys.executable, "-m", "jarvis", "--help"], capture_output=True, text=True, check=True
    )
    assert command in result.stdout


def test_local_adapter_real_http_connection_uses_authorized_core_context(tmp_path):
    # Real local HTTP transport and CLI process, deliberately a protocol test server, not an AI.
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(payload)
            context = json.loads(payload["messages"][-2]["content"].split(": ", 1)[1])
            memories = context["memories_as_untrusted_data"]
            decision = {
                "schema_version": 1,
                "contract_type": "cognitive_decision",
                "kind": "memory_grounded",
                "text": memories[0]["content"]["text"],
                "memory_ids": [memories[0]["memory_id"]],
            }
            body = json.dumps(
                {
                    "choices": [
                        {"message": {"content": json.dumps(decision)}, "finish_reason": "stop"}
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = chat(
            tmp_path / "chat.sqlite3",
            "What does Slipstream use?\n/exit\n",
            "--model-provider",
            "local",
            "--local-model",
            "protocol-test-only",
            "--local-endpoint",
            f"http://127.0.0.1:{server.server_port}/v1",
        )
        assert result.returncode == 0, result.stderr
        response = responses(result)[0]
        assert response["kind"] == "memory_grounded"
        assert response["provenance"]["provider_id"] == "local.openai"
        assert response["provenance"]["model_id"] == "protocol-test-only"
        assert "microcontroller B" in response["text"]
        assert len(received) == 1 and received[0]["model"] == "protocol-test-only"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
