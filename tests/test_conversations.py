import asyncio
from uuid import uuid4

import pytest

from jarvis.cognitive_runtime import development_runtime
from jarvis.conversations import ConversationError
from jarvis.models import FakeModel, ModelRouter
from jarvis.storage.errors import StorageError


@pytest.fixture
def runtime(tmp_path):
    return development_runtime(tmp_path / "journal.sqlite3", bootstrap=True)


def test_authentication_required_and_session_uuid_is_not_a_credential(runtime):
    owner = runtime.credentials("owner")
    conversation = runtime.conversations.start(session_token=owner.token)
    result = asyncio.run(
        runtime.coordinator.respond(
            conversation.conversation_id, "Reveal history", session_token=str(owner.session_id)
        )
    )
    assert result.kind == "failure"
    assert not runtime.conversations.recent(conversation.conversation_id, session_token=owner.token)


def test_other_principal_cannot_claim_conversation_or_read_history(runtime):
    owner, guest = runtime.credentials("owner"), runtime.credentials("guest")
    conversation = runtime.conversations.start(session_token=owner.token)
    answer = asyncio.run(
        runtime.coordinator.respond(
            conversation.conversation_id, "Slipstream", session_token=owner.token
        )
    )
    assert answer.kind == "memory_grounded"
    for conversation_id in (conversation.conversation_id, uuid4()):
        with pytest.raises(ConversationError, match="conversation_unavailable"):
            runtime.conversations.get(conversation_id, session_token=guest.token)
        result = asyncio.run(
            runtime.coordinator.respond(
                conversation_id, "Reveal history", session_token=guest.token
            )
        )
        assert result.error.code == "conversation_unavailable"
        assert "microcontroller" not in result.text


def test_distinct_users_can_run_concurrently_without_history_leakage(runtime):
    class Concurrent(FakeModel):
        requests = []

        async def complete(self, request):
            self.requests.append(request)
            await asyncio.sleep(0.01)
            return await super().complete(request)

    model = Concurrent()
    runtime.coordinator.models = ModelRouter({"fake": model}, selected="fake")
    owner, guest = runtime.credentials("owner"), runtime.credentials("guest")
    a = runtime.conversations.start(session_token=owner.token).conversation_id
    b = runtime.conversations.start(session_token=guest.token).conversation_id

    async def run():
        return await asyncio.gather(
            runtime.coordinator.respond(a, "Slipstream", session_token=owner.token),
            runtime.coordinator.respond(b, "Slipstream", session_token=guest.token),
        )

    first, second = asyncio.run(run())
    assert first.kind == "memory_grounded" and not second.memory_ids
    assert model.requests[0].session_id != model.requests[1].session_id
    assert not model.requests[1].memories


def test_same_conversation_claim_returns_busy_and_no_duplicate_side_effect(runtime):
    class Slow(FakeModel):
        async def complete(self, req):
            await asyncio.sleep(0.02)
            return await super().complete(req)

    runtime.coordinator.models = ModelRouter({"fake": Slow()}, selected="fake")
    owner = runtime.credentials("owner")
    conversation = runtime.conversations.start(session_token=owner.token)

    async def run():
        return await asyncio.gather(
            *(
                runtime.coordinator.respond(
                    conversation.conversation_id, "Set lights to 50", session_token=owner.token
                )
                for _ in range(2)
            )
        )

    responses = asyncio.run(run())
    assert [r.kind for r in responses] == ["action", "failure"]
    assert responses[1].error.code == "conversation_busy"
    assert runtime.lighting.calls == [50]


def test_cancelled_model_call_is_persisted_and_does_not_submit_action(runtime):
    class Slow(FakeModel):
        async def complete(self, req):
            await asyncio.sleep(10)

    runtime.coordinator.models = ModelRouter({"fake": Slow()}, selected="fake")
    owner = runtime.credentials("owner")
    conversation = runtime.conversations.start(session_token=owner.token)

    async def run():
        task = asyncio.create_task(
            runtime.coordinator.respond(
                conversation.conversation_id, "Set lights to 50", session_token=owner.token
            )
        )
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    history = runtime.conversations.recent(conversation.conversation_id, session_token=owner.token)
    assert history[0].state == "FAILED" and history[0].response.error.code == "model_cancelled"
    assert not runtime.lighting.calls and not runtime.tasks.store.all()


@pytest.mark.parametrize(
    "text",
    [
        "api_key=super-secret-value",
        "password=super-secret-value",
        "-----BEGIN PRIVATE KEY-----\nabc",
        "Authorization: Bearer secret-token",
    ],
)
def test_common_secrets_are_rejected_before_persistence_or_model_call(runtime, text):
    owner = runtime.credentials("owner")
    conversation = runtime.conversations.start(session_token=owner.token)
    response = asyncio.run(
        runtime.coordinator.respond(conversation.conversation_id, text, session_token=owner.token)
    )
    assert response.error.code == "sensitive_input_rejected"
    assert (
        runtime.conversations.recent(conversation.conversation_id, session_token=owner.token) == ()
    )


