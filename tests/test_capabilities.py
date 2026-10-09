import pytest
from jsonschema import SchemaError
from pydantic import ValidationError

from jarvis.capabilities import InMemoryCapabilityRegistry
from jarvis.core.contracts import Capability


def capability(name="test.echo", **overrides):
    return Capability(
        **{
            "capability_id": name,
            "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
            "output_schema": {"type": "object"},
            "adapter_ref": "test.fake",
            **overrides,
        }
    )


def test_registration_and_discovery_without_model_or_execution():
    registry = InMemoryCapabilityRegistry()
    registry.register(capability())
    registry.register(capability("test.clock"))
    assert [item.capability_id for item in registry.discover()] == ["test.clock", "test.echo"]
    assert registry.get("test.echo").input_schema["type"] == "object"
    assert not hasattr(registry, "execute")
    with pytest.raises(KeyError):
        registry.get("missing")
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(capability())


@pytest.mark.parametrize("field", ["input_schema", "output_schema"])
def test_malformed_schema_never_registered(field):
    registry = InMemoryCapabilityRegistry()
    with pytest.raises(SchemaError):
        registry.register(capability(**{field: {"type": "not_a_json_schema_type"}}))
    assert registry.discover() == ()


def test_registry_owns_metadata_copies():
    registry = InMemoryCapabilityRegistry()
    item = capability()
    registry.register(item)
    item.input_schema["type"] = "string"
    retrieved = registry.get(item.capability_id)
    assert retrieved.input_schema["type"] == "object"
    retrieved.input_schema["properties"]["text"]["type"] = "integer"
    assert registry.get(item.capability_id).input_schema["properties"]["text"]["type"] == "string"


@pytest.mark.parametrize("effect", ["local", "external"])
def test_side_effect_metadata_requires_permissions(effect):
    with pytest.raises(ValidationError):
        capability(side_effect=effect)
    registered = capability(side_effect=effect, required_permissions=("test.echo.invoke",))
    assert registered.required_permissions == ("test.echo.invoke",)
