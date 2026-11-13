"""Real policy/task/persistence boundaries; deterministic model fixtures, no AI claims."""

import asyncio
import json
import subprocess
import sys
from uuid import uuid4

import httpx
import pytest
from jsonschema import Draft202012Validator

from jarvis.application import build_application
from jarvis.core.cognitive_records import IntentPlan, ModelIdentity, decode_cognitive
from jarvis.core.contracts import Provenance
from jarvis.core.memory_records import MemoryDraft
from jarvis.development import BRIGHTNESS_PERMISSION
from jarvis.egress import EgressPolicy
from jarvis.intents import split_intents
from jarvis.model_output import plan_schema, plan_wire_schema
from jarvis.models import FakeModel, ModelError, ModelRouter, OpenAILocalModel

MIXED = (
    "How could we use audio codecs in the Slipstream project? "
    "Also, could you dim the lights to around 45%?"
)


class Spy(FakeModel):
    def __init__(self):
        super().__init__(propose_unavailable=True)
        self.requests = []
        self.transform = None

    async def complete(self, request):
        self.requests.append(request)
        output = await super().complete(request)
        return self.transform(request, json.loads(output)) if self.transform else output


@pytest.fixture
def core(tmp_path):
    model = Spy()
    path = tmp_path / "multi.sqlite3"
    app = build_application(path, bootstrap=True, fake=model)
    tokens = {n: app.credentials(n).token for n in ("owner", "friend", "guest")}
    convs = {n: app.conversations.start(session_token=t).conversation_id for n, t in tokens.items()}
    yield app, model, tokens, convs, path
    app.audio.stop()


def send(core, text=MIXED, who="owner", **kwargs):
    app, _, tokens, convs, _ = core
    return asyncio.run(
        app.coordinator.respond(convs[who], text, session_token=tokens[who], **kwargs)
    )


def test_two_conversational_intents_no_task(core):
    app, model, _, _, _ = core
    result = send(core, "Explain Opus compression and describe audio codecs.")
    assert result.kind == "composed" and result.status == "completed"
    assert len(result.intents) == 2 and all(i.response.kind == "direct" for i in result.intents)
    assert "Opus" in result.text and not app.tasks.store.all() and len(model.requests) == 1


def test_multi_voice_context_has_one_consistent_concision_target(core):
    _, model, _, _, _ = core
    send(core, response_style="voice")
    guidance = " ".join(m.content for m in model.requests[0].messages if m.role == "developer")
    assert "25-40" in guidance and "60-90" not in guidance


def test_exact_mixed_request_recommends_and_executes_with_memory_provenance(core):
    app, model, tokens, convs, _ = core
    fact = app.memory.create(
        MemoryDraft(
            kind="semantic",
            content={"text": "Slipstream design is provisional."},
            source=Provenance(category="user_statement", source_ref="test:provisional-project"),
            consent_ref="consent:test",
            confidence=0.3,
        ),
        session_token=tokens["owner"],
    )
    request_id = uuid4()
    result = send(core, request_id=request_id)
    assert result.status == "completed" and fact.memory_id in result.memory_ids
    assert result.intents[0].response.kind == "memory_grounded"
    action = result.intents[1].response
    assert action.kind == "action" and action.task_state == "COMPLETED"
    assert action.execution.output == {"brightness": 45, "simulated": True}
    assert action.audit_ids and action.correlation_id == result.correlation_id
    assert "COMPLETED" in result.text and "simulated" in result.text
    assert all(m.source_ref != "development.cognition.fixture" for m in model.requests[0].memories)
    assert send(core, request_id=request_id) == result and app.lighting.calls == [45]
    assert len(model.requests) == 1 and len(app.tasks.store.all()) == 1
    history = app.coordinator.history(convs["owner"], session_token=tokens["owner"])
    assert len(history) == 1 and history[0].response == result
    assert decode_cognitive(history[0].model_dump_json()) == history[0]