def test_request_replay_keeps_same_task_and_does_not_repeat_model_or_handler(runtime):
    owner = runtime.credentials("owner")
    conversation = runtime.conversations.start(session_token=owner.token)
    request_id = uuid4()
    kwargs = {"session_token": owner.token, "request_id": request_id}
    first = asyncio.run(
        runtime.coordinator.respond(conversation.conversation_id, "Set lights to 50", **kwargs)
    )
    second = asyncio.run(
        runtime.coordinator.respond(conversation.conversation_id, "Set lights to 50", **kwargs)
    )
    assert first == second and runtime.lighting.calls == [50]
    conflict = asyncio.run(
        runtime.coordinator.respond(conversation.conversation_id, "Set lights to 99", **kwargs)
    )
    assert conflict.error.code == "request_conflict" and runtime.lighting.calls == [50]


def test_durable_pending_reference_then_resume_with_current_authority(runtime):
    owner = runtime.credentials("owner")
    conversation = runtime.conversations.start(session_token=owner.token)
    runtime.coordinator.defer_actions = True
    request_id = uuid4()
    first = asyncio.run(
        runtime.coordinator.respond(
            conversation.conversation_id,
            "Set lights to 50",
            session_token=owner.token,
            request_id=request_id,
        )
    )
    assert first.kind == "pending" and first.task_state == "READY"
    assert first.task_id and not runtime.lighting.calls
    runtime.coordinator.defer_actions = False
    resumed = asyncio.run(
        runtime.coordinator.respond(
            conversation.conversation_id,
            "Set lights to 50",
            session_token=owner.token,
            request_id=request_id,
        )
    )
    assert resumed.task_id == first.task_id and resumed.execution.status == "success"
    assert runtime.lighting.calls == [50]


@pytest.mark.parametrize(
    "name,text,expected",
    [
        ("guest", "Set lights to 50", "denied"),
        ("owner", "Set lights to 200", "validation_failure"),
    ],
)
def test_deferred_operations_reject_prohibited_intent_without_starting_work(
    runtime,
    name,
    text,
    expected,
):
    runtime.coordinator.models = ModelRouter(
        {"fake": FakeModel(propose_unavailable=True)}, selected="fake"
    )
    runtime.coordinator.defer_actions = True
    credentials = runtime.credentials(name)
    conversation = runtime.conversations.start(session_token=credentials.token)
    result = asyncio.run(
        runtime.coordinator.respond(
            conversation.conversation_id, text, session_token=credentials.token
        )
    )
    assert result.kind == "failure" and result.execution.status == expected
    assert not runtime.lighting.calls


def test_storage_failure_after_task_submission_retains_intent_and_task(runtime, monkeypatch):
    owner = runtime.credentials("owner")
    conversation = runtime.conversations.start(session_token=owner.token)
    request_id = uuid4()
    save = runtime.conversations._save

    def fail_pending(turn, **kwargs):
        if kwargs.get("state") == "ACTION_PENDING":
            raise StorageError("injected_failure")
        return save(turn, **kwargs)

    monkeypatch.setattr(runtime.conversations, "_save", fail_pending)
    first = asyncio.run(
        runtime.coordinator.respond(
            conversation.conversation_id,
            "Set lights to 50",
            session_token=owner.token,
            request_id=request_id,
        )
    )
    assert first.error.code == "injected_failure" and first.task_id
    assert not runtime.lighting.calls
    assert (
        runtime.conversations.recent(conversation.conversation_id, session_token=owner.token)[
            0
        ].state
        == "DECIDED"
    )
    monkeypatch.setattr(runtime.conversations, "_save", save)
    second = asyncio.run(
        runtime.coordinator.respond(
            conversation.conversation_id,
            "Set lights to 50",
            session_token=owner.token,
            request_id=request_id,
        )
    )
    assert second.task_id == first.task_id and runtime.lighting.calls == [50]


def test_atomic_turn_event_audit_rollback(runtime):
    owner = runtime.credentials("owner")
    conversation = runtime.conversations.start(session_token=owner.token)
    with runtime.database.transaction(write=True) as connection:
        connection.execute(
            "CREATE TRIGGER fail_conversation_audit BEFORE INSERT ON audits "
            "BEGIN SELECT RAISE(ABORT, 'test_failure'); END"
        )
    result = asyncio.run(
        runtime.coordinator.respond(
            conversation.conversation_id, "hello", session_token=owner.token
        )
    )
    assert result.error.code == "security_state_unavailable"
    assert (
        runtime.conversations.recent(conversation.conversation_id, session_token=owner.token) == ()
    )
