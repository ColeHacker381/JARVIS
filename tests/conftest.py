from uuid import uuid4

import pytest

from jarvis.core.contracts import (
    AccessScope,
    ActionProposal,
    AuditRecord,
    Capability,
    ErrorDetail,
    Event,
    ExecutionResult,
    Memory,
    Observation,
    PolicyDecision,
    Principal,
    Provenance,
    Session,
    Task,
)
from jarvis.core.time import utc_now


@pytest.fixture
def contracts():
    principal = Principal(display_name="Owner", kind="owner")
    session = Session(principal_id=principal.principal_id, interface="local")
    correlation_id = uuid4()
    proposal = ActionProposal(
        session_id=session.session_id,
        capability_id="test.echo",
        correlation_id=correlation_id,
        origin="model",
        source_ref="fake.model.v1",
        arguments={"text": "hello", "nested": [1, True, None]},
    )
    source = Provenance(category="user_statement", source_ref="test.request")
    scope = AccessScope(owner_id=principal.principal_id)
    return [
        principal,
        session,
        Event(event_type="test.started", correlation_id=correlation_id, source="test"),
        Capability(
            capability_id="test.echo",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            adapter_ref="test.fake",
        ),
        proposal,
        Task(owner_id=principal.principal_id, correlation_id=correlation_id),
        Memory(kind="person", content={"preference": "tea"}, source=source, access_scope=scope),
        Observation(
            source=Provenance(category="observation", source_ref="fake.sensor"),
            observed_at=utc_now(),
            payload={"temperature": 21.5},
            access_scope=scope,
            confidence=0.9,
        ),
        PolicyDecision(
            proposal_id=proposal.proposal_id,
            session_id=session.session_id,
            principal_id=principal.principal_id,
            correlation_id=correlation_id,
            decision="deny",
            reason_code="not_granted",
            policy_refs=(),
        ),
        AuditRecord(
            principal_id=principal.principal_id,
            session_id=session.session_id,
            proposal_id=proposal.proposal_id,
            correlation_id=correlation_id,
            action="test.echo",
            outcome="denied",
            reason_code="not_granted",
        ),
        ExecutionResult(
            proposal_id=proposal.proposal_id,
            correlation_id=correlation_id,
            status="denied",
            error=ErrorDetail(code="not_granted", message="Permission required"),
        ),
    ]