def test_denied_action_preserves_valid_answer_and_never_leaks_owner_memory(core):
    app, model, _, _, _ = core
    result = send(core, who="guest")
    assert result.status == "partial" and result.intents[0].response.kind == "direct"
    assert result.intents[1].response.execution.status == "denied"
    assert result.intents[1].response.task_state == "FAILED"
    assert not model.requests[0].memories and not result.memory_ids and not app.lighting.calls
    assert "FAILED" in result.text and "denied" in result.text


@pytest.mark.parametrize("enabled,code", [(False, "cloud_disabled"), (True, "egress_denied")])
def test_multi_intent_cannot_send_context_to_unapproved_cloud(core, enabled, code):
    app, model, _, _, _ = core
    model.identity = ModelIdentity(
        provider_id="test.cloud",
        model_id="spy",
        location="cloud",
        destination="https://cloud.invalid/v1",
    )
    app.coordinator.models = ModelRouter(
        {"cloud": model},
        selected="cloud",
        cloud_enabled=enabled,
        egress=EgressPolicy(app.identities, app.permissions, app.audit, enabled=enabled),
    )
    result = send(core)
    assert result.error.code == code
    assert not model.requests and not app.tasks.store.all() and not app.lighting.calls


def test_multiple_actions_have_independent_tasks_and_one_time_effects(core):
    app, model, _, _, _ = core
    text = "Set lights to 25% and then dim lights to 45%."
    request_id = uuid4()
    result = send(core, text, request_id=request_id)
    tasks = [i.response.task_id for i in result.intents]
    assert result.status == "completed" and len(set(tasks)) == 2
    assert app.lighting.calls == [25, 45]
    assert send(core, text, request_id=request_id) == result
    assert app.lighting.calls == [25, 45] and len(model.requests) == 1


def test_followup_context_includes_current_multi_intent_task_states(core):
    _, model, _, _, _ = core
    result = send(core, "Set lights to 25% and then dim lights to 45%.")
    send(core, "What happened?")
    summaries = model.requests[-1].tasks
    assert {t.task_id for t in summaries} == {i.response.task_id for i in result.intents}
    assert all(t.state == "COMPLETED" for t in summaries)


def test_invalid_arguments_fail_individually_without_discarding_question(core):
    app, _, _, _, _ = core
    result = send(core, "Explain Opus and set lights to 999%.")
    assert result.status == "partial" and result.intents[0].response.kind == "direct"
    assert result.intents[1].response.kind == "failure"
    assert not app.lighting.calls


def test_unsupported_operation_plus_question(core):
    app, _, _, _, _ = core
    result = send(core, "Send a Slack message and explain Opus.")
    assert result.status == "partial" and result.intents[0].response.kind == "unsupported"
    assert result.intents[1].response.kind == "direct" and not app.tasks.store.all()


@pytest.mark.parametrize(
    "mutation",
    ["duplicate_index", "missing_intent", "extra_intent", "version", "identity", "approval"],
)
def test_invalid_decomposition_never_submits_any_action(core, mutation):
    app, model, _, _, _ = core

    def corrupt(request, plan):
        if mutation == "duplicate_index":
            plan["intents"][1]["intent_index"] = 0
        elif mutation == "missing_intent":
            plan["intents"].pop()
        elif mutation == "extra_intent":
            plan["intents"].append(plan["intents"][1])
        elif mutation == "version":
            del plan["intents"][1]["action"]["schema_version"]
        elif mutation == "identity":
            plan["intents"][1]["action"]["session_id"] = str(uuid4())
        else:
            plan["intents"][1]["action"]["approval"] = {"approved": True}
        return json.dumps(plan)

    model.transform = corrupt
    result = send(core)
    assert result.kind == "failure" and result.error
    assert not app.tasks.store.all() and not app.lighting.calls


def test_single_decision_cannot_stand_in_for_a_multi_intent_plan(core):
    app, model, _, _, _ = core
    model.transform = lambda *_: json.dumps(
        {
            "schema_version": 1,
            "contract_type": "cognitive_decision",
            "kind": "direct",
            "text": "The lights were set to 45%.",
        }
    )
    result = send(core)
    assert result.error.code == "intent_decomposition_invalid"
    assert not app.tasks.store.all() and not app.lighting.calls


