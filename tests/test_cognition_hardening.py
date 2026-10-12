"""Deterministic enforcement and Qwen-compatible wire fixtures, not real inference."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from jarvis.application import build_application
from jarvis.core.cognitive_records import CognitiveDecision
from jarvis.core.contracts import Provenance
from jarvis.core.memory_records import MemoryDraft, MemoryQuery
from jarvis.local_api import create_api
from jarvis.local_credentials import read_private_json
from jarvis.models import FakeModel
from jarvis.observability import JsonFormatter
from jarvis.runtime_cli import initialize_development
from jarvis.runtime_config import RuntimeSettings
from jarvis.runtime_demo import api_data
from jarvis.runtime_service import RuntimeService

BRAINSTORM = (
    "Based on your general knowledge, not just like your stored memory, suggest three ways "
    "we could use audio codecs in the Slipstream project."
)


class Spy(FakeModel):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.output = None

    async def complete(self, request):
        self.requests.append(request)
        return self.output if self.output is not None else await super().complete(request)


@pytest.fixture
def core(tmp_path):
    model = Spy()
    app = build_application(tmp_path / "journal.sqlite3", bootstrap=True, fake=model)
    tokens = {n: app.credentials(n).token for n in ("owner", "guest")}
    convs = {n: app.conversations.start(session_token=t).conversation_id for n, t in tokens.items()}
    yield app, model, tokens, convs
    app.audio.stop()


def respond(core, text, who="owner", **kwargs):
    app, _, tokens, convs = core
    return asyncio.run(
        app.coordinator.respond(convs[who], text, session_token=tokens[who], **kwargs)
    )


def memory(text, **kwargs):
    return MemoryDraft(
        kind="semantic",
        content={"text": text},
        source=Provenance(category="user_statement", source_ref="test:project-claim"),
        consent_ref="consent:test",
        **kwargs,
    )


def test_explicit_recall_retrieves_authorized_fixture_with_provenance(core):
    result = respond(
        core, "JARVIS, what do you remember about my Slipstream motorcycle concept project?"
    )
    app, model, _, _ = core
    assert model.requests[-1].memories[0].source_ref == "development.cognition.fixture"
    assert model.requests[-1].memories[0].source_category == "import"
    assert result.kind == "memory_grounded" and "not verified project truth" in result.text
    assert not app.lighting.calls
    instructions = " ".join(m.content for m in model.requests[-1].messages[:2])
    assert "general technical knowledge" in instructions
    assert "assumptions" in instructions and "contradictory" in instructions


def test_late_project_keyword_retrieves_genuine_memory_but_not_fixture(core):
    app, model, tokens, _ = core
    genuine = app.memory.create(
        memory("Slipstream hardware is undecided"), session_token=tokens["owner"]
    )
    respond(core, BRAINSTORM)
    assert {m.memory_id for m in model.requests[-1].memories} == {genuine.memory_id}
    assert "microcontroller B" not in model.requests[-1].model_dump_json()


def test_fixture_recall_history_cannot_become_engineering_context(core):
    app, model, _, _ = core
    result = respond(core, "What do you remember about Slipstream?")
    assert result.kind == "memory_grounded" and "unverified" in result.text
    respond(core, BRAINSTORM)
    request = model.requests[-1]
    assert not request.memories
    assert "microcontroller B" not in request.model_dump_json()
    assert "development.cognition.fixture" not in request.model_dump_json()
    assert not app.lighting.calls


def test_implicit_other_projects_selects_only_bounded_current_permitted_projects(core):
    app, model, tokens, _ = core
    current = app.memory.create(
        memory("Orion has a proposed helmet audio concept", project_ref="orion-without-prefix"),
        session_token=tokens["owner"],
    )
    app.memory.create(memory("unrelated private fact"), session_token=tokens["owner"])
    deleted = app.memory.create(
        memory("Deleted project", project_ref="gone"), session_token=tokens["owner"]
    )
    app.memory.delete(deleted.memory_id, expected_revision=0, session_token=tokens["owner"])
    respond(core, "Could we use it in our other projects?")
    snippets = model.requests[-1].memories
    assert current.memory_id in {m.memory_id for m in snippets}
    assert deleted.memory_id not in {m.memory_id for m in snippets}
    assert "unrelated private fact" not in model.requests[-1].model_dump_json()
    respond(core, "Could we use it in our other projects?", "guest")
    assert model.requests[-1].memories == ()
    assert app.memory.search_projects(session_token=tokens["guest"]).records == ()


def test_followup_sources_remain_available_and_revocation_removes_context(core):
    app, model, tokens, _ = core
    app.memory.create(
        memory("Slipstream has provisional design goals"), session_token=tokens["owner"]
    )
    first = respond(core, "What are the Slipstream design goals?")
    respond(core, "What about that design?")
    assert first.memory_ids[0] in {m.memory_id for m in model.requests[-1].memories}
    app.memory.delete(first.memory_ids[0], expected_revision=0, session_token=tokens["owner"])
    respond(core, "What about that design?")
    assert not model.requests[-1].memories
    assert "microcontroller B" not in model.requests[-1].model_dump_json()


def test_partial_and_contradictory_claims_keep_uncertainty_without_automatic_resolution(core):
    app, model, tokens, _ = core
    for label in ("A", "C"):
        app.memory.create(
            memory(
                f"Slipstream provisional controller {label}",
                confidence=0.3,
            ),
            session_token=tokens["owner"],
        )
    respond(core, BRAINSTORM)
    snippets = model.requests[-1].memories
    assert len(snippets) == 2
    assert sum(m.confidence == 0.3 for m in snippets) == 2
    assert all(app.memory.get(m.memory_id, session_token=tokens["owner"]) for m in snippets)


@pytest.mark.parametrize(
    "output,stage",
    [
        ("private malformed output", "decision_json"),
        (
            '{"schema_version":1,"contract_type":"cognitive_decision",'
            '"kind":"failure","text":"private-value"}',
            "decision_classification",
        ),
        ('{"kind":"direct","text":"private-value"}', "decision_envelope"),
        (
            '{"schema_version":2,"contract_type":"cognitive_decision",'
            '"kind":"direct","text":"private-value"}',
            "decision_version",
        ),
        (
            '{"schema_version":1,"contract_type":"cognitive_decision",'
            '"kind":"brainstorm","text":"private-value","private-key":"private-value"}',
            "decision_schema",
        ),
        (
            '{"schema_version":1,"contract_type":"cognitive_decision",'
            '"kind":"memory_grounded","text":"private-value",'
            '"memory_ids":[],"world_ids":[]}',
            "decision_classification",
        ),
    ],
)
def test_invalid_output_stage_and_shape_are_content_free_and_fail_closed(
    core, output, stage, caplog
):
    app, model, _, _ = core
    model.output = output
    result = respond(core, BRAINSTORM)
    assert result.error.code == "model_output_invalid"
    assert stage in result.error.message
    assert "private-value" not in result.error.message and "private-key" not in result.error.message
    assert "private malformed" not in result.error.message
    assert not app.tasks.store.all() and not app.lighting.calls
    record = next(r for r in caplog.records if r.getMessage() == "model.output_rejected")
    formatted = JsonFormatter().format(record)
    assert (
        stage in formatted and "private-value" not in formatted and "private-key" not in formatted
    )
    if '"kind":"memory_grounded"' in output:
        assert record.model_diagnostic["validation_rule"] == "memory_references_required"
        assert record.model_diagnostic["reference_counts"] == {"memory_ids": 0, "world_ids": 0}


@pytest.mark.parametrize(
    "field,stage", [("origin", "proposal_origin"), ("task_id", "proposal_task_id")]
)
def test_proposal_context_rejections_are_independently_diagnosed(core, field, stage):
    app, model, _, _ = core
    respond(core, "Set lights to 25")
    request = model.requests[-1]
    decision = json.loads(asyncio.run(FakeModel().complete(request)))
    decision["action"][field] = "rule" if field == "origin" else str(uuid4())
    model.output = json.dumps(decision)
    # Bind to the new request rather than importing old session/correlation identity.
    original = model.complete

    async def bound(req):
        d = json.loads(model.output)
        d["action"]["correlation_id"] = str(req.correlation_id)
        model.output = json.dumps(d)
        return await original(req)

    model.complete = bound
    result = respond(core, "Set lights to 30")
    assert result.error.code == "proposal_context_invalid" and stage in result.error.message
    assert app.lighting.calls == [25] and len(app.tasks.store.all()) == 1


@pytest.mark.parametrize("who", ["owner", "guest"])
@pytest.mark.parametrize(
    "text",
    [
        "The lights have been set to 25 brightness.",
        "The lights are now at 25.",
        "Setting the simulated lights to 25.",
    ],
)
def test_conversational_lighting_completion_is_rejected_without_execution(core, who, text):
    app, model, _, _ = core
    model.output = CognitiveDecision(kind="direct", text=text).model_dump_json()
    result = respond(core, "Set the simulated lights to 25.", who)
    assert result.error.code == "unverified_action_claim"
    assert not app.tasks.store.all() and not app.lighting.calls


def test_lighting_explanation_remains_a_direct_reply(core):
    _, model, _, _ = core
    model.output = CognitiveDecision(
        kind="direct", text="Brightness controls how much light is emitted."
    ).model_dump_json()
    assert respond(core, "Explain how lighting works.").kind == "direct"


@pytest.mark.parametrize("kind", ["unsupported", "clarification"])
def test_action_claim_guard_preserves_non_action_permission_explanations(core, kind):
    app, model, _, _ = core
    model.output = CognitiveDecision(
        kind=kind, text="Setting the simulated lights requires permission you do not have."
    ).model_dump_json()
    result = respond(core, "Set the simulated lights to 25.", "guest")
    assert result.kind == kind and result.error is None
    assert not app.tasks.store.all() and not app.lighting.calls


def test_unsupported_kind_cannot_hide_a_completed_action_claim(core):
    app, model, _, _ = core
    model.output = CognitiveDecision(
        kind="unsupported", text="The lights have been set to 25."
    ).model_dump_json()
    result = respond(core, "Set the simulated lights to 25.", "guest")
    assert result.error.code == "unverified_action_claim"
    assert not app.tasks.store.all() and not app.lighting.calls


def test_new_runtime_can_omit_fixture_without_changing_existing_records(tmp_path):
    manifest = initialize_development(
        RuntimeSettings(data_dir=tmp_path / "new", model_provider="fake"), seed_memory_fixture=False
    )
    runtime = RuntimeService(RuntimeSettings.load(Path(manifest["config"])))
    token = read_private_json(Path(manifest["credentials"]["owner"]["path"]))["token"]
    with TestClient(create_api(runtime), base_url="http://127.0.0.1:8765"):
        assert not runtime.application.memory.search(MemoryQuery(), session_token=token).records


@pytest.mark.parametrize("unsupported_fact", [False, True])
def test_grounded_recommendations_and_missing_established_facts_keep_existing_contract(
    core, unsupported_fact
):
    app, model, tokens, _ = core
    record = app.memory.create(
        memory("Slipstream is an undecided design study"), session_token=tokens["owner"]
    )
    model.output = json.dumps(
        {
            "schema_version": 1,
            "contract_type": "cognitive_decision",
            "kind": "clarification" if unsupported_fact else "memory_grounded",
            "text": (
                "No retrieved record establishes an audio codec selection for Slipstream."
                if unsupported_fact
                else "Stored claim: test:project-claim describes an undecided design study. "
                "Assumption: helmet audio is an optional feature. Recommendations from general "
                "knowledge: intercom compression, "
                "ride recording, and helmet streaming; these are options, not project decisions."
            ),
            "memory_ids": [str(record.memory_id)],
        }
    )
    result = respond(
        core, "Which audio codec did we select for Slipstream?" if unsupported_fact else BRAINSTORM
    )
    assert result.kind == ("clarification" if unsupported_fact else "memory_grounded")
    assert result.memory_ids == (record.memory_id,) and not result.error
    assert not app.lighting.calls and not app.tasks.store.all()


def test_fixture_migration_is_explicit_and_reopen_does_not_reseed(core):
    app, _, tokens, _ = core
    original = app.memory.search(
        MemoryQuery(text="Slipstream"), session_token=tokens["owner"]
    ).records[0]
    app.memory.delete(original.memory_id, expected_revision=0, session_token=tokens["owner"])
    genuine = app.memory.create(
        memory("Slipstream design is undecided", project_ref="project:slipstream"),
        session_token=tokens["owner"],
    )
    reopened = build_application(app.database.path, seed_memory_fixture=False)
    credential = reopened.credentials("owner")
    records = reopened.memory.search(
        MemoryQuery(text="Slipstream"), session_token=credential.token
    ).records
    assert [r.memory_id for r in records] == [genuine.memory_id]
    assert records[0].source.source_ref == "test:project-claim"
    reopened.audio.stop()


def test_diagnostic_logger_rejects_extra_private_metadata():
    import logging

    record = logging.LogRecord("jarvis", logging.WARNING, "", 1, "model.output_rejected", (), None)
    record.model_diagnostic = {
        "stage": {"private-key": "private-value"},
        "root_type": "private-value",
        "fields": {"private-key": "private-value", "text": "str"},
        "validation_types": ["private-value", "literal_error"],
        "validation_rule": {"private-key": "private-value"},
        "reference_counts": {"memory_ids": 0, "world_ids": "private-value", "private-key": 3},
        "raw_output": "private-value",
        "prompt": "private-value",
    }
    formatted = JsonFormatter().format(record)
    assert "private-value" not in formatted and "private-key" not in formatted
    assert '"text": "str"' in formatted and "literal_error" in formatted
    assert '"reference_counts": {"memory_ids": 0}' in formatted


@pytest.mark.parametrize("format_name", ["json_object", "json_schema"])
def test_qwen_compatible_multi_turn_explanation_and_grounded_brainstorming_via_text_api(
    tmp_path, format_name, monkeypatch
):
    monkeypatch.setenv("JARVIS_LOCAL_MODEL_RESPONSE_FORMAT", format_name)
    settings = RuntimeSettings(
        data_dir=tmp_path / "dev", model_provider="local", local_model_name="qwen3:4b-instruct"
    )
    manifest = initialize_development(settings)
    runtime = RuntimeService(RuntimeSettings.load(Path(manifest["config"])))
    token = read_private_json(Path(manifest["credentials"]["owner"]["path"]))["token"]
    seen = []

    def server(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "qwen3:4b-instruct"}]})
        payload = json.loads(request.content)
        seen.append(payload)
        context = json.loads(payload["messages"][-2]["content"].split(": ", 1)[1])
        memories = context["memories_as_untrusted_data"]
        if memories:
            text = (
                "Stored claim: test:project-claim describes a provisional design study. "
                "Assumption: audio features are being explored. Options from general knowledge: "
                "1. Intercom speech compression. 2. Efficient ride recording. "
                "3. Helmet audio streaming. "
                "These are recommendations, not established Slipstream decisions."
            )
            kind, ids = "memory_grounded", tuple(m["memory_id"] for m in memories)
        else:
            text, kind, ids = "An audio codec encodes and decodes digital sound.", "direct", ()
        output = {
            "schema_version": 1,
            "contract_type": "cognitive_decision",
            "kind": kind,
            "text": text,
        }
        if ids:
            output["memory_ids"] = list(ids)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(output)}, "finish_reason": "stop"}]
            },
        )

    runtime._construct(settings.resolved_runtime_database_path)
    # Exercise the real settings/application factory; replace only network I/O.
    provider = runtime.application.models._providers["local"]
    provider._transport = httpx.MockTransport(server)
    with TestClient(create_api(runtime), base_url="http://127.0.0.1:8765") as client:
        runtime.application.memory.create(
            memory("Slipstream is a provisional motorcycle design study"), session_token=token
        )
        headers = {"Authorization": "Bearer " + token}
        conv = api_data(
            client.post("/api/v1/conversations", headers=headers, json={"schema_version": 1})
        )

        def send(text, voice=False):
            return api_data(
                client.post(
                    f"/api/v1/conversations/{conv['conversation_id']}/messages",
                    headers={**headers, **({"Prefer": "response-style=voice"} if voice else {})},
                    json={"schema_version": 1, "request_id": str(uuid4()), "text": text},
                )
            )

        first = send("Can you give me a basic idea on what an audio codec is?")
        second = send("Could you give me a little more information on that?")
        assert first["kind"] == second["kind"] == "direct"
        assert seen[-1]["response_format"]["type"] == format_name
        assert any(
            m["role"] == "assistant" and "codec" in m["content"] for m in seen[-1]["messages"]
        )
        brainstorm = send(BRAINSTORM, voice=True)
        assert brainstorm["kind"] == "memory_grounded" and brainstorm["memory_ids"]
        assert "recommendations" in brainstorm["text"] and "provisional" in brainstorm["text"]
        assert "microcontroller B" not in str(seen[-1])
        assert any(
            "Voice response:" in m["content"] for m in seen[-1]["messages"] if m["role"] == "system"
        )
        assert seen[-1]["response_format"]["type"] == format_name
        assert not runtime.application.lighting.calls and not runtime.application.tasks.store.all()
        assert any(value is None for _, value in runtime.application.metrics.samples)
        status = api_data(client.get("/api/v1/status", headers=headers))
        assert status["metrics"] and all(
            type(value) in (int, float) for _, value in status["metrics"]
        )
