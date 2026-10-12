"""Actual coordinator enforcement; model prose never establishes lighting outcomes."""

import asyncio
from threading import Event, Thread
from uuid import uuid4

import pytest

from jarvis.action_reporting import NO_ACTION, SEPARATE_REQUESTS, reported_turn, request_shape
from jarvis.application import build_application
from jarvis.core.cognitive_records import CognitiveDecision, CognitiveResponse
from jarvis.core.contracts import ErrorDetail
from jarvis.models import FakeModel

MIXED = "How could audio codecs help Slipstream? Also, dim the lights to 45%."
FALSE_SUCCESS = "Audio codecs support streams. The simulated lights were set to 45%."


class Spy(FakeModel):
    def __init__(self):
        super().__init__(propose_unavailable=True)
        self.requests = []
        self.output = None

    async def complete(self, request):
        self.requests.append(request)
        return self.output if self.output is not None else await super().complete(request)


@pytest.fixture
def core(tmp_path):
    model = Spy()
    app = build_application(tmp_path / "mixed.sqlite3", bootstrap=True, fake=model)
    tokens = {who: app.credentials(who).token for who in ("owner", "guest")}
    convs = {
        who: app.conversations.start(session_token=t).conversation_id for who, t in tokens.items()
    }
    yield app, model, tokens, convs
    app.audio.stop()


def send(core, text, who="owner", **kwargs):
    app, _, tokens, convs = core
    return asyncio.run(
        app.coordinator.respond(convs[who], text, session_token=tokens[who], **kwargs)
    )


@pytest.mark.parametrize("who", ["owner", "guest"])
@pytest.mark.parametrize(
    "text",
    [
        MIXED,
        "How could audio codecs help Slipstream and dim the lights to 45%",
        "Dim the lights to 45%; explain audio codecs",
        "Please explain codecs. Could you also dim the lights to 45%?",
        "Dim the lights to 45% and then set the lights to 50%",
    ],
)
def test_mixed_requests_defer_without_model_memory_egress_or_task(core, who, text):
    app, model, tokens, convs = core
    request_id = uuid4()
    result = send(core, text, who, request_id=request_id)
    assert result.kind == "clarification" and result.text == SEPARATE_REQUESTS
    assert result.task_id is result.execution is result.provenance is None
    assert result.audit_ids and not result.memory_ids
    assert not model.requests and not app.tasks.store.all() and not app.lighting.calls
    assert send(core, text, who, request_id=request_id) == result
    turn = app.conversations.recent(convs[who], session_token=tokens[who])[-1]
    assert turn.state == "COMPLETED" and turn.response == result
    assert turn.correlation_id == result.correlation_id and not turn.memory_ids


@pytest.mark.parametrize(
    "text",
    [
        "The lights were set to 45%.",
        "I dimmed the lights to 45%.",
        "Done!",
        "Audio codecs are useful; the simulated lights are now set to 45%.",
        "I cannot propose an action.",
    ],
)
def test_single_request_without_proposal_suppresses_all_generated_prose(core, text):
    app, model, _, _ = core
    model.output = CognitiveDecision(kind="direct", text=text).model_dump_json()
    result = send(core, "Dim the lights to 45%.")
    assert result.kind in {"clarification", "failure"}
    assert result.text == NO_ACTION
    assert result.task_id is result.execution is None
    assert len(model.requests) == 1 and not app.lighting.calls and not app.tasks.store.all()


@pytest.mark.parametrize("text", ["The simulated lights were set to 45%.", "I dimmed the lights."])
def test_spontaneous_lighting_claim_in_explanation_is_not_authoritative(core, text):
    app, model, _, _ = core
    model.output = CognitiveDecision(kind="direct", text=text).model_dump_json()
    result = send(core, "What are audio codecs?")
    assert result.text == NO_ACTION and result.task_id is None
    assert not app.lighting.calls and not app.tasks.store.all()


def test_separate_requests_execute_only_validated_action_and_replay_once(core):
    app, model, _, _ = core
    assert send(core, MIXED).text == SEPARATE_REQUESTS
    model.output = CognitiveDecision(
        kind="direct", text="A codec encodes and decodes audio."
    ).model_dump_json()
    assert send(core, "What are audio codecs?").kind == "direct"
    model.output = None
    request_id = uuid4()
    action = send(core, "Dim the lights to 45%.", request_id=request_id)
    assert action.kind == "action" and action.task_state == "COMPLETED"
    assert action.execution.output == {"brightness": 45, "simulated": True}
    assert action.execution.task_id == action.task_id
    assert send(core, "Dim the lights to 45%.", request_id=request_id) == action
    assert len(model.requests) == 2 and app.lighting.calls == [45]
    denied = send(core, "Dim the lights to 45%.", "guest")
    assert denied.execution.status == "denied" and app.lighting.calls == [45]


def seed_old_unsubmitted_turn(core):
    app, _, tokens, convs = core
    turn, _ = app.conversations.begin(convs["owner"], uuid4(), MIXED, session_token=tokens["owner"])
    decision = CognitiveDecision(kind="direct", text=FALSE_SUCCESS)
    turn = app.conversations._save(
        turn, session_token=tokens["owner"], state="DECIDED", decision=decision
    )
    response = CognitiveResponse(
        conversation_id=turn.conversation_id,
        request_id=turn.request_id,
        correlation_id=turn.correlation_id,
        kind="direct",
        text=FALSE_SUCCESS,
    )
    return app.conversations._save(
        turn, session_token=tokens["owner"], state="COMPLETED", response=response
    )


