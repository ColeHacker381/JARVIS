"""Bounded cognitive coordination; authority and invocation stay in existing kernel services."""

import asyncio
import json
import logging
import re
import time
from typing import Protocol
from uuid import uuid4, uuid5

from pydantic import ValidationError

from jarvis.action_reporting import (
    NO_ACTION,
    no_action_decision,
    reported_turn,
)
from jarvis.conversations import ConversationError, contains_secret
from jarvis.core.cognitive_records import (
    CognitiveDecision,
    CognitiveResponse,
    ComposedResponse,
    IntentOutcome,
    IntentPlan,
    ModelMessage,
    ModelProvenance,
    PlannedIntent,
    decode_cognitive,
)
from jarvis.core.contracts import ActionProposal, AuditRecord, ErrorDetail, Session
from jarvis.core.task_records import TaskStep
from jarvis.execution import validate_capability_arguments
from jarvis.identity import IdentityError
from jarvis.initiative import InitiativeError, describe_initiatives, is_initiative_question
from jarvis.intents import action_matches_source, execution_claim, requested_action
from jarvis.memory import MemoryError
from jarvis.model_output import output_shape, validation_stage
from jarvis.models import ModelError
from jarvis.notifications import NotificationError, describe_notifications, is_notification_question
from jarvis.observability import AuditWriteError, write_audit
from jarvis.storage.errors import StorageError
from jarvis.tasks import TaskConflictError
from jarvis.world_state import WorldError, describe_world, is_world_question


class ProposalSource(Protocol):
    def propose(self, session: Session, request: str) -> tuple[ActionProposal, ...]: ...


