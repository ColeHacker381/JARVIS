import json
import subprocess
import sys
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from threading import Event as ThreadEvent
from uuid import uuid4

import pytest
from pydantic import ValidationError

from jarvis.authorization import DeterministicPolicy
from jarvis.capabilities import InMemoryCapabilityRegistry
from jarvis.core.contracts import AccessScope, Capability, Principal, Session
from jarvis.core.time import utc_now
from jarvis.development import (
    BRIGHTNESS_PERMISSION,
    ECHO_PERMISSION,
    SimulatedLighting,
    proposal_for,
    register_development_capabilities,
    run_demo,
)
from jarvis.execution import CapabilityExecutor
from jarvis.identity import IdentityError, InMemoryIdentityService, SessionCredentials
from jarvis.observability import AuditWriteError, InMemoryAuditSink
from jarvis.permissions import InMemoryPermissionStore, PermissionAdministrationError


@dataclass
class Kernel:
    identities: InMemoryIdentityService
    audit: InMemoryAuditSink
    permissions: InMemoryPermissionStore
    registry: InMemoryCapabilityRegistry
    policy: DeterministicPolicy
    executor: CapabilityExecutor
    owner: Principal
    friend: Principal
    guest: Principal
    owner_session: SessionCredentials
    friend_session: SessionCredentials
    guest_session: SessionCredentials
    lighting: SimulatedLighting

    def grant(self, principal, permission):
        return self.permissions.grant(
            self.owner_session.token, principal.principal_id, permission, correlation_id=uuid4()
        )

    def lighting_action(self, credentials, **arguments):
        return proposal_for(
            credentials, "home.lighting.set_brightness", arguments or {"brightness": 40}
        )


@pytest.fixture
def kernel():
    audit = InMemoryAuditSink()
    identities = InMemoryIdentityService()
    owner = identities.bootstrap_owner_for_testing()
    friend = identities.create_principal_for_testing("Friend", relationship_ref="best_friend")
    guest = identities.create_principal_for_testing("Guest", kind="guest")
    sessions = [
        identities.establish_session_for_testing(principal.principal_id)
        for principal in (owner, friend, guest)
    ]
    permissions = InMemoryPermissionStore(identities, audit)
    registry = InMemoryCapabilityRegistry()
    lighting = register_development_capabilities(registry)
    policy = DeterministicPolicy(identities, permissions, audit)
    executor = CapabilityExecutor(identities, registry, policy, audit)
    return Kernel(
        identities,
        audit,
        permissions,
        registry,
        policy,
        executor,
        owner,
        friend,
        guest,
        *sessions,
        lighting,
    )


def test_single_owner_bootstrap_and_unknown_principal(kernel):
    assert kernel.owner.kind == "owner"
    assert kernel.friend.kind == "user"
    assert kernel.guest.kind == "guest"
    assert kernel.friend.policy_refs == ()
    with pytest.raises(ValueError, match="bootstrapped"):
        kernel.identities.bootstrap_owner_for_testing()
    with pytest.raises(ValueError, match="singleton"):
        kernel.identities.create_principal_for_testing("forged owner", kind="owner")
    with pytest.raises(IdentityError):
        kernel.identities.establish_session_for_testing(uuid4())
    unknown = kernel.identities.establish_session_for_testing(None)
    assert kernel.identities.resolve_session(unknown.token).principal_id is None
    assert unknown.token not in repr(unknown)


@pytest.mark.parametrize("actor", ["owner", "friend", "guest"])
def test_default_deny_then_explicit_principal_grant(kernel, actor):
    principal = getattr(kernel, actor)
    credentials = getattr(kernel, actor + "_session")
    proposal = kernel.lighting_action(credentials)
    denied = kernel.executor.execute(proposal, session_token=credentials.token)
    assert denied.status == "denied"
    assert denied.error.code == "permission_missing"
    assert kernel.lighting.calls == []
    kernel.grant(principal, BRIGHTNESS_PERMISSION)
    allowed = kernel.executor.execute(proposal, session_token=credentials.token)
    assert allowed.status == "success"
    assert allowed.output == {"brightness": 40, "simulated": True}
    assert kernel.lighting.calls == [40]


