"""Conservative request separation and reporting, never execution or authorization.

English lighting clauses cover the current conversational simulated adapter.
Recognizing a clause can defer work or suppress prose; it cannot create a proposal,
infer permissions, or establish that a handler ran.
"""

import re

from jarvis.core.cognitive_records import CognitiveDecision, ConversationTurn

SEPARATE_REQUESTS = (
    "This contains multiple requests. No action was submitted or executed for this request. "
    "Please send the question and each action separately."
)
NO_ACTION = (
    "No action was submitted or executed for this request. "
    "The assistant did not propose an executable action."
)
_CLAUSES = re.compile(r"[.!?;\n]+|\b(?:also|and then|and|then)\b", re.I)
_LIGHTING = re.compile(
    r"^(?:jarvis[, :]+)?(?:please\s+)?"
    r"(?:(?:can|could|would|will) you\s+(?:please\s+)?|i want you to\s+)?"
    r"(?:set|dim|turn|switch|brighten|adjust|change|lower|raise|increase|decrease|reduce)\b"
    r".{0,200}\b(?:lights?|lighting|brightness)\b",
    re.I,
)
_QUESTION = re.compile(
    r"^(?:jarvis[, :]+)?(?:please\s+)?"
    r"(?:how|what|why|when|where|who|explain|describe|tell|compare|suggest|recommend|brainstorm)\b",
    re.I,
)
_CLAIM = re.compile(
    r"\b(?:lights?|lighting|brightness)\b.{0,100}\b"
    r"(?:were|was|are|is|have been|has been|now)\b.{0,30}\b"
    r"(?:set|dimmed|adjusted|changed|turned|switched|lowered|raised)\b|"
    r"\b(?:i|we)\b.{0,30}\b(?:set|dimmed|adjusted|changed|turned|switched|lowered|raised)\b"
    r".{0,100}\b(?:lights?|lighting|brightness)\b",
    re.I,
)


def request_shape(text: str) -> tuple[bool, bool]:
    clauses = [part.strip(" ,:\t") for part in _CLAUSES.split(text) if part.strip(" ,:\t")]
    commands = sum(bool(_LIGHTING.search(part)) for part in clauses)
    questions = any(_QUESTION.search(part) and not _LIGHTING.search(part) for part in clauses)
    return bool(commands), bool(commands > 1 or (commands and questions))


def no_action_decision(decision: CognitiveDecision, text: str) -> CognitiveDecision:
    requested, multiple = request_shape(text)
    if decision.action is not None or (not requested and not _CLAIM.search(decision.text)):
        return decision
    return CognitiveDecision(
        kind=decision.kind if decision.kind in {"unsupported", "failure"} else "clarification",
        text=SEPARATE_REQUESTS if multiple else NO_ACTION,
        error=decision.error,
    )


def reported_turn(turn: ConversationTurn) -> ConversationTurn:
    """Protect display/replay/history of older prose without rewriting stored evidence.

    An accepted action whose task link is missing may already have effects. Leave it
    untouched; only a turn with no accepted action and no task can assert no submission.
    """
    if turn.response is None or turn.task_id is not None:
        return turn
    if turn.decision is not None and turn.decision.action is not None:
        return turn
    if not request_shape(turn.user_text)[0] and not _CLAIM.search(turn.response.text):
        return turn
    original = turn.decision
    if turn.response.kind == "failure" or original is None:
        original = CognitiveDecision(
            kind="failure", text=turn.response.text, error=turn.response.error
        )
    decision = no_action_decision(original, turn.user_text)
    response = turn.response.model_copy(
        update={
            "kind": decision.kind,
            "text": decision.text,
            "memory_ids": (),
            "world_refs": (),
            "initiative_refs": (),
            "notification_refs": (),
        }
    )
    return turn.model_copy(
        update={"response": response, "decision": decision if turn.decision is not None else None}
    )