class CognitiveCoordinator:
    def history(self, conversation_id, *, session_token, limit=20):
        """User-facing history rechecks all private context dependencies before reuse."""
        records = self.conversations.recent(
            conversation_id, session_token=session_token, limit=limit
        )
        visible = []
        for turn in records:
            try:
                self.context.ensure_current(
                    turn.memory_ids,
                    session_token=session_token,
                    world_refs=turn.world_refs,
                    initiative_refs=turn.initiative_refs,
                    notification_refs=turn.notification_refs,
                )
            except (MemoryError, WorldError, InitiativeError, NotificationError):
                continue
            except ConversationError as exc:
                if exc.code != "context_authorization_changed":
                    raise
                continue
            visible.append(turn)
        return tuple(visible)

    def __init__(self, *, conversations, context, models, tasks, audit, defer_actions=False):
        if tasks.identities is not conversations.identities or (
            context.identities is not conversations.identities
        ):
            raise ValueError("coordinator must share trusted identity state")
        self.conversations, self.context, self.models = conversations, context, models
        self.tasks, self.audit = tasks, audit
        # Trusted scheduling choice; a model cannot mark an operation completed/deferred.
        self.defer_actions = defer_actions

    def _refs(self, correlation_id):
        return tuple(a.audit_id for a in self.audit.records(correlation_id=correlation_id))

    @staticmethod
    def _response(turn, *, kind, text, **kwargs):
        return CognitiveResponse(
            conversation_id=turn.conversation_id,
            request_id=turn.request_id,
            correlation_id=turn.correlation_id,
            kind=kind,
            text=text,
            provenance=turn.provenance,
            **kwargs,
        )

    def _validate(self, serialized, request):
        try:
            decision = decode_cognitive(serialized)
            if not isinstance(decision, CognitiveDecision):
                raise ValueError()
        except (ValueError, TypeError, AttributeError) as exc:
            errors = (
                exc.errors(include_input=False, include_context=False)
                if isinstance(exc, ValidationError)
                else ()
            )
            raise ModelError(
                "model_output_invalid",
                diagnostic=output_shape(serialized, stage=validation_stage(exc), errors=errors),
            ) from None
        if contains_secret(serialized):
            raise ModelError("sensitive_output_rejected")
        if (
            decision.action is None
            and re.search(
                r"\b(set|turn|switch|dim|brighten)\b.*\b(lights?|lighting|brightness)\b",
                request.messages[-1].content.casefold(),
            )
            and (
                re.search(
                    r"\b(lights?|lighting|brightness)\b.*\b(?:have been|has been|are now|is now) "
                    r"(?:set|turned|switched|at|dimmed|changed)\b",
                    decision.text.casefold(),
                )
                or (
                    decision.kind == "direct"
                    and re.search(
                        r"\b(setting|turning|switching|dimming|changing)\b.*"
                        r"\b(lights?|lighting|brightness)\b",
                        decision.text.casefold(),
                    )
                )
            )
        ):
            # Output-integrity guard for the reproduced false lighting completion.
            # This never establishes intent/permission or submits an action. Free-text
            # factual correctness in general still cannot be proven by a regex.
            raise ModelError(
                "unverified_action_claim",
                diagnostic=output_shape(serialized, stage="unverified_action_claim"),
            )
        available_memories = {m.memory_id for m in request.memories}
        if not set(decision.memory_ids) <= available_memories:
            raise ModelError("memory_reference_invalid", diagnostic={"stage": "memory_citation"})
        if not set(decision.world_ids) <= {s.state_id for s in request.world_states}:
            raise ModelError("world_reference_invalid")
        if self.context.initiatives is not None and is_initiative_question(
            request.messages[-1].content
        ):
            # Inspection never imports approval or executes a conversationally approved action.
            decision = CognitiveDecision(
                kind="direct", text=describe_initiatives(request.initiatives)
            )
        if (
            self.context.notifications is not None
            and is_notification_question(request.messages[-1].content)
            and (
                request.notifications
                or "waiting for my approval" not in request.messages[-1].content.casefold()
            )
        ):
            decision = CognitiveDecision(
                kind="direct", text=describe_notifications(request.notifications)
            )
        # Render environmental replies from verified state, never arbitrary model claims.
        if (
            request.world_states
            and decision.action is None
            and decision.kind != "failure"
            and not is_notification_question(request.messages[-1].content)
            and not is_initiative_question(request.messages[-1].content)
        ):
            decision = CognitiveDecision(
                kind="world_grounded",
                text=describe_world(request.world_states),
                world_ids=tuple(s.state_id for s in request.world_states),
            )
        elif (
            self.context.world is not None
            and not is_notification_question(request.messages[-1].content)
            and not is_initiative_question(request.messages[-1].content)
            and is_world_question(request.messages[-1].content)
            and decision.action is None
            and decision.kind != "failure"
        ):
            decision = CognitiveDecision(
                kind="clarification",
                text=(
                    "Authorized environmental evidence is unavailable; "
                    "the current state is unknown."
                ),
            )
        if decision.action:
            action = decision.action
            if (
                action.session_id != request.session_id
                or action.correlation_id != request.correlation_id
            ):
                raise ModelError(
                    "proposal_context_mismatch", diagnostic={"stage": "proposal_identity_binding"}
                )
            if action.origin != "model":
                raise ModelError(
                    "proposal_context_invalid", diagnostic={"stage": "proposal_origin"}
                )
            if action.task_id is not None:
                raise ModelError(
                    "proposal_context_invalid", diagnostic={"stage": "proposal_task_id"}
                )
            try:
                self.context.registry.get(action.capability_id)
            except KeyError:
                raise ModelError("capability_unknown") from None
            # Input validation/current policy are always performed by the executor;
            # even a known off-catalogue proposal cannot bypass it or invoke a handler.
        elif decision.kind != "failure" and any(
            memory.source_ref.startswith("development.") for memory in request.memories
        ):
            warning = (
                "Development fixture context is unverified and is not established project truth. "
            )
            # Deterministic provenance survives a model omitting its own fixture disclaimer.
            if len(warning + decision.text) <= 4096:
                decision = decision.model_copy(update={"text": warning + decision.text})
            else:
                raise ModelError("fixture_provenance_missing")
        # A conversational decision cannot stand in for submission. Suppress its
        # prose for recognized action requests, irrespective of wording or tense.
        return no_action_decision(decision, request.messages[-1].content)

    def _validate_plan(self, serialized, request):
        started = time.monotonic()
        try:
            try:
                plan = decode_cognitive(serialized)
                if not isinstance(plan, IntentPlan) or len(plan.intents) != len(
                    request.intent_inputs
                ):
                    raise ValueError("plan input count mismatch")
            except (ValueError, TypeError) as exc:
                errors = (
                    exc.errors(include_input=False, include_context=False)
                    if isinstance(exc, ValidationError)
                    else ()
                )
                raise ModelError(
                    "intent_decomposition_invalid",
                    diagnostic=output_shape(serialized, stage="intent_schema", errors=errors),
                ) from exc
            accepted = []
            for item, source in zip(plan.intents, request.intent_inputs, strict=True):
                scoped = request.model_copy(
                    update={
                        "intent_inputs": (),
                        "messages": (
                            *request.messages[:-1],
                            ModelMessage(role="user", content=source.text),
                        ),
                    }
                )
                try:
                    if item.action is None and execution_claim(item.text):
                        raise ModelError("unverified_action_claim")
                    decision = self._validate(item.decision().model_dump_json(), scoped)
                    if decision.action and not action_matches_source(decision.action, source.text):
                        raise ModelError("intent_action_not_requested")
                    if len(decision.text) > 1200:
                        raise ModelError("intent_answer_too_large")
                    if requested_action(source.text) and decision.action is None:
                        logging.getLogger("jarvis").warning(
                            "model.intent_unresolved",
                            extra={
                                "correlation_id": request.correlation_id,
                                "reason_code": "intent_action_missing",
                                "model_diagnostic": output_shape(
                                    item.model_dump_json(), stage="intent_action_missing"
                                ),
                            },
                        )
                        decision = CognitiveDecision(
                            kind="unsupported"
                            if decision.kind == "unsupported"
                            else "clarification",
                            text="No task was submitted for this requested operation; "
                            "it is unavailable or needs clarification.",
                        )
                    if decision.text == NO_ACTION and not decision.action:
                        decision = decision.model_copy(
                            update={
                                "text": "This part was not answered: no executable proposal "
                                "or verified action claim was accepted."
                            }
                        )
                except ModelError as exc:
                    if exc.code in {
                        "proposal_context_mismatch",
                        "proposal_context_invalid",
                        "sensitive_output_rejected",
                    }:
                        raise
                    decision = CognitiveDecision(
                        kind="failure",
                        text=f"Intent rejected: {exc.code}.",
                        error=ErrorDetail(code=exc.code, message=exc.code),
                    )
                purpose = (
                    "clarification"
                    if decision.kind in {"failure", "unsupported", "clarification"}
                    else item.purpose
                )
                accepted.append(
                    PlannedIntent(
                        **decision.model_dump(exclude={"contract_type"}),
                        intent_index=item.intent_index,
                        purpose=purpose,
                    )
                )
            return IntentPlan(intents=tuple(accepted))
        finally:
            self.context.metrics.record(
                "cognition.plan_validation_ms", (time.monotonic() - started) * 1000
            )

    async def respond(
        self, conversation_id, text, *, session_token, request_id=None, response_style="text"
    ):
        request_id = request_id or uuid4()
        turn = None
        try:
            with self.conversations.claim(conversation_id, session_token=session_token):
                turn, fresh = self.conversations.begin(
                    conversation_id, request_id, text, session_token=session_token
                )
                if turn.parent_request_id is not None:
                    raise ConversationError("request_conflict")
                if turn.response is not None:
                    self.context.ensure_current(
                        turn.memory_ids,
                        session_token=session_token,
                        world_refs=turn.world_refs,
                        initiative_refs=turn.initiative_refs,
                        notification_refs=turn.notification_refs,
                    )
                    return reported_turn(turn).response  # Never repeat model calls or actions.
                if not fresh and turn.state == "RECEIVED":
                    raise ModelError("interrupted_model_request")
                if fresh:
                    built = self.context.build(
                        turn, session_token=session_token, response_style=response_style
                    )
                    provenance = ModelProvenance(**self.models.identity().model_dump())
                    # Metadata only. Never persist the complete prompt or private reasoning trace.
                    write_audit(
                        self.audit,
                        AuditRecord(
                            principal_id=turn.owner_id,
                            session_id=turn.session_id,
                            correlation_id=turn.correlation_id,
                            action="model.request",
                            outcome="allowed",
                            reason_code="model_selected",
                            policy_refs=(f"model:{provenance.provider_id}/{provenance.model_id}",),
                        ),
                    )
                    try:
                        raw, provenance = await self.models.complete(
                            built.request,
                            session_token=session_token,
                            contains_memory=bool(built.memory_ids),
                            contains_world=bool(built.world_refs),
                            contains_initiatives=bool(built.initiative_refs),
                            contains_notifications=bool(built.notification_refs),
                        )
                    except asyncio.CancelledError:
                        response = self._response(
                            turn,
                            kind="failure",
                            text="Model request cancelled; no action submitted.",
                            error=ErrorDetail(code="model_cancelled", message="model_cancelled"),
                        ).model_copy(update={"provenance": provenance})
                        self.conversations._save(
                            turn,
                            session_token=session_token,
                            state="FAILED",
                            response=response,
                            provenance=provenance,
                        )
                        raise
                    except ModelError:
                        # Preserve the chosen provider's provenance even on availability failure.
                        turn = turn.model_copy(update={"provenance": provenance})
                        raise
                    turn = turn.model_copy(update={"provenance": provenance})
                    self.context.ensure_current(
                        built.memory_ids,
                        session_token=session_token,
                        world_refs=built.world_refs,
                        initiative_refs=built.initiative_refs,
                        notification_refs=built.notification_refs,
                    )
                    plan = (
                        self._validate_plan(raw, built.request)
                        if built.request.intent_inputs
                        else None
                    )
                    decision = None if plan else self._validate(raw, built.request)
                    turn = self.conversations._save(
                        turn,
                        session_token=session_token,
                        state="DECIDED",
                        decision=decision,
                        plan=plan,
                        intent_inputs=built.request.intent_inputs,
                        provenance=provenance,
                        memory_ids=built.memory_ids,
                        world_refs=built.world_refs,
                        initiative_refs=built.initiative_refs,
                        notification_refs=built.notification_refs,
                    )
                self.context.ensure_current(
                    turn.memory_ids,
                    session_token=session_token,
                    world_refs=turn.world_refs,
                    initiative_refs=turn.initiative_refs,
                    notification_refs=turn.notification_refs,
                )
                if turn.plan is not None:
                    return self._run_plan(turn, session_token)
                if turn.decision.action is None:
                    # Also protect a pre-upgrade DECIDED turn resumed after a crash.
                    # Preserve its stored decision as evidence; render safely.
                    decision = no_action_decision(turn.decision, turn.user_text)
                    response = self._response(
                        turn,
                        kind=decision.kind,
                        text=decision.text,
                        memory_ids=decision.memory_ids,
                        world_refs=turn.world_refs,
                        initiative_refs=turn.initiative_refs,
                        notification_refs=turn.notification_refs,
                        error=decision.error,
                        audit_ids=self._refs(turn.correlation_id),
                    )
                    self.conversations._save(
                        turn, session_token=session_token, state="COMPLETED", response=response
                    )
                    return response
                return self._action(turn, session_token)
        except (
            ModelError,
            ConversationError,
            MemoryError,
            IdentityError,
            StorageError,
            AuditWriteError,
            TaskConflictError,
        ) as exc:
            code = (
                getattr(exc, "code", None)
                or getattr(exc, "reason_code", None)
                or ("task_conflict" if isinstance(exc, TaskConflictError) else "audit_unavailable")
            )
            if isinstance(exc, ModelError) and exc.diagnostic is not None:
                # Numeric/type/allowlisted shape metadata only; no prompts or validator inputs.
                logging.getLogger("jarvis").warning(
                    "model.output_rejected",
                    extra={
                        "correlation_id": turn.correlation_id if turn else request_id,
                        "reason_code": code,
                        "model_diagnostic": exc.diagnostic,
                    },
                )
            response = CognitiveResponse(
                conversation_id=conversation_id,
                request_id=request_id,
                correlation_id=turn.correlation_id if turn else uuid4(),
                kind="failure",
                text=f"Request failed: {code}.",
                provenance=turn.provenance if turn else None,
                task_id=turn.task_id if turn else None,
                error=ErrorDetail(
                    code=code, message=exc.message if isinstance(exc, ModelError) else code
                ),
            )
            if turn is not None and turn.response is None:
                # Do not terminally discard submitted actions on storage/authority failures.
                # The task and accepted decision remain recoverable, with their stable IDs.
                try:
                    if (
                        turn.parent_request_id is None
                        and turn.plan is None
                        and turn.task_id is None
                        and (turn.decision is None or not turn.decision.action)
                    ):
                        self.conversations._save(
                            turn, session_token=session_token, state="FAILED", response=response
                        )
                except (StorageError, ConversationError, IdentityError, AuditWriteError):
                    pass  # Return observable failure; existing checkpoints remain authoritative.
            # No accepted proposal means no task submission was attempted. Never
            # apply this projection to an accepted action with an unknown outcome.
            if turn is not None:
                response = reported_turn(turn.model_copy(update={"response": response})).response
            return response

    def _run_plan(self, parent, token):
        """Finite ordered pass; accepted child checkpoints and tasks survive crashes."""
        started = time.monotonic()
        try:
            outcomes = []
            blocked = False
            for item in parent.plan.intents:
                self.context.ensure_current(
                    parent.memory_ids,
                    session_token=token,
                    world_refs=parent.world_refs,
                    initiative_refs=parent.initiative_refs,
                    notification_refs=parent.notification_refs,
                )
                if blocked and item.action is not None:
                    response = CognitiveResponse(
                        conversation_id=parent.conversation_id,
                        request_id=uuid5(parent.request_id, f"intent:{item.intent_index}"),
                        correlation_id=parent.correlation_id,
                        provenance=parent.provenance,
                        kind="pending",
                        text="This action is waiting for the earlier action's verified outcome; "
                        "no task has been submitted for this part.",
                    )
                    outcomes.append(
                        IntentOutcome(
                            intent_index=item.intent_index, purpose=item.purpose, response=response
                        )
                    )
                    continue
                child, _ = self.conversations._begin_intent(
                    parent, item.intent_index, session_token=token
                )
                if child.state == "RECEIVED":
                    child = self.conversations._save(
                        child,
                        session_token=token,
                        state="DECIDED",
                        decision=item.decision(),
                        provenance=parent.provenance,
                        memory_ids=parent.memory_ids,
                        world_refs=parent.world_refs,
                        initiative_refs=parent.initiative_refs,
                        notification_refs=parent.notification_refs,
                    )
                if child.response is not None:
                    response = child.response
                elif child.decision.action is not None:
                    response = self._action(child, token)
                else:
                    decision = child.decision
                    response = self._response(
                        child,
                        kind=decision.kind,
                        text=decision.text,
                        memory_ids=decision.memory_ids,
                        world_refs=child.world_refs,
                        error=decision.error,
                        audit_ids=self._refs(parent.correlation_id),
                    )
                    self.conversations._save(
                        child, session_token=token, state="COMPLETED", response=response
                    )
                outcomes.append(
                    IntentOutcome(
                        intent_index=item.intent_index, purpose=item.purpose, response=response
                    )
                )
                if item.action is not None and (
                    response.kind == "pending"
                    or response.task_state == "NEEDS_REVIEW"
                    or (response.kind == "failure" and response.task_state is None)
                ):
                    blocked = True
            self.context.ensure_current(
                parent.memory_ids,
                session_token=token,
                world_refs=parent.world_refs,
                initiative_refs=parent.initiative_refs,
                notification_refs=parent.notification_refs,
            )
            pending = blocked or any(
                o.response.kind == "pending"
                or (
                    o.purpose == "action"
                    and o.response.kind == "failure"
                    and o.response.task_state is None
                )
                for o in outcomes
            )
            successes = sum(
                o.response.kind in {"direct", "memory_grounded", "world_grounded", "action"}
                for o in outcomes
            )
            status = (
                "pending"
                if pending
                else "completed"
                if successes == len(outcomes)
                else "partial"
                if successes
                else "failed"
            )
            text = "\n".join(
                f"{o.intent_index + 1}. "
                + (
                    "Execution denied: "
                    if o.response.execution and o.response.execution.status == "denied"
                    else ""
                )
                + o.response.text
                + (
                    f" [Task {o.response.task_id}: "
                    f"{o.response.task_state or 'completion unconfirmed'}]"
                    if o.response.task_id
                    else ""
                )
                for o in outcomes
            )
            response = ComposedResponse(
                conversation_id=parent.conversation_id,
                request_id=parent.request_id,
                correlation_id=parent.correlation_id,
                provenance=parent.provenance,
                text=text,
                spoken_text="\n".join(
                    f"{o.intent_index + 1}. "
                    + (
                        "Execution denied: "
                        if o.response.execution and o.response.execution.status == "denied"
                        else ""
                    )
                    + o.response.text
                    + (
                        f" [Task state: {o.response.task_state or 'completion unconfirmed'}]"
                        if o.response.task_id
                        else ""
                    )
                    for o in outcomes
                ),
                status=status,
                intents=tuple(outcomes),
                memory_ids=tuple(dict.fromkeys(m for o in outcomes for m in o.response.memory_ids)),
                world_refs=parent.world_refs,
                initiative_refs=parent.initiative_refs,
                notification_refs=parent.notification_refs,
                audit_ids=self._refs(parent.correlation_id),
            )
            if not pending:
                self.conversations._save(
                    parent, session_token=token, state="COMPLETED", response=response
                )
            return response
        finally:
            self.context.metrics.record(
                "cognition.orchestration_ms", (time.monotonic() - started) * 1000
            )

    def _action(self, turn, token):
        task_id = turn.task_id
        try:
            return self._run_action(turn, token)
        except (
            StorageError,
            ConversationError,
            IdentityError,
            AuditWriteError,
            TaskConflictError,
        ) as exc:
            code = getattr(exc, "code", None) or getattr(exc, "reason_code", "audit_unavailable")
            # The accepted decision stays DECIDED/ACTION_PENDING. Its scoped stable
            # submission key recovers a task even if linking its ID failed after submit.
            if task_id is None:
                try:
                    action = turn.decision.action
                    task = self.tasks.submit(
                        (
                            TaskStep(
                                step_id="action",
                                capability_id=action.capability_id,
                                arguments=action.arguments,
                            ),
                        ),
                        session_token=token,
                        correlation_id=turn.correlation_id,
                        idempotency_key=f"conversation:{turn.conversation_id}/turn:{turn.request_id}",
                    )
                    task_id = task.task_id
                except (StorageError, IdentityError, AuditWriteError, TaskConflictError):
                    pass
            return self._response(
                turn,
                kind="failure",
                text=f"Action state preserved: {code}.",
                task_id=task_id,
                error=ErrorDetail(code=code, message=code),
            )

    def _run_action(self, turn, token):
        action = turn.decision.action
        if turn.task_id is None:
            task = self.tasks.submit(
                (
                    TaskStep(
                        step_id="action",
                        capability_id=action.capability_id,
                        arguments=action.arguments,
                    ),
                ),
                session_token=token,
                correlation_id=turn.correlation_id,
                idempotency_key=f"conversation:{turn.conversation_id}/turn:{turn.request_id}",
            )
            turn = self.conversations._save(
                turn, session_token=token, state="ACTION_PENDING", task_id=task.task_id
            )
        task = self.tasks.store.get(turn.task_id)
        if self.defer_actions and task.state in {"CREATED", "READY"}:
            available = self.context.registry.discover_authorized(
                session_token=token, policy=self.context.policy, correlation_id=turn.correlation_id
            )
            capability = next(
                (c for c in available if c.capability_id == action.capability_id), None
            )
            valid = capability is not None
            if valid:
                try:
                    validate_capability_arguments(capability, action.arguments)
                except Exception:
                    valid = False
            if valid:
                return self._response(
                    turn,
                    kind="pending",
                    text=f"Work submitted as durable task {turn.task_id}.",
                    task_id=turn.task_id,
                    task_state=task.state,
                    audit_ids=self._refs(turn.correlation_id),
                )
            # Invalid/denied proposals get the normal executor's rejection immediately;
            # deferral is never reported as permitted work for an unauthorized proposal.
        result = self.tasks.run(turn.task_id, session_token=token)
        task = result.task
        if task is None:
            return self._response(
                turn,
                kind="pending",
                text=f"Task {turn.task_id}: {result.status}.",
                task_id=turn.task_id,
                error=result.error,
                audit_ids=self._refs(turn.correlation_id),
            )
        execution = task.steps[-1].result
        if task.state == "COMPLETED":
            text = "Verified capability result: " + json.dumps(
                execution.output, sort_keys=True, ensure_ascii=False
            )
            kind, error = "action", None
        elif task.state in {"FAILED", "CANCELLED", "NEEDS_REVIEW"}:
            code = execution.error.code if execution and execution.error else task.state.casefold()
            text = f"Task {task.state}: {code}."
            kind = "failure"
            error = execution.error if execution and execution.error else task.error
            if error is None:
                error = ErrorDetail(code=code, message=code)
        else:
            text = f"Task {turn.task_id} is {task.state}; completion is unconfirmed."
            kind, error = "pending", task.error
        if (
            len(text) > 8192
            or contains_secret(text)
            or (
                execution
                and (
                    len(execution.model_dump_json().encode()) > 16384
                    or contains_secret(execution.model_dump_json())
                )
            )
        ):
            text = "Capability result is sensitive; inspect the restricted task record."
            execution = None
        response = self._response(
            turn,
            kind=kind,
            text=text,
            task_id=task.task_id,
            task_state=task.state,
            execution=execution,
            error=error,
            audit_ids=self._refs(turn.correlation_id),
        )
        if kind != "pending":
            self.conversations._save(
                turn,
                session_token=token,
                state="COMPLETED",
                response=response,
            )
        return response
