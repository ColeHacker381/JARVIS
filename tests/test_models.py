import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from jarvis.core.cognitive_records import (
    CognitiveDecision,
    MemoryContext,
    ModelMessage,
    ModelRequest,
)
from jarvis.core.contracts import decode_contract
from jarvis.models import FakeModel, ModelError, ModelRouter, OpenAILocalModel
from jarvis.observability import InMemoryMetrics


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


def test_schema_mode_constrains_kernel_metadata_without_changing_the_contract():
    import jsonschema

    req = request()
    sent = []

    def server(http):
        sent.append(json.loads(http.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": CognitiveDecision(
                                kind="direct", text="Explanation"
                            ).model_dump_json()
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    model = OpenAILocalModel(response_format="json_schema", transport=httpx.MockTransport(server))
    assert isinstance(decode_contract(asyncio.run(model.complete(req))), CognitiveDecision)
    schema = sent[0]["response_format"]["json_schema"]["schema"]
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    action = {
        "schema_version": 1,
        "contract_type": "action_proposal",
        "session_id": str(req.session_id),
        "correlation_id": str(req.correlation_id),
        "capability_id": "home.lighting.set_brightness",
        "arguments": {"brightness": 25},
        "origin": "model",
        "source_ref": "local.openai",
    }
    decision = {
        "schema_version": 1,
        "contract_type": "cognitive_decision",
        "kind": "action",
        "text": "Proposal",
        "action": action,
    }
    validator.validate(decision)
    for field, value in (
        ("origin", "user"),
        ("task_id", str(uuid4())),
        ("session_id", str(uuid4())),
    ):
        with pytest.raises(jsonschema.ValidationError):
            validator.validate({**decision, "action": {**action, field: value}})


def test_truncated_output_reports_stage_and_actual_endpoint_token_counts():
    metrics = InMemoryMetrics()
    model = OpenAILocalModel(
        metrics=metrics,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": "private unfinished content"},
                            "finish_reason": "length",
                        }
                    ],
                    "usage": {"prompt_tokens": 400, "completion_tokens": 1024},
                },
            )
        ),
    )
    with pytest.raises(ModelError) as failure:
        asyncio.run(model.complete(request()))
    assert failure.value.code == "model_output_incomplete"
    assert failure.value.diagnostic == {"stage": "completion_limit"}
    assert ("model.output_tokens", 1024) in metrics.samples
    assert "private" not in failure.value.message


@pytest.mark.parametrize("format_name", ["xml", "tool_calls"])
def test_unsupported_response_format_never_invokes_transport(format_name):
    with pytest.raises(ValueError, match="response format"):
        OpenAILocalModel(response_format=format_name)


@pytest.mark.parametrize("has_memory", [False, True])
def test_generation_schema_requires_supplied_memory_for_grounded_kind(has_memory):
    import jsonschema

    from jarvis.model_output import decision_schema, structured_decision_schema

    memory = MemoryContext(
        memory_id=uuid4(),
        revision=0,
        content={"text": "A provisional project claim"},
        source_ref="test:claim",
        source_category="user_statement",
    )
    req = request().model_copy(update={"memories": (memory,) if has_memory else ()})
    validator = jsonschema.Draft202012Validator(
        structured_decision_schema(decision_schema(req, "local.openai"))
    )
    validator.validate(
        CognitiveDecision(kind="direct", text="General explanation").model_dump(mode="json")
    )
    grounded = CognitiveDecision(
        kind="memory_grounded",
        text="According to a provisional claim",
        memory_ids=(memory.memory_id,),
    ).model_dump(mode="json")
    if has_memory:
        validator.validate(grounded)
    else:
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(grounded)
    # Generation narrowing never turns an invalid citation-free decision into a reply.
    with pytest.raises(ValueError, match="grounded responses require memory references"):
        decode_contract(
            '{"schema_version":1,"contract_type":"cognitive_decision",'
            '"kind":"memory_grounded","text":"Untrusted output","memory_ids":[]}'
        )