def test_guest_echo_and_guest_denied_side_effect_never_invokes_provider(kernel):
    kernel.grant(kernel.guest, ECHO_PERMISSION)
    echo = proposal_for(kernel.guest_session, "development.echo", {"text": "hello"})
    assert kernel.executor.execute(echo, session_token=kernel.guest_session.token).output == {
        "text": "hello"
    }
    denied = kernel.executor.execute(
        kernel.lighting_action(kernel.guest_session), session_token=kernel.guest_session.token
    )
    assert denied.status == "denied"
    assert kernel.lighting.calls == []


def test_permission_revocation_rejects_preexisting_proposal_and_cached_decision(kernel):
    grant = kernel.grant(kernel.friend, BRIGHTNESS_PERMISSION)
    action = kernel.lighting_action(kernel.friend_session)
    cached = kernel.policy.evaluate(
        kernel.friend_session.token, action, kernel.registry.get(action.capability_id)
    )
    assert cached.decision == "allow"
    assert (
        kernel.executor.execute(action, session_token=kernel.friend_session.token).status
        == "success"
    )
    kernel.permissions.revoke(
        kernel.owner_session.token,
        kernel.friend.principal_id,
        BRIGHTNESS_PERMISSION,
        correlation_id=uuid4(),
    )
    assert kernel.permissions.inspect_grant(kernel.owner_session.token, grant.policy_ref) == grant
    denied = kernel.executor.execute(action, session_token=kernel.friend_session.token)
    assert denied.status == "denied"
    assert kernel.lighting.calls == [40]
    with pytest.raises(TypeError):
        kernel.executor.execute(action, session_token=kernel.friend_session.token, decision=cached)


def test_grants_are_exact_not_role_relationship_or_wildcard_inheritance(kernel):
    for permission in (BRIGHTNESS_PERMISSION + ".other", "*", "admin"):
        kernel.grant(kernel.friend, permission)
    assert (
        kernel.executor.execute(
            kernel.lighting_action(kernel.friend_session), session_token=kernel.friend_session.token
        ).status
        == "denied"
    )
    kernel.grant(kernel.owner, BRIGHTNESS_PERMISSION)
    assert (
        kernel.executor.execute(
            kernel.lighting_action(kernel.friend_session), session_token=kernel.friend_session.token
        ).status
        == "denied"
    )
    assert kernel.lighting.calls == []


@pytest.mark.parametrize("actor", ["friend", "guest", "unknown", "invalid"])
@pytest.mark.parametrize("operation", ["grant", "revoke"])
def test_permission_administration_requires_real_owner_session(kernel, actor, operation):
    kernel.grant(kernel.friend, BRIGHTNESS_PERMISSION)
    if actor == "unknown":
        token = kernel.identities.establish_session_for_testing(None).token
    elif actor == "invalid":
        token = str(kernel.owner.principal_id)
    else:
        token = getattr(kernel, actor + "_session").token
    with pytest.raises(PermissionAdministrationError):
        getattr(kernel.permissions, operation)(
            token, kernel.friend.principal_id, BRIGHTNESS_PERMISSION, correlation_id=uuid4()
        )
    assert (
        len(kernel.permissions.current_grants(kernel.friend.principal_id, (BRIGHTNESS_PERMISSION,)))
        == 1
    )
    record = kernel.audit.records()[-1]
    assert record.action == "permission." + operation
    assert record.outcome == "denied"
    assert record.principal_id != kernel.owner.principal_id