def test_old_prose_is_projected_on_history_replay_and_context_without_rewriting_evidence(core):
    app, model, tokens, convs = core
    original = seed_old_unsubmitted_turn(core)
    history = app.coordinator.history(convs["owner"], session_token=tokens["owner"])
    assert history[0].response.text == SEPARATE_REQUESTS
    replay = send(core, MIXED, request_id=original.request_id)
    assert replay.text == SEPARATE_REQUESTS and replay.correlation_id == original.correlation_id
    assert not model.requests
    send(core, "What are audio codecs?")
    assert FALSE_SUCCESS not in model.requests[-1].model_dump_json()
    stored, fresh = app.conversations.begin(
        convs["owner"], original.request_id, MIXED, session_token=tokens["owner"]
    )
    assert (
        not fresh and stored.response.text == FALSE_SUCCESS and stored.revision == original.revision
    )
    with pytest.raises(Exception, match="conversation_unavailable"):
        app.coordinator.history(convs["owner"], session_token=tokens["guest"])


def test_deferral_and_old_reporting_survive_reopening_store(core, tmp_path):
    app, _, tokens, convs = core
    original = seed_old_unsubmitted_turn(core)
    result = send(core, MIXED)
    reopened = build_application(tmp_path / "mixed.sqlite3")
    try:
        history = reopened.coordinator.history(convs["owner"], session_token=tokens["owner"])
        assert history[-1].response == result
        replay = asyncio.run(
            reopened.coordinator.respond(
                convs["owner"], MIXED, session_token=tokens["owner"], request_id=original.request_id
            )
        )
        assert replay.text == SEPARATE_REQUESTS and not reopened.lighting.calls
    finally:
        reopened.audio.stop()


def test_pre_upgrade_decided_turn_resumes_with_safe_response(core):
    app, _, tokens, convs = core
    turn, _ = app.conversations.begin(convs["owner"], uuid4(), MIXED, session_token=tokens["owner"])
    app.conversations._save(
        turn,
        session_token=tokens["owner"],
        state="DECIDED",
        decision=CognitiveDecision(kind="direct", text=FALSE_SUCCESS),
    )
    response = send(core, MIXED, request_id=turn.request_id)
    assert response.text == SEPARATE_REQUESTS and response.task_id is None
    assert not app.lighting.calls and not app.tasks.store.all()


def test_reporting_preserves_structured_failure_after_a_decision(core):
    original = seed_old_unsubmitted_turn(core)
    failed_response = original.response.model_copy(
        update={
            "kind": "failure",
            "text": "Request failed: context_authorization_changed.",
            "error": ErrorDetail(
                code="context_authorization_changed", message="context_authorization_changed"
            ),
        }
    )
    projected = reported_turn(original.model_copy(update={"response": failed_response}))
    assert (
        projected.response.kind == "failure" and projected.response.error == failed_response.error
    )
    assert projected.response.text == SEPARATE_REQUESTS


def test_accepted_action_with_missing_task_link_is_not_reported_as_unsubmitted(core):
    app, _, tokens, convs = core
    action = send(core, "Dim the lights to 45%.")
    turn = app.conversations.recent(convs["owner"], session_token=tokens["owner"])[-1]
    assert reported_turn(turn) == turn and action.execution.status == "success"
    # Trusted recovery may encounter an accepted proposal before its task link is saved.
    unlinked = turn.model_copy(update={"task_id": None})
    assert unlinked.decision.action is not None and reported_turn(unlinked) == unlinked


def test_claimed_task_reports_pending_then_replays_verified_result_once(core, monkeypatch):
    app, model, _, _ = core
    submit = app.tasks.submit
    entered, release = Event(), Event()
    workers = []

    def claimed_submit(*args, **kwargs):
        task = submit(*args, **kwargs)

        def hold():
            with app.tasks.store.claim(task.task_id):
                entered.set()
                assert release.wait(5)

        worker = Thread(target=hold)
        workers.append(worker)
        worker.start()
        assert entered.wait(3)
        return task

    monkeypatch.setattr(app.tasks, "submit", claimed_submit)
    request_id = uuid4()
    try:
        pending = send(core, "Dim the lights to 45%.", request_id=request_id)
        assert pending.kind == "pending" and pending.error.code == "task_busy"
        assert pending.task_id and pending.execution is None and not app.lighting.calls
        assert "busy" in pending.text and "No action" not in pending.text
    finally:
        release.set()
        for worker in workers:
            worker.join(3)
            assert not worker.is_alive()
    monkeypatch.setattr(app.tasks, "submit", submit)
    completed = send(core, "Dim the lights to 45%.", request_id=request_id)
    assert completed.kind == "action" and completed.task_state == "COMPLETED"
    assert (
        completed.task_id == pending.task_id and completed.correlation_id == pending.correlation_id
    )
    assert completed.execution.output == {"brightness": 45, "simulated": True}
    assert len(model.requests) == 1 and app.lighting.calls == [45]


@pytest.mark.parametrize(
    "text",
    ["What are audio codecs?", "How does dimming lights work?", "What if we dim the lights?"],
)
def test_explanatory_questions_are_not_misclassified_as_commands(core, text):
    _, model, _, _ = core
    model.output = CognitiveDecision(
        kind="direct", text="An audio codec encodes and decodes audio."
    ).model_dump_json()
    assert request_shape(text) == (False, False)
    assert send(core, text).kind == "direct"
