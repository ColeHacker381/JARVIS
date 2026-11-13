"""Bounded source segmentation and consent checks, never permission or execution."""

import re

from jarvis.core.cognitive_records import IntentInput

_PREFIX = (
    r"(?:jarvis[, :]+)?(?:please\s+)?"
    r"(?:(?:can|could|would|will) you\s+(?:also\s+)?(?:please\s+)?|i want you to\s+)?"
)
_VERBS = (
    r"set|dim|turn|switch|brighten|adjust|change|lower|raise|increase|decrease|reduce|"
    r"echo|repeat|send|email|call|delete|open|play|approve|grant|run"
)
_START = (
    r"(?:how|what|why|when|where|who|explain|describe|tell|compare|suggest|recommend|"
    r"brainstorm|remember|recall|" + _VERBS + r")\b"
)
_LEADING = re.compile(r"^" + _PREFIX + _START, re.I)
_SPLIT = re.compile(
    r"[!?;]+|\.\s+(?=" + _PREFIX + _START + r")|"
    r"\s+(?:and\s+then|(?<!you )also|then|and)\s*,?\s*(?=" + _PREFIX + _START + r")",
    re.I,
)
_ACTION = re.compile(
    r"^" + _PREFIX + r"(?:" + _VERBS + r")\b",
    re.I,
)
_LIGHT = re.compile(
    r"^"
    + _PREFIX
    + r"(?:set|dim|turn|switch|brighten|adjust|change|lower|raise|increase|decrease|reduce)"
    r"\b.{0,200}\b(?:lights?|lighting|brightness)\b",
    re.I,
)
_ECHO = re.compile(r"^" + _PREFIX + r"(?:echo|repeat)\s+(.+)$", re.I)


def split_intents(text):
    """Only explicit boundaries, never guessed subgoals; cap before model invocation."""
    parts = []
    for part in _SPLIT.split(text):
        part = re.sub(r"^(?:also|then|and then|and)[,\s]+", "", part.strip(), flags=re.I).strip()
        if part.strip(" .,"):
            parts.append(part.strip(" .,"))
    if len(parts) < 2 or not all(_LEADING.match(p) for p in parts):
        return ()
    if len(parts) > 4:
        raise ValueError("intent_limit_exceeded")
    return tuple(
        IntentInput(intent_index=i, text=p, action_requested=requested_action(p))
        for i, p in enumerate(parts)
    )


def requested_action(text):
    return bool(_ACTION.match(text))


def execution_claim(text):
    """Conservative claim rejection for model reasoning, not universal prose verification."""
    verbs = (
        r"set|dimmed|changed|sent|deleted|emailed|called|executed|switched|turned|"
        r"opened|played|approved|granted"
    )
    subjects = (
        r"lights?|brightness|messages?|emails?|files?|permissions?|privileges?|tasks?|commands?|"
        r"devices?|doors?|notifications?"
    )
    return bool(
        re.search(
            r"\b(?:i|we)(?:'ve)?\b.{0,30}\b(?:" + verbs + r")\b.{0,100}\b(?:" + subjects + r")\b|"
            r"\b(?:"
            + subjects
            + r")\b.{0,60}\b(?:were|was|have been|has been|are now|is now)\b.{0,30}\b(?:"
            + verbs
            + r")\b|\b(?:done|successfully completed)\b.{0,60}\b(?:"
            + subjects
            + r")\b|\b(?:"
            + subjects
            + r")\b.{0,30}\b(?:now|already|successfully|done|completed)\b",
            text,
            re.I,
        )
    )


def echo_arguments(text):
    match = _ECHO.match(text)
    return {"text": match[1].strip("\"'")} if match else None


def action_matches_source(action, text):
    """Supported explicit adapters only; model cannot invent a requested effect/value.

    This adds a consent bound to multi-intent proposals. Current executor policy and
    input schema still decide permission and validity independently.
    """
    if action.capability_id == "home.lighting.set_brightness" and _LIGHT.match(text):
        if re.search(r"\bby\b", text, re.I):
            return False  # Relative changes require a separate state/consent design.
        numbers = re.findall(r"(?<![\w.])\d{1,4}(?![\w.])", text)
        if len(numbers) != 1:
            return False
        return action.arguments.get("brightness") == int(numbers[0])
    if action.capability_id == "development.echo":
        arguments = echo_arguments(text)
        return bool(arguments and action.arguments.get("text") == arguments["text"])
    return False


def interaction_inventory(capabilities):
    chat = []
    for capability in capabilities:
        simulated = capability.adapter_ref.startswith("development.")
        chat.append(
            {
                "id": capability.capability_id,
                "simulated": simulated,
                "side_effect": capability.side_effect,
                "multi_intent_supported": capability.capability_id
                in {"home.lighting.set_brightness", "development.echo"},
            }
        )
    return {
        "internal_subsystems": [
            "identity",
            "authorization",
            "tasks",
            "memory",
            "world",
            "initiatives",
            "notifications",
            "audio",
        ],
        "chat_capabilities": chat,
        "unavailable_external_integrations": [
            "physical lighting",
            "external messaging",
            "arbitrary shell",
            "cloud AI",
        ],
        "limits": "Internal subsystems are not conversational permission grants. "
        "Actions require an exposed adapter and current executor authorization.",
    }