def test_idempotent_grant_revoke_and_structured_audit_metadata(kernel):
    first = kernel.grant(kernel.friend, BRIGHTNESS_PERMISSION)
    assert kernel.grant(kernel.friend, BRIGHTNESS_PERMISSION) == first
    assert first.principal_id == kernel.friend.principal_id
    assert first.granted_by == kernel.owner.principal_id
    assert first.permission == BRIGHTNESS_PERMISSION
    kernel.permissions.revoke(
        kernel.owner_session.token,
        kernel.friend.principal_id,
        BRIGHTNESS_PERMISSION,
        correlation_id=uuid4(),
    )
    kernel.permissions.revoke(
        kernel.owner_session.token,
        kernel.friend.principal_id,
        BRIGHTNESS_PERMISSION,
        correlation_id=uuid4(),
    )
    assert (
        kernel.permissions.current_grants(kernel.friend.principal_id, (BRIGHTNESS_PERMISSION,))
        == ()
    )
    assert first.policy_ref in kernel.audit.records()[0].policy_refs
    assert all(record.outcome == "succeeded" for record in kernel.audit.records())
    with pytest.raises(PermissionAdministrationError):
        kernel.permissions.inspect_grant(kernel.guest_session.token, first.policy_ref)


def test_invalid_permission_and_unknown_target_are_rejected_and_audited(kernel):
    for permission, target, code in (
        (" ", kernel.friend.principal_id, "permission_invalid"),
        (BRIGHTNESS_PERMISSION, uuid4(), "unknown_or_disabled_target"),
    ):
        with pytest.raises(PermissionAdministrationError, match=code):
            kernel.permissions.grant(
                kernel.owner_session.token, target, permission, correlation_id=uuid4()
            )
        assert kernel.audit.records()[-1].reason_code == code
    assert (
        kernel.permissions.current_grants(kernel.friend.principal_id, (BRIGHTNESS_PERMISSION,))
        == ()
    )


@pytest.mark.parametrize(
    "impersonation", ["proposal_session", "principal_id", "session_id", "session_object"]
)
def test_guest_cannot_impersonate_owner_even_with_valid_contract_ids(kernel, impersonation):
    kernel.grant(kernel.owner, BRIGHTNESS_PERMISSION)
    proposal = kernel.lighting_action(kernel.guest_session)
    token = kernel.guest_session.token
    if impersonation == "proposal_session":
        proposal = proposal.model_copy(update={"session_id": kernel.owner_session.session_id})
    elif impersonation == "principal_id":
        token = str(kernel.owner.principal_id)
    elif impersonation == "session_id":
        token = str(kernel.owner_session.session_id)
    else:
        token = Session(
            session_id=kernel.owner_session.session_id,
            principal_id=kernel.owner.principal_id,
            authentication_ref="development.test_fixture",
            interface="development",
        )
    result = kernel.executor.execute(proposal, session_token=token)
    assert result.status == "denied"
    assert kernel.lighting.calls == []
    assert kernel.audit.records()[-1].outcome == "denied"


def test_model_claims_and_forged_decisions_have_no_authority(kernel):
    action = kernel.lighting_action(kernel.guest_session).model_copy(
        update={"origin": "model", "source_ref": "owner_authorized_admin_best_friend"}
    )
    assert (
        kernel.executor.execute(action, session_token=kernel.guest_session.token).status == "denied"
    )
    for field in ("authorized", "policy_decision", "principal_id"):
        with pytest.raises(ValidationError):
            type(action).model_validate({**action.model_dump(), field: True})
    assert not hasattr(kernel.registry, "execute")
    assert not hasattr(kernel.executor, "execute_authorized")
    assert kernel.lighting.calls == []


