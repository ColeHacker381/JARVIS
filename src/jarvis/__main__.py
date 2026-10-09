"""Offline smoke check for installed contracts and capability metadata discovery."""

import argparse
import json
from uuid import uuid4

from jarvis.capabilities import InMemoryCapabilityRegistry
from jarvis.config import Settings
from jarvis.core.contracts import ActionProposal, Capability, Principal, Session, decode_contract
from jarvis.observability import configure_logging


def smoke() -> dict[str, object]:
    settings = Settings.from_environment()
    logger = configure_logging(settings.log_level)
    principal = Principal(display_name="Local development principal")
    session = Session(principal_id=principal.principal_id, interface="smoke")
    registry = InMemoryCapabilityRegistry()
    registry.register(
        Capability(
            capability_id="development.echo",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            output_schema={"type": "object"},
            adapter_ref="development.fake",
        )
    )
    proposal = ActionProposal(
        session_id=session.session_id,
        capability_id=registry.get("development.echo").capability_id,
        arguments={"text": "offline foundation check"},
        correlation_id=uuid4(),
        origin="rule",
        source_ref="development.smoke",
    )
    if decode_contract(proposal.model_dump_json()) != proposal:
        raise RuntimeError("contract round trip failed")
    logger.info("foundation.smoke.passed", extra={"correlation_id": proposal.correlation_id})
    return {
        "status": "ok",
        "schema_version": proposal.schema_version,
        "capabilities": [item.capability_id for item in registry.discover()],
        "cloud_ai_enabled": settings.cloud_ai_enabled,
        "execution_enabled": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="JARVIS development foundation")
    parser.add_argument("command", choices=["smoke"])
    parser.parse_args()
    print(json.dumps(smoke()))


if __name__ == "__main__":
    main()