@pytest.mark.parametrize(
    "injection", ["wrong_value", "action_for_question", "false_prose", "unknown_capability"]
)
def test_model_cannot_invent_effects_or_report_false_completion(core, injection):
    app, model, _, _, _ = core

    def corrupt(request, plan):
        action = plan["intents"][1]
        if injection == "wrong_value":
            action["action"]["arguments"]["brightness"] = 90
        elif injection == "unknown_capability":
            action["action"]["capability_id"] = "shell.execute"
        elif injection == "false_prose":
            plan["intents"][0]["text"] = "The lights were set to 90%."
        else:
            action["intent_index"] = 0
            plan["intents"][0] = action.copy()
            plan["intents"][1] = {
                "schema_version": 1,
                "contract_type": "planned_intent",
                "intent_index": 1,
                "purpose": "clarification",
                "kind": "unsupported",
                "text": "No proposal.",
            }
        return json.dumps(plan)

    model.transform = corrupt
    result = send(core)
    assert result.status == "partial" or result.status == "failed"
    if injection == "false_prose":
        assert "were set" not in result.text and app.lighting.calls == [45]
    else:
        assert not app.lighting.calls and not app.tasks.store.all()


@pytest.mark.parametrize("claim", ["Done: lights at 90%.", "Brightness is now 90%."])
def test_reasoning_cannot_include_terse_unverified_action_claims(core, claim):
    app, model, _, _, _ = core

    def corrupt(request, plan):
        plan["intents"][0]["text"] = claim
        return json.dumps(plan)

    model.transform = corrupt
    result = send(core)
    assert result.status == "partial" and claim not in result.text
    assert result.intents[0].response.error.code == "unverified_action_claim"
    assert result.intents[1].response.execution.output == {"brightness": 45, "simulated": True}
    assert app.lighting.calls == [45]


def test_revocation_between_intents_prevents_second_handler(core):
    app, _, tokens, _, _ = core
    provider = app.registry._resolve_for_executor(BRIGHTNESS_PERMISSION).provider
    original = provider.__class__.__call__

    # Trusted test handler hook revokes a real current grant after the first effect.
    def revoke(self, arguments):
        output = original(self, arguments)
        if arguments["brightness"] == 25:
            principal = app.identities.resolve_session(tokens["friend"]).principal_id
            app.permissions.revoke(
                tokens["owner"], principal, BRIGHTNESS_PERMISSION, correlation_id=uuid4()
            )
        return output

    from unittest.mock import patch

    with patch.object(provider.__class__, "__call__", revoke):
        result = send(core, "Set lights to 25% and then set lights to 45%.", who="friend")
    assert result.status == "partial" and app.lighting.calls == [25]
    assert result.intents[1].response.execution.status == "denied"


def test_pending_tasks_replay_without_inference_or_duplicate_submissions(core):
    app, model, tokens, _, _ = core
    app.coordinator.defer_actions = True
    request_id = uuid4()
    first = send(core, request_id=request_id)
    assert first.status == "pending" and first.intents[0].response.kind == "direct"
    task_id = first.intents[1].response.task_id
    app.tasks.run(task_id, session_token=tokens["owner"])
    final = send(core, request_id=request_id)
    assert final.status == "completed" and final.intents[1].response.task_id == task_id
    assert app.lighting.calls == [45] and len(model.requests) == 1