@pytest.mark.parametrize(
    "change", ["disabled_principal", "closed_session", "expired_session", "unknown_principal"]
)
def test_identity_revocation_is_checked_at_invocation(kernel, change, monkeypatch):
    kernel.grant(kernel.friend, BRIGHTNESS_PERMISSION)
    credentials = kernel.friend_session
    if change == "expired_session":
        expiry = utc_now() + timedelta(seconds=10)
        credentials = kernel.identities.establish_session_for_testing(
            kernel.friend.principal_id, expires_at=expiry
        )
        monkeypatch.setattr("jarvis.identity.utc_now", lambda: expiry + timedelta(seconds=1))
    elif change == "disabled_principal":
        kernel.identities.disable_principal_for_testing(kernel.friend.principal_id)
    elif change == "closed_session":
        kernel.identities.close_session_for_testing(credentials.token)
    else:
        credentials = kernel.identities.establish_session_for_testing(None)
    result = kernel.executor.execute(
        kernel.lighting_action(credentials), session_token=credentials.token
    )
    assert result.status == "denied"
    assert kernel.lighting.calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"brightness": -1},
        {"brightness": 101},
        {"brightness": "50"},
        {"brightness": True},
        {"brightness": 1.5},
        {"brightness": 50, "principal_id": "owner"},
    ],
)
def test_invalid_input_never_causes_a_side_effect(kernel, arguments):
    kernel.grant(kernel.owner, BRIGHTNESS_PERMISSION)
    action = proposal_for(kernel.owner_session, "home.lighting.set_brightness", arguments)
    result = kernel.executor.execute(action, session_token=kernel.owner_session.token)
    assert result.status == "validation_failure"
    assert result.error.code == "input_invalid"
    assert kernel.lighting.calls == []
    assert kernel.audit.records()[-1].outcome == "failed"


def test_unknown_capability_and_metadata_only_provider_failure(kernel):
    action = proposal_for(kernel.owner_session, "unknown", {})
    assert (
        kernel.executor.execute(action, session_token=kernel.owner_session.token).error.code
        == "capability_unknown"
    )
    kernel.registry.register(
        Capability(
            capability_id="test.metadata",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            required_permissions=("test.metadata",),
            adapter_ref="test",
        )
    )
    kernel.grant(kernel.owner, "test.metadata")
    result = kernel.executor.execute(
        proposal_for(kernel.owner_session, "test.metadata", {}),
        session_token=kernel.owner_session.token,
    )
    assert result.status == "execution_failure"
    assert result.error.code == "provider_unavailable"


def custom_capability(kernel, provider, *, scope=None, input_schema=None):
    kernel.registry.register(
        Capability(
            capability_id="test.private",
            input_schema=input_schema or {"type": "object"},
            output_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            required_permissions=("test.private.read",),
            adapter_ref="test.private_fake",
        ),
        provider,
        access_scope=scope,
    )
    return "test.private"


@pytest.mark.parametrize("shared", [False, True])
def test_private_context_is_filtered_before_handler_and_not_leaked_to_other_users(kernel, shared):
    calls = []

    def provider(arguments):
        calls.append(arguments)
        return {"value": "owner-private-context"}

    scope = AccessScope(
        owner_id=kernel.owner.principal_id,
        kind="shared" if shared else "private",
        shared_with=(kernel.friend.principal_id,) if shared else (),
    )
    custom_capability(kernel, provider, scope=scope)
    for principal in (kernel.owner, kernel.friend, kernel.guest):
        kernel.grant(principal, "test.private.read")
    for actor, allowed in (("owner", True), ("friend", shared), ("guest", False)):
        credentials = getattr(kernel, actor + "_session")
        result = kernel.executor.execute(
            proposal_for(credentials, "test.private", {}), session_token=credentials.token
        )
        assert result.status == ("success" if allowed else "denied")
        if not allowed:
            assert result.output is None
            assert "owner-private-context" not in result.model_dump_json()
    assert len(calls) == (2 if shared else 1)
    assert "owner-private-context" not in json.dumps(
        [record.model_dump(mode="json") for record in kernel.audit.records()]
    )


@pytest.mark.parametrize(
    "failure,code,status",
    [
        (ValueError("secret_exception"), "provider_failed", "execution_failure"),
        (TimeoutError("secret_exception"), "provider_timeout", "timeout"),
        (CancelledError("secret_exception"), "provider_cancelled", "cancelled"),
    ],
)
def test_provider_failures_are_structured_correlated_and_sanitized(
    kernel, failure, code, status, caplog
):
    def provider(arguments):
        raise failure

    custom_capability(kernel, provider)
    kernel.grant(kernel.owner, "test.private.read")
    action = proposal_for(kernel.owner_session, "test.private", {})
    result = kernel.executor.execute(action, session_token=kernel.owner_session.token)
    assert result.status == status
    assert result.error.code == code
    assert result.correlation_id == action.correlation_id
    assert result.proposal_id == action.proposal_id
    assert "secret_exception" not in result.model_dump_json()
    assert "secret_exception" not in caplog.text
    assert kernel.audit.records()[-1].reason_code == code


