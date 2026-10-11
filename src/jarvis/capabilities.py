"""Trusted provider registration and discovery; invocation is confined to the executor."""

from dataclasses import dataclass
from threading import RLock
from typing import Protocol
from uuid import UUID

from jsonschema import Draft202012Validator
from pydantic import JsonValue

from jarvis.core.contracts import AccessScope, ActionProposal, Capability


class CapabilityProvider(Protocol):
    def __call__(self, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]: ...


@dataclass(frozen=True)
class _Registration:
    capability: Capability
    provider: CapabilityProvider | None
    access_scope: AccessScope | None
    supports_idempotency: bool = False
    execution_guard_family: str | None = None


def _local_schema_only(value: JsonValue) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "$id":
                raise ValueError("schema base relocation is unsupported; use document-local $defs")
            if key in ("$ref", "$dynamicRef", "$recursiveRef"):
                if not isinstance(nested, str) or not nested.startswith("#"):
                    raise ValueError("only document-local schema references are supported")
            if key == "$schema" and nested not in (
                "https://json-schema.org/draft/2020-12/schema",
                "https://json-schema.org/draft/2020-12/schema#",
            ):
                raise ValueError("only JSON Schema Draft 2020-12 is supported")
            _local_schema_only(nested)
    elif isinstance(value, list):
        for nested in value:
            _local_schema_only(nested)


class CapabilityRegistry(Protocol):
    def register(
        self,
        capability: Capability,
        provider: CapabilityProvider | None = None,
        *,
        access_scope: AccessScope | None = None,
        supports_idempotency: bool = False,
        execution_guard_family: str | None = None,
    ) -> None: ...

    def get(self, capability_id: str) -> Capability: ...

    def discover(self) -> tuple[Capability, ...]: ...

    def _resolve_for_executor(self, capability_id: str) -> _Registration: ...


class InMemoryCapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, _Registration] = {}
        self._lock = RLock()

    def register(
        self,
        capability: Capability,
        provider: CapabilityProvider | None = None,
        *,
        access_scope: AccessScope | None = None,
        supports_idempotency: bool = False,
        execution_guard_family: str | None = None,
    ) -> None:
        if execution_guard_family is not None and (not execution_guard_family.isidentifier()):
            raise ValueError("invalid required execution guard")
        # Revalidate even objects built via unchecked model_copy/model_construct.
        capability = Capability.model_validate(capability.model_dump())
        if type(supports_idempotency) is not bool or (supports_idempotency and provider is None):
            raise ValueError("idempotency requires a trusted callable provider")
        if provider is not None and (not callable(provider) or not capability.required_permissions):
            raise ValueError("callable capabilities require a provider and explicit permissions")
        for schema in (capability.input_schema, capability.output_schema):
            Draft202012Validator.check_schema(schema)
            _local_schema_only(schema)
        if access_scope is not None:
            access_scope = AccessScope.model_validate(access_scope.model_dump())
        # Payload dictionaries are mutable; do not expose the registry's stored copy.
        with self._lock:
            if capability.capability_id in self._capabilities:
                raise ValueError(f"duplicate capability: {capability.capability_id}")
            self._capabilities[capability.capability_id] = _Registration(
                capability.model_copy(deep=True),
                provider,
                access_scope,
                supports_idempotency,
                execution_guard_family,
            )

    def get(self, capability_id: str) -> Capability:
        return self._resolve_for_executor(capability_id).capability

    def discover(self) -> tuple[Capability, ...]:
        with self._lock:
            return tuple(self.get(key) for key in sorted(self._capabilities))

    def discover_authorized(self, *, session_token: str, policy, correlation_id: UUID):
        """Trusted discovery uses the existing policy and registration scope, never handlers."""
        with policy.identities.transaction():
            session = policy.identities.resolve_session(session_token)
            available = []
            for capability in self.discover():
                registered = self._resolve_for_executor(capability.capability_id)
                if registered.execution_guard_family is not None:
                    continue  # Internal task-bound transports are not conversational tools.
                proposal = ActionProposal(
                    session_id=session.session_id,
                    capability_id=capability.capability_id,
                    correlation_id=correlation_id,
                    origin="rule",
                    source_ref="jarvis.capability.discovery",
                )
                decision = policy.evaluate(
                    session_token, proposal, capability, access_scope=registered.access_scope
                )
                if decision.decision == "allow":
                    available.append(capability)
            return tuple(available)

    def _resolve_for_executor(self, capability_id: str) -> _Registration:
        """Trusted internal dependency API, never exposed to a model or request transport."""
        with self._lock:
            registered = self._capabilities[capability_id]
            return _Registration(
                registered.capability.model_copy(deep=True),
                registered.provider,
                registered.access_scope,
                registered.supports_idempotency,
                registered.execution_guard_family,
            )