def test_partial_execution_survives_a_separate_process(core, monkeypatch):
    app, _, tokens, convs, path = core
    original = app.conversations._save

    class Crash(BaseException):
        pass

    def crash(turn, **changes):
        updated = original(turn, **changes)
        if updated.parent_request_id and updated.state == "COMPLETED":
            raise Crash()
        return updated

    monkeypatch.setattr(app.conversations, "_save", crash)
    request_id = uuid4()
    text = "Set lights to 25% and then set lights to 45%."
    with pytest.raises(Crash):
        send(core, text, request_id=request_id)
    assert app.lighting.calls == [25]
    script = """import asyncio,json,sys
from pathlib import Path
from uuid import UUID
from jarvis.application import build_application
d=json.load(sys.stdin);app=build_application(Path(d['path']))
r=asyncio.run(app.coordinator.respond(UUID(d['conversation']),d['text'],session_token=d['token'],request_id=UUID(d['request'])))
print(json.dumps({'response':r.model_dump(mode='json'),'calls':app.lighting.calls}));app.audio.stop()
"""
    process = subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps(
            {
                "path": str(path),
                "conversation": str(convs["owner"]),
                "token": tokens["owner"],
                "request": str(request_id),
                "text": text,
            }
        ),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    data = json.loads(process.stdout)
    assert data["response"]["status"] == "completed" and data["calls"] == [45]
    assert len(app.tasks.store.all()) == 2


@pytest.mark.parametrize("mode", ["json_object", "json_schema"])
def test_plan_wire_schema_and_local_provider_contract(core, mode):
    app, model, _, _, _ = core
    send(core)
    request = model.requests[0]
    schema = plan_schema(request, "local.openai", structured=mode == "json_schema")
    Draft202012Validator.check_schema(schema)
    plan = json.loads(asyncio.run(FakeModel(propose_unavailable=True).complete(request)))
    plan["intents"][1]["action"]["source_ref"] = "local.openai"
    for name in ("task_id", "proposal_id", "created_at"):
        plan["intents"][1]["action"].pop(name, None)
    Draft202012Validator(schema).validate(plan)
    wire = (
        {
            "schema_version": 1,
            "contract_type": "local_intent_slots",
            "intents": {str(i): item for i, item in enumerate(plan["intents"])},
        }
        if mode == "json_schema"
        else plan
    )
    if mode == "json_schema":
        Draft202012Validator(plan_wire_schema(request, "local.openai")).validate(wire)
    seen = []

    def endpoint(req):
        seen.append(json.loads(req.content))
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(wire)}}]},
        )

    provider = OpenAILocalModel(
        model="qwen-protocol-fixture", response_format=mode, transport=httpx.MockTransport(endpoint)
    )
    assert isinstance(decode_cognitive(asyncio.run(provider.complete(request))), IntentPlan)
    payload = seen[0]
    assert payload["response_format"]["type"] == mode
    context = json.loads(payload["messages"][-2]["content"].split(": ", 1)[1])
    assert len(context["intent_inputs"]) == 2
    assert any(c["simulated"] for c in context["interaction_inventory"]["chat_capabilities"])
    inventory = context["interaction_inventory"]
    assert "external messaging" in inventory["unavailable_external_integrations"]


def test_limits_and_ordinary_explanations(core):
    app, model, _, _, _ = core
    assert not split_intents("Explain Opus and AAC.")
    result = send(core, "Explain Opus; explain AAC; explain MP3; explain FLAC; explain PCM.")
    assert (
        result.error.code == "intent_limit_exceeded"
        and not model.requests
        and not app.tasks.store.all()
    )


@pytest.mark.parametrize("mutation", ["missing", "extra", "index", "version", "approval"])
def test_local_slot_normalization_never_repairs_invalid_output(core, mutation):
    app, model, _, _, _ = core
    send(core)
    request = model.requests[0]
    plan = json.loads(asyncio.run(model.complete(request)))
    action = plan["intents"][1]["action"]
    action["source_ref"] = "local.openai"
    for name in ("task_id", "proposal_id", "created_at"):
        action.pop(name, None)
    wire = {
        "schema_version": 1,
        "contract_type": "local_intent_slots",
        "intents": {str(i): item for i, item in enumerate(plan["intents"])},
    }
    if mutation == "missing":
        del wire["intents"]["0"]
    elif mutation == "extra":
        wire["intents"]["2"] = plan["intents"][1]
    elif mutation == "index":
        wire["intents"]["1"]["intent_index"] = 0
    elif mutation == "version":
        del action["schema_version"]
    else:
        action["approval"] = True
    provider = OpenAILocalModel(
        response_format="json_schema",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(wire)}}]},
            )
        ),
    )
    with pytest.raises(ModelError, match="model_output_invalid"):
        asyncio.run(provider.complete(request))
    assert app.lighting.calls == [45] and len(app.tasks.store.all()) == 1


