import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from jarvis.core.cognitive_records import (
    CognitiveDecision,
    CognitiveResponse,
    Conversation,
    ConversationTurn,
    ModelMessage,
    ModelRequest,
)
from jarvis.core.contracts import ActionProposal, decode_contract


def examples():
    conversation = Conversation(owner_id=uuid4())
    turn = ConversationTurn(
        request_id=uuid4(),
        conversation_id=conversation.conversation_id,
        owner_id=conversation.owner_id,
        session_id=uuid4(),
        user_text="hello",
    )
    action = ActionProposal(
        session_id=turn.session_id,
        correlation_id=turn.correlation_id,
        capability_id="development.echo",
        origin="model",
        source_ref="test:model",
    )
    return (
        conversation,
        turn,
        CognitiveDecision(kind="action", text="Proposal", action=action),
        CognitiveResponse(
            conversation_id=conversation.conversation_id,
            request_id=turn.request_id,
            correlation_id=turn.correlation_id,
            kind="direct",
            text="Hello",
        ),
        ModelRequest(
            request_id=turn.request_id,
            session_id=turn.session_id,
            correlation_id=turn.correlation_id,
            messages=(ModelMessage(role="user", content="hello"),),
        ),
    )


@pytest.mark.parametrize("example", examples())
def test_cognitive_contract_round_trip(example):
    assert decode_contract(example.model_dump_json()) == example
    assert type(example).model_json_schema()["additionalProperties"] is False


@pytest.mark.parametrize("example", examples())
@pytest.mark.parametrize("version", [None, True, "1", 2])
def test_cognitive_wire_versions_are_explicit(example, version):
    data = example.model_dump(mode="json")
    if version is None:
        del data["schema_version"]
    else:
        data["schema_version"] = version
    with pytest.raises(ValueError):
        decode_contract(json.dumps(data))


@pytest.mark.parametrize("key", ["action", "messages"])
@pytest.mark.parametrize("version", [None, 2])
def test_nested_contract_versions_are_not_defaulted(key, version):
    example = examples()[2 if key == "action" else 4]
    data = example.model_dump(mode="json")
    nested = data[key] if key == "action" else data[key][0]
    if version is None:
        del nested["schema_version"]
    else:
        nested["schema_version"] = version
    with pytest.raises(ValueError):
        decode_contract(json.dumps(data))


def test_argument_data_with_schema_version_is_not_an_envelope():
    decision = examples()[2]
    action = decision.action.model_copy(update={"arguments": {"schema_version": 27}})
    decision = decision.model_copy(update={"action": action})
    assert decode_contract(decision.model_dump_json()) == decision


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "action", "text": "No proposal"},
        {"kind": "memory_grounded", "text": "No references"},
        {"kind": "failure", "text": "No error"},
    ],
)
def test_decision_variant_consistency(kwargs):
    with pytest.raises(ValidationError):
        CognitiveDecision(**kwargs)
