"""In-memory metadata discovery, deliberately without invocation or dynamic imports."""

from typing import Protocol

from jsonschema import Draft202012Validator

from jarvis.core.contracts import Capability


class CapabilityRegistry(Protocol):
    def register(self, capability: Capability) -> None: ...

    def get(self, capability_id: str) -> Capability: ...

    def discover(self) -> tuple[Capability, ...]: ...


class InMemoryCapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        if capability.capability_id in self._capabilities:
            raise ValueError(f"duplicate capability: {capability.capability_id}")
        for schema in (capability.input_schema, capability.output_schema):
            Draft202012Validator.check_schema(schema)
        # Payload dictionaries are mutable; do not expose the registry's stored copy.
        self._capabilities[capability.capability_id] = capability.model_copy(deep=True)

    def get(self, capability_id: str) -> Capability:
        return self._capabilities[capability_id].model_copy(deep=True)

    def discover(self) -> tuple[Capability, ...]:
        return tuple(self.get(key) for key in sorted(self._capabilities))
