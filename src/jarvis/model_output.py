"""Model-facing guidance and content-free diagnostics; never authorization or repair."""

import json
import re
from copy import deepcopy

from pydantic import ValidationError

from jarvis.core.cognitive_records import CognitiveDecision, IntentPlan, PlannedIntent
from jarvis.core.contracts import ActionProposal
from jarvis.intents import action_matches_source, echo_arguments, requested_action

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
        "intent_schema",
        "intent_action_missing",
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
    "intent indexes must be complete and ordered": "intent_index_order",
    "intent action purpose mismatch": "intent_purpose_binding",
    "unresolved intents require clarification purpose": "intent_purpose_binding",
    "plan input count mismatch": "intent_count_binding",
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


def decision_schema(request, provider_id, model=CognitiveDecision):
    """A restricted projection of existing v1 contracts, not a second decision protocol.

    Optional kernel-generated proposal metadata need not be generated by the model.
    The full original decoder and contextual/policy validation still run afterwards.
    """
    schema = model.model_json_schema()
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


def plan_schema(request, provider_id, *, structured=False):
    schema = IntentPlan.model_json_schema()
    decision = decision_schema(request, provider_id, PlannedIntent)
    decision["properties"]["intent_index"]["enum"] = list(range(len(request.intent_inputs)))
    schema["required"].extend(["schema_version", "contract_type"])
    schema["properties"]["intents"]["minItems"] = len(request.intent_inputs)
    schema["properties"]["intents"]["maxItems"] = len(request.intent_inputs)
    if structured:
        converted = structured_decision_schema(decision)
        for branch in converted["oneOf"]:
            kind = branch["properties"]["kind"]["const"]
            branch["properties"]["purpose"] = (
                {"const": "action"}
                if kind == "action"
                else {"const": "clarification"}
                if kind in {"clarification", "unsupported", "failure"}
                else {"enum": ["conversation", "memory", "reasoning"]}
            )
        schema["$defs"] = {**converted["$defs"], "PlannedIntent": {"oneOf": converted["oneOf"]}}
        # Generation guidance follows explicit sources, never grants. Qwen repeatedly
        # selected unsupported for clear requested actions with the unrestricted union.
        # The full decoder, source checks and current executor authorization still run.
        positional = []
        for source in request.intent_inputs:
            source_variants = []
            proposal = _source_example(request, source, provider_id)
            named = re.findall(r"\b[A-Z][\w-]{3,}\b", source.text)
            relevant = any(
                re.search(r"\b" + re.escape(word) + r"\b", json.dumps(m.content), re.I)
                for word in named
                if word.casefold() not in {"what", "could", "explain", "also", "jarvis"}
                for m in request.memories
            )
            kinds = (
                {"action"}
                if proposal
                else (
                    {"unsupported", "clarification", "failure"}
                    if source.action_requested
                    else {"memory_grounded", "clarification", "unsupported", "failure"}
                    if relevant
                    else {
                        "direct",
                        "memory_grounded",
                        "world_grounded",
                        "clarification",
                        "unsupported",
                        "failure",
                    }
                )
            )
            for branch in converted["oneOf"]:
                if branch["properties"]["kind"]["const"] not in kinds:
                    continue
                variant = deepcopy(branch)
                variant["properties"]["intent_index"] = {
                    "type": "integer",
                    "const": source.intent_index,
                }
                if proposal:
                    name = f"SourceAction{source.intent_index}"
                    action = deepcopy(converted["$defs"]["ActionProposal"])
                    action["properties"]["capability_id"]["const"] = proposal.capability_id
                    action["properties"]["arguments"]["const"] = proposal.arguments
                    schema["$defs"][name] = action
                    variant["properties"]["action"] = {"$ref": f"#/$defs/{name}"}
                source_variants.append(variant)
            positional.append({"oneOf": source_variants})
        schema["properties"]["intents"]["prefixItems"] = positional
        # Exact min/max length excludes trailing items. Some local grammar compilers
        # require an object here even when prefixItems covers the entire array.
        schema["properties"]["intents"]["items"] = {}
    else:
        schema["$defs"] = {
            **decision["$defs"],
            "PlannedIntent": {k: v for k, v in decision.items() if k != "$defs"},
        }
    return schema


def plan_wire_schema(request, provider_id):
    """Local adapter envelope: required named slots avoid positional grammar ambiguity.

    Normalization only orders existing slots; it cannot invent decisions or metadata.
    The provider-neutral core still consumes the unchanged IntentPlan v1 array.
    """
    schema = plan_schema(request, provider_id, structured=True)
    positions = schema["properties"]["intents"]["prefixItems"]
    schema["properties"]["contract_type"]["const"] = "local_intent_slots"
    schema["properties"]["intents"] = {
        "type": "object",
        "properties": {str(i): item for i, item in enumerate(positions)},
        "required": [str(i) for i in range(len(positions))],
        "additionalProperties": False,
    }
    for item in positions:
        for branch in item["oneOf"]:
            if branch["properties"]["kind"]["const"] == "memory_grounded":
                branch["properties"]["memory_ids"]["items"]["enum"] = [
                    str(m.memory_id) for m in request.memories
                ]
    return schema


