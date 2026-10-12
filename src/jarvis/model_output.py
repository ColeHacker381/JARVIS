"""Model-facing guidance and content-free diagnostics; never authorization or repair."""

import json
import re
from copy import deepcopy

from pydantic import ValidationError

from jarvis.core.cognitive_records import CognitiveDecision
from jarvis.core.contracts import ActionProposal

KINDS = frozenset(CognitiveDecision.model_fields["kind"].annotation.__args__)
FIELDS = frozenset(CognitiveDecision.model_fields)
TYPES = frozenset({"dict", "list", "str", "int", "float", "bool", "NoneType"})
STAGES = frozenset(
    {
        "decision_json",
        "decision_schema",
        "decision_classification",
        "decision_version",
        "decision_envelope",
        "provider_json",
        "provider_envelope",
        "completion_limit",
        "completion_content",
        "memory_citation",
        "proposal_identity_binding",
        "proposal_origin",
        "proposal_task_id",
        "unverified_action_claim",
    }
)
VALIDATION_TYPES = frozenset(
    {
        "literal_error",
        "missing",
        "extra_forbidden",
        "string_type",
        "string_too_long",
        "string_too_short",
        "string_pattern_mismatch",
        "uuid_parsing",
        "uuid_type",
        "int_type",
        "dict_type",
        "tuple_type",
        "list_type",
        "value_error",
        "model_type",
        "bool_type",
        "datetime_parsing",
        "datetime_type",
        "greater_than_equal",
        "too_long",
        "too_short",
        "json_invalid",
        "union_tag_invalid",
        "union_tag_not_found",
    }
)
CONSISTENCY_RULES = {
    "only action decisions carry one ActionProposal": "action_kind_binding",
    "only failures carry an error": "failure_error_binding",
    "grounded responses require memory references": "memory_references_required",
    "world replies require evidence references": "world_references_required",
    "world IDs require world-grounded decision": "world_kind_binding",
    "use memory_grounded for memory references": "memory_kind_binding",
}


def safe_diagnostic(value):
    """Keep logging/API diagnostics allowlisted even if a caller supplies extra metadata."""
    if not isinstance(value, dict):
        return {}
    stage = value.get("stage")
    result = {"stage": stage if isinstance(stage, str) and stage in STAGES else "unclassified"}
    for key in ("output_bytes", "unknown_field_count"):
        if type(value.get(key)) is int and 0 <= value[key] <= 65536:
            result[key] = value[key]
    if isinstance(value.get("root_type"), str) and value["root_type"] in TYPES:
        result["root_type"] = value["root_type"]
    if isinstance(value.get("fields"), dict):
        result["fields"] = {
            key: value["fields"][key]
            for key in sorted(FIELDS & value["fields"].keys())
            if isinstance(value["fields"][key], str) and value["fields"][key] in TYPES
        }
    if isinstance(value.get("kind"), str) and value["kind"] in KINDS:
        result["kind"] = value["kind"]
    if isinstance(value.get("validation_types"), list):
        result["validation_types"] = sorted(
            {
                code if isinstance(code, str) and code in VALIDATION_TYPES else "validation_error"
                for code in value["validation_types"][:8]
            }
        )
    rule = value.get("validation_rule")
    if isinstance(rule, str) and rule in CONSISTENCY_RULES.values():
        result["validation_rule"] = rule
    if isinstance(value.get("reference_counts"), dict):
        result["reference_counts"] = {
            key: count
            for key, count in value["reference_counts"].items()
            if key in {"memory_ids", "world_ids"} and type(count) is int and 0 <= count <= 65536
        }
    return result


def output_shape(serialized, *, stage, errors=()):
    """No field values, unknown field names, input, validator messages or error context."""
    result = {"stage": stage, "output_bytes": len(serialized.encode())}
    try:
        data = json.loads(serialized)
    except (ValueError, TypeError):
        return result
    result["root_type"] = type(data).__name__
    if isinstance(data, dict):
        result["fields"] = {key: type(data[key]).__name__ for key in sorted(FIELDS & data.keys())}
        result["unknown_field_count"] = len(data.keys() - FIELDS)
        if isinstance(data.get("kind"), str) and data["kind"] in KINDS:
            result["kind"] = data["kind"]
        result["reference_counts"] = {
            key: len(data[key])
            for key in ("memory_ids", "world_ids")
            if isinstance(data.get(key), list)
        }
    if errors:
        # Pydantic's built-in type codes, never locations (which can contain private keys).
        result["validation_types"] = sorted({e["type"] for e in errors})[:8]
        for error in errors:
            rule = CONSISTENCY_RULES.get(error.get("msg", "").removeprefix("Value error, "))
            if rule:
                result["validation_rule"] = rule
                break
    return result