@pytest.mark.parametrize(
    "output",
    [
        {},
        {"value": 1},
        {"value": "private", "extra": "secret"},
        ["private"],
        {"value": float("nan")},
        {"value": object()},
    ],
)
def test_invalid_output_is_not_returned_or_logged(kernel, output):
    custom_capability(kernel, lambda arguments: output)
    kernel.grant(kernel.owner, "test.private.read")
    result = kernel.executor.execute(
        proposal_for(kernel.owner_session, "test.private", {}),
        session_token=kernel.owner_session.token,
    )
    assert result.status == "execution_failure"
    assert result.error.code == "output_invalid"
    assert result.output is None
    assert "secret" not in result.model_dump_json()


def test_audit_links_current_decisions_execution_and_grants_without_payloads(kernel):
    grant = kernel.grant(kernel.owner, BRIGHTNESS_PERMISSION)
    action = kernel.lighting_action(kernel.owner_session)
    kernel.executor.execute(action, session_token=kernel.owner_session.token)
    decision, outcome = kernel.audit.records()[-2:]
    assert decision.outcome == "allowed"
    assert outcome.outcome == "succeeded"
    assert decision.decision_id == outcome.decision_id
    assert decision.principal_id == kernel.owner.principal_id
    assert decision.session_id == action.session_id
    assert decision.proposal_id == outcome.proposal_id == action.proposal_id
    assert decision.correlation_id == outcome.correlation_id == action.correlation_id
    assert grant.policy_ref in decision.policy_refs
    assert '"brightness":' not in decision.model_dump_json()
    assert "arguments" not in decision.model_dump()
    assert "output" not in decision.model_dump()
    assert kernel.owner_session.token not in str(kernel.audit.records())


class SwitchableAudit(InMemoryAuditSink):
    fail = False
    fail_outcome = False

    def append(self, record):
        if self.fail or (self.fail_outcome and record.outcome == "succeeded"):
            raise RuntimeError("sensitive_sink_failure")
        super().append(record)


def test_audit_failure_prevents_invocation_and_permission_mutation(kernel):
    audit = SwitchableAudit()
    permissions = InMemoryPermissionStore(kernel.identities, audit)
    policy = DeterministicPolicy(kernel.identities, permissions, audit)
    executor = CapabilityExecutor(kernel.identities, kernel.registry, policy, audit)
    permissions.grant(
        kernel.owner_session.token,
        kernel.owner.principal_id,
        BRIGHTNESS_PERMISSION,
        correlation_id=uuid4(),
    )
    audit.fail = True
    action = kernel.lighting_action(kernel.owner_session)
    result = executor.execute(action, session_token=kernel.owner_session.token)
    assert result.error.code == "audit_unavailable"
    assert kernel.lighting.calls == []
    for operation in ("grant", "revoke"):
        with pytest.raises(AuditWriteError):
            getattr(permissions, operation)(
                kernel.owner_session.token,
                kernel.owner.principal_id,
                BRIGHTNESS_PERMISSION,
                correlation_id=uuid4(),
            )
    assert len(permissions.current_grants(kernel.owner.principal_id, (BRIGHTNESS_PERMISSION,))) == 1
    with pytest.raises(AuditWriteError):
        permissions.grant(
            kernel.owner_session.token,
            kernel.friend.principal_id,
            BRIGHTNESS_PERMISSION,
            correlation_id=uuid4(),
        )
    assert permissions.current_grants(kernel.friend.principal_id, (BRIGHTNESS_PERMISSION,)) == ()


