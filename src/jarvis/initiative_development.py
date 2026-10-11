"""Explicit simulated local-inbox capability. No messaging or real device adapters."""

from dataclasses import dataclass, field

from jarvis.core.contracts import Capability


@dataclass
class SimulatedNotice:
    calls: list = field(default_factory=list)

    def __call__(self, arguments):
        self.calls.append(arguments["text"])
        return {"recorded": arguments["text"], "simulated": True}


def register_initiative_capability(registry):
    notice = SimulatedNotice()
    registry.register(
        Capability(
            capability_id="development.simulated_notice",
            adapter_ref="development.simulated_notice",
            side_effect="local",
            required_permissions=("development.simulated_notice.invoke",),
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 512}},
                "required": ["text"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"recorded": {"type": "string"}, "simulated": {"const": True}},
                "required": ["recorded", "simulated"],
                "additionalProperties": False,
            },
        ),
        notice,
    )
    # Conservative non-idempotent profile: uncertain calls need review, even for this fixture.
    return notice