def validation_stage(exc):
    if isinstance(exc, json.JSONDecodeError):
        return "decision_json"
    if isinstance(exc, ValidationError):
        # Only our known static consistency-validator messages classify this stage.
        for error in exc.errors(include_input=False, include_context=True):
            context = error.get("ctx", {}).get("error")
            if isinstance(context, ValueError) and str(context) in CONSISTENCY_RULES:
                return "decision_classification"
        return "decision_schema"
    if str(exc) in {"explicit cognitive version required", "unsupported cognitive version"}:
        return "decision_version"
    return "decision_envelope"


def decision_schema(request, provider_id):
    """A restricted projection of existing v1 contracts, not a second decision protocol.

    Optional kernel-generated proposal metadata need not be generated by the model.
    The full original decoder and contextual/policy validation still run afterwards.
    """
    schema = CognitiveDecision.model_json_schema()
    if not request.memories:
        # No valid memory-grounded decision can cite an empty supplied reference set.
        # This narrows generation guidance; the original decoder still rejects it.
        schema["properties"]["kind"]["enum"].remove("memory_grounded")
        schema["properties"]["memory_ids"]["maxItems"] = 0
    action = schema["$defs"]["ActionProposal"]
    for name in ("proposal_id", "created_at", "task_id"):
        action["properties"].pop(name)
    action["properties"]["origin"] = {"const": "model", "type": "string"}
    action["properties"]["session_id"]["const"] = str(request.session_id)
    action["properties"]["correlation_id"]["const"] = str(request.correlation_id)
    action["properties"]["source_ref"]["const"] = provider_id
    for envelope in (schema, *schema.get("$defs", {}).values()):
        properties = envelope.get("properties", {})
        explicit = [name for name in ("schema_version", "contract_type") if name in properties]
        envelope["required"] = list(dict.fromkeys((*envelope.get("required", []), *explicit)))
    return schema


def action_example(request, provider_id):
    """Illustrate the existing envelope using authorized metadata, never submit it.

    Name matching only selects a prompt example. It grants no authority, supplies no
    executable arguments, and cannot bypass the registry/executor's validation.
    """
    if not request.capabilities:
        return ""
    words = re.findall(r"[\w]+", request.messages[-1].content.casefold())

    def relevance(capability):
        names = re.findall(r"[a-z]+", capability.capability_id.casefold())
        return sum(any(w == n or (len(w) >= 4 and w[:4] == n[:4]) for w in words) for n in names)

    capability = max(request.capabilities, key=relevance)
    example = CognitiveDecision(
        kind="action",
        text="Proposal only; not executed.",
        action=ActionProposal(
            session_id=request.session_id,
            correlation_id=request.correlation_id,
            origin="model",
            source_ref=provider_id,
            capability_id=capability.capability_id,
            arguments={},
        ),
    ).model_dump(mode="json", exclude_none=True)
    for field in ("proposal_id", "task_id", "created_at"):
        example["action"].pop(field, None)
    for field in ("memory_ids", "world_ids"):
        example.pop(field)
    return (
        " For a request to perform an available capability, return kind=action "
        "with ActionProposal, "
        "or clarification/unsupported; a direct reply does not perform it. "
        "Never say it has completed. Minimal action envelope example: "
        + json.dumps(example, separators=(",", ":"))
        + " Fill arguments from the user's actual request according to that capability's "
        "input_schema; the empty example arguments are placeholders. "
        "Select the capability matching the request, not merely the example."
    )


def structured_decision_schema(schema):
    """Express the existing consistency validator for grammar-capable local servers.

    Pydantic's JSON Schema does not export model_validator conditions. In particular,
    kind=failure without ErrorDetail is legal in that schema but invalid in the contract.
    Each branch retains the original fields/types/versions and narrows only that shape.
    """
    variants = []
    for kind in sorted(schema["properties"]["kind"]["enum"]):
        branch = deepcopy({key: value for key, value in schema.items() if key != "$defs"})
        properties = branch["properties"]
        properties["kind"] = {"const": kind, "type": "string"}
        for field, active, definition in (
            ("action", kind == "action", "ActionProposal"),
            ("error", kind == "failure", "ErrorDetail"),
        ):
            properties[field] = {"$ref": f"#/$defs/{definition}"} if active else {"type": "null"}
            if active:
                branch["required"].append(field)
        for field, grounded in (("memory_ids", "memory_grounded"), ("world_ids", "world_grounded")):
            if kind == grounded:
                properties[field]["minItems"] = 1
                branch["required"].append(field)
            elif field == "world_ids" or kind == "direct":
                properties[field]["maxItems"] = 0
        variants.append(branch)
    return {"$defs": schema["$defs"], "oneOf": variants}