def test_outcome_audit_failure_does_not_claim_success_or_rollback(kernel):
    audit = SwitchableAudit()
    policy = DeterministicPolicy(kernel.identities, kernel.permissions, audit)
    executor = CapabilityExecutor(kernel.identities, kernel.registry, policy, audit)
    kernel.grant(kernel.owner, BRIGHTNESS_PERMISSION)
    audit.fail_outcome = True
    result = executor.execute(
        kernel.lighting_action(kernel.owner_session), session_token=kernel.owner_session.token
    )
    assert result.status == "execution_failure"
    assert result.error.code == "audit_unavailable"
    assert result.error.retryable is False
    assert kernel.lighting.calls == [40]


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://example.invalid/schema"},
        {"$dynamicRef": "file:///tmp/private"},
        {"$id": "https://example.invalid/base", "$ref": "#/schema"},
        {"$schema": "https://json-schema.org/draft-07/schema"},
    ],
)
def test_remote_or_relocated_schemas_are_rejected_at_registration(kernel, schema):
    with pytest.raises(ValueError):
        custom_capability(kernel, lambda arguments: {"value": "ok"}, input_schema=schema)
    assert "test.private" not in [item.capability_id for item in kernel.registry.discover()]


def test_local_schema_reference_and_unresolvable_reference_never_invoke_invalid_input(kernel):
    calls = []
    custom_capability(
        kernel,
        lambda arguments: calls.append(arguments) or {"value": "ok"},
        input_schema={
            "$defs": {"request": {"type": "object", "required": ["text"]}},
            "$ref": "#/$defs/request",
        },
    )
    kernel.grant(kernel.owner, "test.private.read")
    action = proposal_for(kernel.owner_session, "test.private", {})
    assert (
        kernel.executor.execute(action, session_token=kernel.owner_session.token).status
        == "validation_failure"
    )
    action = proposal_for(kernel.owner_session, "test.private", {"text": "hello"})
    assert (
        kernel.executor.execute(action, session_token=kernel.owner_session.token).status
        == "success"
    )
    assert calls == [{"text": "hello"}]


def test_registration_requires_explicit_permissions_for_callable_provider(kernel):
    cap = Capability(
        capability_id="test.unsafe", input_schema={}, output_schema={}, adapter_ref="test"
    )
    with pytest.raises(ValueError):
        kernel.registry.register(cap, lambda arguments: {})
    kernel.registry.register(cap)
    result = kernel.executor.execute(
        proposal_for(kernel.owner_session, cap.capability_id, {}),
        session_token=kernel.owner_session.token,
    )
    assert result.status == "denied"
    assert result.error.code == "permission_declaration_missing"


def test_provider_mutation_does_not_change_proposal_or_registry_metadata(kernel):
    def provider(arguments):
        arguments["nested"]["secret"] = "provider-mutated"
        return {"value": "ok"}

    custom_capability(kernel, provider)
    kernel.grant(kernel.owner, "test.private.read")
    action = proposal_for(kernel.owner_session, "test.private", {"nested": {"secret": "original"}})
    assert (
        kernel.executor.execute(action, session_token=kernel.owner_session.token).status
        == "success"
    )
    assert action.arguments["nested"]["secret"] == "original"
    metadata = kernel.registry.get("test.private")
    metadata.input_schema["type"] = "string"
    assert kernel.registry.get("test.private").input_schema["type"] == "object"


def test_all_declared_permissions_are_required(kernel):
    calls = []
    kernel.registry.register(
        Capability(
            capability_id="test.two",
            input_schema={},
            output_schema={},
            adapter_ref="test",
            required_permissions=("permission.a", "permission.b"),
        ),
        lambda arguments: calls.append(arguments) or {},
    )
    kernel.grant(kernel.friend, "permission.a")
    action = proposal_for(kernel.friend_session, "test.two", {})
    assert (
        kernel.executor.execute(action, session_token=kernel.friend_session.token).status
        == "denied"
    )
    assert calls == []
    kernel.grant(kernel.friend, "permission.b")
    assert (
        kernel.executor.execute(action, session_token=kernel.friend_session.token).status
        == "success"
    )