def test_planned_intent_does_not_allow_a_new_contract_version():
    with pytest.raises(ValueError):
        decode_cognitive(
            json.dumps({"schema_version": 2, "contract_type": "intent_plan", "intents": []})
        )


def test_echo_and_lighting_are_distinct_supported_actions(core):
    app, _, _, _, _ = core
    result = send(core, 'Echo "hello" and set lights to 45%.')
    assert result.status == "completed"
    assert result.intents[0].response.execution.output == {"text": "hello"}
    assert result.intents[1].response.execution.output == {"brightness": 45, "simulated": True}
    assert len({i.response.task_id for i in result.intents}) == 2 and app.lighting.calls == [45]


def test_pending_first_action_delays_later_submission_and_preserves_order(core):
    app, model, tokens, _, _ = core
    app.coordinator.defer_actions = True
    request_id = uuid4()
    text = "Set lights to 25% and then set lights to 45%."
    first = send(core, text, request_id=request_id)
    assert first.status == "pending" and first.intents[0].response.task_id
    assert first.intents[1].response.task_id is None and len(app.tasks.store.all()) == 1
    app.tasks.run(first.intents[0].response.task_id, session_token=tokens["owner"])
    second = send(core, text, request_id=request_id)
    assert second.intents[1].response.task_id and app.lighting.calls == [25]
    app.tasks.run(second.intents[1].response.task_id, session_token=tokens["owner"])
    final = send(core, text, request_id=request_id)
    assert final.status == "completed" and app.lighting.calls == [25, 45]
    assert len(model.requests) == 1 and len(app.tasks.store.all()) == 2


def test_private_context_revocation_blocks_plan_replay_and_preserves_tasks(core):
    app, model, tokens, _, _ = core
    fact = app.memory.create(
        MemoryDraft(
            kind="semantic",
            content={"text": "Slipstream provisional design"},
            source=Provenance(category="user_statement", source_ref="test:revocable"),
            consent_ref="consent:test",
        ),
        session_token=tokens["owner"],
    )
    request_id = uuid4()
    send(core, request_id=request_id)
    app.memory.delete(fact.memory_id, expected_revision=0, session_token=tokens["owner"])
    replay = send(core, request_id=request_id)
    assert replay.kind == "failure" and replay.error.code == "context_authorization_changed"
    assert "provisional design" not in replay.text and app.lighting.calls == [45]
    assert len(model.requests) == 1 and len(app.tasks.store.all()) == 1


def test_internal_child_request_is_not_a_public_replay_endpoint(core):
    app, _, _, _, _ = core
    result = send(core)
    child = result.intents[1].response
    replay = send(core, "could you dim the lights to around 45%", request_id=child.request_id)
    assert replay.kind == "failure" and replay.error.code == "request_conflict"
    assert app.lighting.calls == [45]


def test_composed_nested_versions_remain_explicit(core):
    app, _, tokens, convs, _ = core
    send(core)
    turn = app.coordinator.history(convs["owner"], session_token=tokens["owner"])[0]
    raw = turn.model_dump(mode="json")
    del raw["response"]["intents"][1]["response"]["execution"]["schema_version"]
    with pytest.raises(ValueError, match="explicit cognitive version"):
        decode_cognitive(json.dumps(raw))


def test_metadata_only_storage_migration_preserves_existing_authority(core, monkeypatch):
    import jarvis.storage.sqlite as storage

    app, _, tokens, _, _ = core
    before = app.identities.resolve_session(tokens["owner"])
    with app.database.transaction(write=True) as connection:
        connection.execute("PRAGMA user_version = 9")
    app.database.initialize()
    with app.database.transaction() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10
    assert app.identities.resolve_session(tokens["owner"]) == before
    monkeypatch.setattr(storage, "SCHEMA_VERSION", 9)
    with pytest.raises(Exception, match="database_version_unsupported"):
        app.database.initialize()
