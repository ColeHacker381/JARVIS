import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from jarvis.core.cognitive_records import CognitiveDecision, ModelMessage, ModelRequest
from jarvis.core.contracts import decode_contract
from jarvis.models import FakeModel, ModelError, ModelRouter, OpenAILocalModel


def request():
    return ModelRequest(
        request_id=uuid4(),
        session_id=uuid4(),
        correlation_id=uuid4(),
        messages=(ModelMessage(role="user", content="Hello"),),
    )


@pytest.mark.parametrize("local", [False, True])
def test_fake_and_local_contracts_and_trusted_provenance(local):
    payloads = []

    def server(req):
        payloads.append(json.loads(req.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": CognitiveDecision(
                                kind="direct", text="Local reply"
                            ).model_dump_json()
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    provider = OpenAILocalModel(transport=httpx.MockTransport(server)) if local else FakeModel()
    output, provenance = asyncio.run(
        ModelRouter({"test": provider}, selected="test").complete(
            request(),
            session_token="never-sent-to-provider",
        )
    )
    assert isinstance(decode_contract(output), CognitiveDecision)
    assert provenance.provider_id == provider.identity.provider_id
    assert provenance.model_id == provider.identity.model_id
    assert provenance.duration_ms >= 0
    if local:
        assert payloads[0]["response_format"] == {"type": "json_object"}
        assert payloads[0]["stream"] is False
        assert "never-sent-to-provider" not in json.dumps(payloads)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.example.com/v1",
        "http://192.168.1.8/v1",
        "http://example.com/v1",
        "http://127.0.0.1.evil.invalid/v1",
        "http://user:secret@127.0.0.1/v1",
        "http://127.0.0.1/v1?token=secret",
        "http://127.0.0.1/v1#fragment",
    ],
)
def test_local_endpoint_cannot_be_used_for_cloud_or_credentials(endpoint):
    with pytest.raises(ValueError):
        OpenAILocalModel(endpoint=endpoint)


@pytest.mark.parametrize("endpoint", ["http://localhost:11434/v1", "http://[::1]:8080/v1"])
def test_loopback_endpoint_is_pinned(endpoint):
    model = OpenAILocalModel(endpoint=endpoint)
    assert "localhost" not in model.endpoint


@pytest.mark.parametrize(
    "response,expected",
    [
        (httpx.Response(302, headers={"Location": "https://example.com"}), "model_http_error"),
        (httpx.Response(401, text="secret provider details"), "model_http_error"),
        (httpx.Response(500), "model_http_error"),
        (httpx.Response(200, text="not JSON"), "model_output_invalid"),
        (httpx.Response(200, json={"choices": []}), "model_output_invalid"),
        (
            httpx.Response(
                200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "length"}]}
            ),
            "model_output_incomplete",
        ),
        (httpx.Response(200, content=b"x" * 262145), "model_response_too_large"),
    ],
)
def test_local_http_errors_are_normalized_without_details(response, expected):
    model = OpenAILocalModel(transport=httpx.MockTransport(lambda _: response))
    with pytest.raises(ModelError) as exc:
        asyncio.run(model.complete(request()))
    assert exc.value.code == expected
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize(
    "failure,code",
    [
        (httpx.ConnectError("private prompt"), "model_unavailable"),
        (httpx.ReadTimeout("private prompt"), "model_timeout"),
    ],
)
def test_transport_failures(failure, code):
    def fail(_):
        raise failure

    model = OpenAILocalModel(transport=httpx.MockTransport(fail))
    with pytest.raises(ModelError, match=code):
        asyncio.run(model.complete(request()))


def test_router_timeout_cancels_provider_without_fallback():
    class Slow(FakeModel):
        cancelled = False

        async def complete(self, req):
            try:
                await asyncio.sleep(10)
            finally:
                self.cancelled = True

    slow = Slow()
    with pytest.raises(ModelError, match="model_timeout"):
        asyncio.run(
            ModelRouter({"slow": slow}, selected="slow", timeout=0.01).complete(
                request(),
                session_token="token",
            )
        )
    assert slow.cancelled


def test_unknown_provider_is_explicitly_unavailable():
    with pytest.raises(ModelError, match="model_not_configured"):
        asyncio.run(ModelRouter({}).complete(request(), session_token="token"))
