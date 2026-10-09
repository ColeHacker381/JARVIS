"""Offline simulated providers and explicit test-fixture bootstrap demonstration."""

from dataclasses import dataclass, field
from uuid import uuid4

from pydantic import JsonValue

from jarvis.authorization import DeterministicPolicy
from jarvis.capabilities import InMemoryCapabilityRegistry
from jarvis.core.contracts import ActionProposal, Capability
from jarvis.execution import CapabilityExecutor
from jarvis.identity import InMemoryIdentityService, SessionCredentials
from jarvis.observability import InMemoryAuditSink
from jarvis.permissions import InMemoryPermissionStore

ECHO_PERMISSION = "development.echo.invoke"
BRIGHTNESS_PERMISSION = "home.lighting.set_brightness"


@dataclass
class SimulatedLighting:
    brightness: int = 0
    calls: list[int] = field(default_factory=list)

    def __call__(self, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.brightness = arguments["brightness"]
        self.calls.append(self.brightness)
        return {"brightness": self.brightness, "simulated": True}


def register_development_capabilities(registry: InMemoryCapabilityRegistry) -> SimulatedLighting:
    registry.register(
        Capability(
            capability_id="development.echo",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": 4096}},
                "required": ["text"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            required_permissions=(ECHO_PERMISSION,),
            adapter_ref="development.fake_echo",
        ),
        lambda arguments: {"text": arguments["text"]},
    )
    lighting = SimulatedLighting()
    registry.register(
        Capability(
            capability_id="home.lighting.set_brightness",
            input_schema={
                "type": "object",
                "properties": {"brightness": {"type": "integer", "minimum": 0, "maximum": 100}},
                "required": ["brightness"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "brightness": {"type": "integer", "minimum": 0, "maximum": 100},
                    "simulated": {"const": True},
                },
                "required": ["brightness", "simulated"],
                "additionalProperties": False,
            },
            required_permissions=(BRIGHTNESS_PERMISSION,),
            side_effect="local",
            adapter_ref="development.simulated_lighting",
        ),
        lighting,
    )
    return lighting


def proposal_for(
    credentials: SessionCredentials, capability_id: str, arguments: dict[str, JsonValue]
) -> ActionProposal:
    return ActionProposal(
        session_id=credentials.session_id,
        capability_id=capability_id,
        arguments=arguments,
        correlation_id=uuid4(),
        origin="user",
        source_ref="development.demo",
    )


def run_demo() -> dict[str, object]:
    audit = InMemoryAuditSink()
    identities = InMemoryIdentityService()
    owner = identities.bootstrap_owner_for_testing()
    friend = identities.create_principal_for_testing("Trusted friend", relationship_ref="friend")
    guest = identities.create_principal_for_testing("Guest", kind="guest")
    owner_session = identities.establish_session_for_testing(owner.principal_id)
    friend_session = identities.establish_session_for_testing(friend.principal_id)
    guest_session = identities.establish_session_for_testing(guest.principal_id)
    permissions = InMemoryPermissionStore(identities, audit)
    registry = InMemoryCapabilityRegistry()
    lighting = register_development_capabilities(registry)
    policy = DeterministicPolicy(identities, permissions, audit)
    executor = CapabilityExecutor(identities, registry, policy, audit)
    permissions.grant(
        owner_session.token, owner.principal_id, BRIGHTNESS_PERMISSION, correlation_id=uuid4()
    )
    permissions.grant(
        owner_session.token, guest.principal_id, ECHO_PERMISSION, correlation_id=uuid4()
    )
    results = {}
    owner_action = proposal_for(owner_session, "home.lighting.set_brightness", {"brightness": 25})
    results["owner_access"] = executor.execute(owner_action, session_token=owner_session.token)
    guest_echo = proposal_for(guest_session, "development.echo", {"text": "offline hello"})
    results["guest_echo"] = executor.execute(guest_echo, session_token=guest_session.token)
    guest_action = proposal_for(guest_session, "home.lighting.set_brightness", {"brightness": 99})
    results["guest_denied"] = executor.execute(guest_action, session_token=guest_session.token)
    friend_action = proposal_for(friend_session, "home.lighting.set_brightness", {"brightness": 50})
    results["friend_before_grant"] = executor.execute(
        friend_action, session_token=friend_session.token
    )
    permissions.grant(
        owner_session.token, friend.principal_id, BRIGHTNESS_PERMISSION, correlation_id=uuid4()
    )
    results["friend_granted"] = executor.execute(friend_action, session_token=friend_session.token)
    permissions.revoke(
        owner_session.token, friend.principal_id, BRIGHTNESS_PERMISSION, correlation_id=uuid4()
    )
    results["friend_revoked"] = executor.execute(friend_action, session_token=friend_session.token)
    expected = {
        "owner_access": "success",
        "guest_echo": "success",
        "guest_denied": "denied",
        "friend_before_grant": "denied",
        "friend_granted": "success",
        "friend_revoked": "denied",
    }
    if {name: result.status for name, result in results.items()} != expected or lighting.calls != [
        25,
        50,
    ]:
        raise RuntimeError("Phase 2 demonstration failed")
    return {
        "status": "ok",
        "mode": "offline_test_fixture",
        "results": {name: result.model_dump(mode="json") for name, result in results.items()},
        "lighting_calls": lighting.calls,
        "audit": [record.model_dump(mode="json") for record in audit.records()],
    }