def _source_example(request, source, provider_id):
    """Illustrate only a supported explicitly requested value, never submit a task."""
    if not source.action_requested:
        return None
    numbers = re.findall(r"(?<![\w.])\d{1,4}(?![\w.])", source.text)
    args = echo_arguments(source.text)
    capability = "development.echo" if args is not None else "home.lighting.set_brightness"
    if args is None:
        args = {"brightness": int(numbers[0])} if len(numbers) == 1 else {}
    action = ActionProposal(
        session_id=request.session_id,
        correlation_id=request.correlation_id,
        origin="model",
        source_ref=provider_id,
        capability_id=capability,
        arguments=args,
    )
    available = {c.capability_id for c in request.capabilities}
    return (
        action if capability in available and action_matches_source(action, source.text) else None
    )


def plan_instructions(request, provider_id, schema=None, *, slots=False):
    text = (
        "Return exactly one IntentPlan v1 JSON object, no markdown or reasoning trace. "
        "One planned_intent per supplied intent_input, in intent_index order, maximum four. "
        "Each planned_intent has schema_version=1, contract_type=planned_intent, intent_index, "
        "purpose (conversation/memory/reasoning/action/clarification), kind and text. "
        "Keep text under 1200 characters. Questions get useful direct or memory_grounded answers; "
        "combine authorized recalled claims with general knowledge and label assumptions/options. "
        "Missing project selections do not prevent conditional engineering recommendations. "
        "For codec recommendations name suitable codecs, practical uses and tradeoffs. "
        "Do not merely copy example reasoning; answer each actual input. "
        "For memory_grounded cite actual supplied memory_ids. Without snippets use direct. "
        "Actions require purpose=action, kind=action and one existing ActionProposal v1. "
        "Use the supplied session_id/correlation_id, origin=model, source_ref=" + provider_id + ". "
        "Omit task_id, proposal_id, created_at and any identity/approval fields. "
        "Propose exactly the effect and parameters explicitly requested in that input, never "
        "subgoals or actions for question inputs. Unsupported requests get purpose=clarification "
        "and kind=unsupported; unclear parameters get kind=clarification. "
        "Never claim execution in model text. Trusted task results are composed later. "
        "Inputs with action_requested=true are requests to perform an operation, not questions "
        "about whether it is possible. Propose an exposed supported operation for those inputs. "
        "The simulated lighting adapter executes local brightness state changes; simulated means "
        "no hardware, not unavailable. Propose exposed lighting with the requested value. "
        "Adapt answers to actual inputs. Internal subsystems are not tools or permission grants. "
        "Only available_capabilities describe chat tools; simulated adapters are not real devices."
    )
    if schema is not None:
        text += " Schema: " + json.dumps(schema, separators=(",", ":"))
    if slots:
        text = text.replace("IntentPlan v1", "LocalIntentSlots v1")
        text += (
            " The adapter envelope uses contract_type=local_intent_slots and intents is an "
            "object with required keys 0, 1 (and 2, 3 if supplied), NOT an array. "
            "Each key contains its corresponding planned_intent; preserve intent_index."
        )
    examples = []
    available = {c.capability_id for c in request.capabilities}
    for source in request.intent_inputs:
        item = {
            "schema_version": 1,
            "contract_type": "planned_intent",
            "intent_index": source.intent_index,
            "purpose": "conversation",
            "kind": "direct",
            "text": "Answer this input using general knowledge.",
        }
        if requested_action(source.text):
            numbers = re.findall(r"(?<![\w.])\d{1,4}(?![\w.])", source.text)
            args = echo_arguments(source.text)
            capability = "development.echo" if args is not None else "home.lighting.set_brightness"
            if args is None:
                args = {"brightness": int(numbers[0])} if len(numbers) == 1 else {}
            action = ActionProposal(
                session_id=request.session_id,
                correlation_id=request.correlation_id,
                origin="model",
                source_ref=provider_id,
                capability_id=capability,
                arguments=args,
            )
            if capability in available and action_matches_source(action, source.text):
                item.update(
                    purpose="action",
                    kind="action",
                    text="Proposed only; not executed.",
                    action=action.model_dump(
                        mode="json", exclude={"proposal_id", "created_at", "task_id"}
                    ),
                )
            else:
                item.update(
                    purpose="clarification",
                    kind="unsupported",
                    text="No supported proposal for this input.",
                )
        elif request.memories:
            item.update(
                purpose="reasoning",
                kind="memory_grounded",
                memory_ids=[str(request.memories[0].memory_id)],
                text="Stored claims are provisional. Assuming audio is wanted, Opus suits "
                "low-latency intercom, FLAC lossless diagnostics, and AAC compact recordings. "
                "These are options, not established project features.",
            )
        examples.append(item)
    text += " Exact envelope example using supplied metadata (adapt reasoning, never treat example "
    text += "text as project evidence or execution): " + json.dumps(
        {
            "schema_version": 1,
            "contract_type": "local_intent_slots" if slots else "intent_plan",
            "intents": {str(i): item for i, item in enumerate(examples)} if slots else examples,
        },
        separators=(",", ":"),
    )
    return text


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
