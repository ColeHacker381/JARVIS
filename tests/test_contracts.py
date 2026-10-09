import json
from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from jarvis.core.contracts import (
    CONTRACT_TYPES,
    AccessScope,
    ActionProposal,
    ErrorDetail,
    ExecutionResult,
    Principal,
    Provenance,
    Session,
    Task,
    decode_contract,
)
from jarvis.core.time import utc_now

CONTRACT_NAMES = tuple(CONTRACT_TYPES)


def example(contracts, name):
    return next(item for item in contracts if item.contract_type == name)


@pytest.mark.parametrize("name", CONTRACT_NAMES)
def test_contract_round_trip_and_schema(contracts, name):
    contract = example(contracts, name)
    payload = contract.model_dump_json()
    assert json.loads(payload)["schema_version"] == 1
    restored = decode_contract(payload)
    assert type(restored) is type(contract)
    assert restored == contract
    schema = type(contract).model_json_schema()
    assert schema["additionalProperties"] is False
    assert "schema_version" in schema["properties"]


@pytest.mark.parametrize("name", CONTRACT_NAMES)
@pytest.mark.parametrize("version", [2, 0, "1", True, None])
def test_unsupported_or_malformed_version_rejected(contracts, name, version):
    data = example(contracts, name).model_dump(mode="json")
    data["schema_version"] = version
    with pytest.raises(ValueError):
        decode_contract(json.dumps(data))
    with pytest.raises(ValidationError):
        type(example(contracts, name)).model_validate_json(json.dumps(data))


@pytest.mark.parametrize("name", CONTRACT_NAMES)
def test_missing_wire_version_and_unknown_fields_rejected(contracts, name):
    data = example(contracts, name).model_dump(mode="json")
    del data["schema_version"]
    with pytest.raises(ValueError):
        decode_contract(json.dumps(data))
    data["schema_version"] = 1
    data["unexpected"] = "untrusted"
    with pytest.raises(ValidationError):
        decode_contract(json.dumps(data))


@pytest.mark.parametrize(
    "payload",
    ["[]", "null", "{", '{"schema_version":1}', '{"schema_version":1,"contract_type":[]}'],
)
def test_invalid_envelope_rejected(payload):
    with pytest.raises(ValueError):
        decode_contract(payload)


def test_required_fields_and_strict_types(contracts):
    for contract in contracts:
        required = [
            name for name, field in type(contract).model_fields.items() if field.is_required()
        ]
        assert required
        data = contract.model_dump(mode="json")
        del data[required[0]]
        with pytest.raises(ValidationError):
            type(contract).model_validate_json(json.dumps(data))
    with pytest.raises(ValidationError):
        Principal(display_name=123)
    with pytest.raises(ValidationError):
        Principal(display_name="   ")
    with pytest.raises(ValidationError):
        Session(interface="local", principal_id="invalid-uuid")


def test_principals_sessions_and_relationships_are_only_data():
    owner = Principal(display_name="Owner", kind="owner")
    guest = Principal(display_name="Guest", kind="guest", relationship_ref="best_friend")
    assert owner.principal_id != guest.principal_id
    assert guest.policy_refs == ()
    assert Session(interface="local").principal_id is None
    assert Session(interface="local", principal_id=guest.principal_id).authentication_ref is None
    with pytest.raises(ValidationError):
        Session(interface="local", authentication_ref="forged")
    with pytest.raises(ValidationError):
        owner.kind = "guest"


def test_session_lifetime_and_timezone_validation():
    now = utc_now()
    with pytest.raises(ValidationError):
        Session(interface="local", started_at=now.replace(tzinfo=None))
    for field in ("ended_at", "expires_at"):
        with pytest.raises(ValidationError):
            Session(interface="local", started_at=now, **{field: now - timedelta(seconds=1)})


def test_proposal_cannot_embed_authorization(contracts):
    proposal = example(contracts, "action_proposal")
    data = proposal.model_dump(mode="json")
    for key in ("authorized", "policy_decision", "principal_id", "required_permissions"):
        with pytest.raises(ValidationError):
            ActionProposal.model_validate_json(json.dumps({**data, key: True}))
    assert "decision" not in data
    assert "execute" not in dir(proposal)


