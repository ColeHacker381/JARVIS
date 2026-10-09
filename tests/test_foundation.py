import io
import json
import logging
import subprocess
import sys

import pytest
from pydantic import ValidationError

from jarvis.cognition import ProposalSource
from jarvis.config import Settings
from jarvis.observability import configure_logging
from jarvis.world_state import ObservationSource


def test_fake_adapters_can_supply_only_contracts(contracts):
    session = next(item for item in contracts if item.contract_type == "session")
    proposal = next(item for item in contracts if item.contract_type == "action_proposal")
    observation = next(item for item in contracts if item.contract_type == "observation")

    class FakeModel:
        def propose(self, session, request):
            return (proposal,)

    class FakeSensor:
        def observe(self, session):
            return (observation,)

    provider: ProposalSource = FakeModel()
    sensor: ObservationSource = FakeSensor()
    assert provider.propose(session, "echo")[0].origin == "model"
    assert sensor.observe(session)[0].source.source_ref == "fake.sensor"
    assert not hasattr(provider, "authorize")
    assert not hasattr(provider, "execute")


def test_environment_configuration_is_local_and_validated(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    monkeypatch.delenv("JARVIS_LOG_LEVEL", raising=False)
    assert Settings.from_environment().cloud_ai_enabled is False
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_LOG_LEVEL", "DEBUG")
    assert Settings.from_environment().data_dir == tmp_path
    assert Settings.from_environment().log_level == "DEBUG"
    monkeypatch.setenv("JARVIS_LOG_LEVEL", "INVALID")
    with pytest.raises(ValidationError):
        Settings.from_environment()
    monkeypatch.setenv("JARVIS_LOG_LEVEL", "INFO")
    monkeypatch.setenv("JARVIS_DATA_DIR", "  ")
    with pytest.raises(ValidationError):
        Settings.from_environment()
    with pytest.raises(ValidationError):
        Settings(cloud_ai_enabled=True)


def test_json_logging_preserves_correlations_without_payloads(contracts):
    stream = io.StringIO()
    logger = configure_logging(stream=stream)
    result = next(item for item in contracts if item.contract_type == "execution_result")
    logger.error(
        "capability.denied",
        extra={
            "correlation_id": result.correlation_id,
            "proposal_id": result.proposal_id,
            "reason_code": result.error.code,
            "arguments": {"secret": "never-log-this"},
        },
    )
    data = json.loads(stream.getvalue())
    assert data["correlation_id"] == str(result.correlation_id)
    assert data["proposal_id"] == str(result.proposal_id)
    assert data["reason_code"] == "not_granted"
    assert data["level"] == "ERROR"
    assert "never-log-this" not in stream.getvalue()
    stream.seek(0)
    stream.truncate()
    configure_logging(stream=stream).info("second_setup")
    assert len(stream.getvalue().splitlines()) == 1


def test_exception_logging_omits_sensitive_exception_messages():
    stream = io.StringIO()
    logger = configure_logging(stream=stream)
    try:
        raise ValueError("sensitive-input")
    except ValueError:
        logger.exception("adapter.failure", extra={"reason_code": "invalid_input"})
    data = json.loads(stream.getvalue())
    assert data["exception_type"] == "ValueError"
    assert "sensitive-input" not in stream.getvalue()
    logging.getLogger("jarvis").handlers.clear()


def test_installed_module_smoke_without_credentials_or_services(monkeypatch):
    monkeypatch.delenv("JARVIS_LOG_LEVEL", raising=False)
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    process = subprocess.run(
        [sys.executable, "-m", "jarvis", "smoke"], capture_output=True, text=True, check=True
    )
    result = json.loads(process.stdout)
    assert result == {
        "status": "ok",
        "schema_version": 1,
        "capabilities": ["development.echo"],
        "cloud_ai_enabled": False,
        "execution_enabled": False,
    }
    assert json.loads(process.stderr)["event"] == "foundation.smoke.passed"