def test_inflight_execution_and_revocation_have_a_defined_serial_order(kernel):
    authorized = ThreadEvent()
    resume = ThreadEvent()
    revoke_attempted = ThreadEvent()
    revoked = ThreadEvent()

    class PausingAudit(InMemoryAuditSink):
        def append(self, record):
            if record.outcome == "allowed":
                authorized.set()
                assert resume.wait(2)
            super().append(record)

    policy = DeterministicPolicy(kernel.identities, kernel.permissions, PausingAudit())
    executor = CapabilityExecutor(kernel.identities, kernel.registry, policy, kernel.audit)
    kernel.grant(kernel.friend, BRIGHTNESS_PERMISSION)
    action = kernel.lighting_action(kernel.friend_session)

    def revoke():
        revoke_attempted.set()
        kernel.permissions.revoke(
            kernel.owner_session.token,
            kernel.friend.principal_id,
            BRIGHTNESS_PERMISSION,
            correlation_id=uuid4(),
        )
        revoked.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(executor.execute, action, session_token=kernel.friend_session.token)
        try:
            assert authorized.wait(2)
            revocation = pool.submit(revoke)
            assert revoke_attempted.wait(2)
            assert not revoked.wait(0.05)
        finally:
            resume.set()
        assert future.result(timeout=2).status == "success"
        revocation.result(timeout=2)
    assert revoked.is_set()
    assert executor.execute(action, session_token=kernel.friend_session.token).status == "denied"
    assert kernel.lighting.calls == [40]


def test_demo_calls_the_real_pipeline_and_cli(kernel):
    result = run_demo()
    assert result["lighting_calls"] == [25, 50]
    assert result["results"]["friend_revoked"]["status"] == "denied"
    assert any(record["action"] == "permission.revoke" for record in result["audit"])
    process = subprocess.run(
        [sys.executable, "-m", "jarvis", "demo"], capture_output=True, text=True, check=True
    )
    assert json.loads(process.stdout)["lighting_calls"] == [25, 50]
    assert all(
        json.loads(line)["event"] == "capability.execution" for line in process.stderr.splitlines()
    )


def test_policy_adapter_failure_is_structured_and_never_invokes_provider(kernel):
    class UnavailablePolicy:
        identities = kernel.identities

        def evaluate(self, *args, **kwargs):
            raise RuntimeError("sensitive_backend_message")

    kernel.grant(kernel.owner, BRIGHTNESS_PERMISSION)
    executor = CapabilityExecutor(
        kernel.identities, kernel.registry, UnavailablePolicy(), kernel.audit
    )
    result = executor.execute(
        kernel.lighting_action(kernel.owner_session), session_token=kernel.owner_session.token
    )
    assert result.status == "execution_failure"
    assert result.error.code == "policy_unavailable"
    assert "sensitive_backend_message" not in result.model_dump_json()
    assert kernel.lighting.calls == []


@pytest.mark.parametrize("field", ["proposal_id", "session_id", "principal_id", "correlation_id"])
def test_misbound_policy_decision_is_rejected_before_provider_invocation(kernel, field):
    class MisboundPolicy:
        identities = kernel.identities

        def evaluate(self, *args, **kwargs):
            decision = kernel.policy.evaluate(*args, **kwargs)
            return decision.model_copy(update={field: uuid4()})

    kernel.grant(kernel.owner, BRIGHTNESS_PERMISSION)
    executor = CapabilityExecutor(
        kernel.identities, kernel.registry, MisboundPolicy(), kernel.audit
    )
    result = executor.execute(
        kernel.lighting_action(kernel.owner_session), session_token=kernel.owner_session.token
    )
    assert result.status == "execution_failure"
    assert result.error.code == "policy_decision_mismatch"
    assert kernel.lighting.calls == []


def test_services_cannot_be_wired_to_different_identity_states(kernel):
    other = InMemoryIdentityService()
    with pytest.raises(ValueError, match="share"):
        DeterministicPolicy(other, kernel.permissions, kernel.audit)
    with pytest.raises(ValueError, match="share"):
        CapabilityExecutor(other, kernel.registry, kernel.policy, kernel.audit)