def test_policy_data_is_distinct_and_requires_explicit_allow_basis(contracts):
    decision = example(contracts, "policy_decision")
    data = decision.model_dump(mode="json")
    data["decision"] = "allow"
    with pytest.raises(ValidationError):
        type(decision).model_validate_json(json.dumps(data))
    data["policy_refs"] = ["test.explicit_grant"]
    allowed = type(decision).model_validate_json(json.dumps(data))
    assert allowed.decision == "allow"
    data["principal_id"] = None
    with pytest.raises(ValidationError):
        type(decision).model_validate_json(json.dumps(data))


def test_memory_requires_scope_and_provenance(contracts):
    memory = example(contracts, "memory")
    assert memory.access_scope.kind == "private"
    assert memory.access_scope.shared_with == ()
    with pytest.raises(ValidationError):
        AccessScope(owner_id=uuid4(), kind="shared")
    with pytest.raises(ValidationError):
        AccessScope(owner_id=uuid4(), shared_with=(uuid4(),))
    shared = AccessScope(owner_id=uuid4(), kind="shared", shared_with=(uuid4(),))
    assert len(shared.shared_with) == 1
    with pytest.raises(ValidationError):
        Provenance(category="derived", source_ref="model")
    derived = Provenance(category="derived", source_ref="model", derived_from=("event.1",))
    assert derived.derived_from == ("event.1",)


def test_memory_temporal_and_deletion_metadata(contracts):
    memory = example(contracts, "memory")
    data = memory.model_dump(mode="json")
    now = utc_now()
    data.update(valid_from=now.isoformat(), valid_to=(now - timedelta(seconds=1)).isoformat())
    with pytest.raises(ValidationError):
        type(memory).model_validate_json(json.dumps(data))
    data["valid_to"] = (now + timedelta(days=1)).isoformat()
    data.update(consent_ref="consent.1", retention_ref="retention.1", deletion_state="tombstoned")
    restored = type(memory).model_validate_json(json.dumps(data))
    assert restored.valid_from != restored.created_at
    assert restored.deletion_state == "tombstoned"


@pytest.mark.parametrize("confidence", [-0.1, 1.1, float("nan"), float("inf")])
@pytest.mark.parametrize("name", ["memory", "observation"])
def test_invalid_confidence_rejected(contracts, name, confidence):
    contract = example(contracts, name)
    with pytest.raises(ValidationError):
        type(contract).model_validate({**contract.model_dump(), "confidence": confidence})


def test_observation_distinguishes_evidence_from_inference(contracts):
    observation = example(contracts, "observation")
    data = observation.model_dump()
    with pytest.raises(ValidationError):
        type(observation).model_validate({**data, "evidence_kind": "inferred"})
    data["source"] = Provenance(
        category="derived", source_ref="fake.model", derived_from=("sensor.event",)
    )
    with pytest.raises(ValidationError):
        type(observation).model_validate(data)
    inferred = type(observation).model_validate({**data, "evidence_kind": "inferred"})
    assert inferred.source.derived_from == ("sensor.event",)


@pytest.mark.parametrize(
    "status", ["denied", "validation_failure", "execution_failure", "timeout", "cancelled"]
)
def test_failure_envelope_requires_error_and_correlation(status):
    args = dict(proposal_id=uuid4(), correlation_id=uuid4(), status=status)
    with pytest.raises(ValidationError):
        ExecutionResult(**args)
    result = ExecutionResult(**args, error=ErrorDetail(code=status, message="Simulated failure"))
    assert decode_contract(result.model_dump_json()) == result
    assert result.correlation_id == args["correlation_id"]
    with pytest.raises(ValidationError):
        ExecutionResult(**args, error=result.error, output={"secret": "must not return"})


def test_success_cannot_hide_error():
    args = dict(proposal_id=uuid4(), correlation_id=uuid4(), status="success")
    assert ExecutionResult(**args, output={"ok": True}).error is None
    with pytest.raises(ValidationError):
        ExecutionResult(**args, error=ErrorDetail(code="failed", message="Failure"))


def test_task_failure_and_retry_metadata_are_explicit():
    args = dict(owner_id=uuid4(), correlation_id=uuid4())
    with pytest.raises(ValidationError):
        Task(**args, state="failed")
    error = ErrorDetail(code="adapter_failed", message="Adapter failed", retryable=True)
    task = Task(**args, state="failed", error=error, attempt=2, idempotency_key="request.1")
    assert decode_contract(task.model_dump_json()) == task
    with pytest.raises(ValidationError):
        Task(**args, error=error)
    with pytest.raises(ValidationError):
        Task(**args, attempt=-1)
    now = utc_now()
    with pytest.raises(ValidationError):
        Task(**args, created_at=now, updated_at=now - timedelta(seconds=1))
