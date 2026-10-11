"""The sole request-facing invocation path: validate, authorize, audit, then invoke."""

import hashlib
import json
import logging
from collections.abc import Callable
from concurrent.futures import CancelledError
from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ConfigDict, JsonValue, TypeAdapter

from jarvis.authorization import PolicyEvaluator
from jarvis.capabilities import CapabilityRegistry
from jarvis.core.contracts import (
    ActionProposal,
    AuditRecord,
    ErrorDetail,
    ExecutionResult,
    PolicyDecision,
)
from jarvis.identity import IdentityError, IdentityRepository
from jarvis.observability import AuditSink, AuditWriteError, write_audit

_JSON_OBJECT = TypeAdapter(
    dict[str, JsonValue], config=ConfigDict(strict=True, allow_inf_nan=False)
)


def validate_capability_arguments(capability, arguments) -> None:
    """Shared pure schema check. This proves shape only, never authorization."""
    Draft202012Validator(capability.input_schema, format_checker=FormatChecker()).validate(
        arguments
    )


@dataclass(frozen=True)
class ReplayProfile:
    fingerprint: str
    retry_safe: bool


@dataclass(frozen=True)
class InvocationContext:
    task_id: UUID
    step_id: str
    attempt_id: UUID
    idempotency_key: str
    execution_guard_ref: str | None = None


def _profile(registered) -> ReplayProfile:
    metadata = {
        "capability": registered.capability.model_dump(mode="json"),
        "scope": registered.access_scope.model_dump(mode="json")
        if registered.access_scope
        else None,
        "idempotent": registered.supports_idempotency,
    }
    if registered.execution_guard_family is not None:
        metadata["execution_guard_family"] = registered.execution_guard_family
    return ReplayProfile(
        hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest(),
        registered.capability.side_effect == "none" or registered.supports_idempotency,
    )


class CapabilityExecutor:
    def __init__(
        self,
        identities: IdentityRepository,
        registry: CapabilityRegistry,
        policy: PolicyEvaluator,
        audit: AuditSink,
    ) -> None:
        if policy.identities is not identities:
            raise ValueError("executor and policy must share trusted identity state")
        self._identities = identities
        self._registry = registry
        self._policy = policy
        self._audit = audit

    def replay_profile(self, capability_id: str) -> ReplayProfile:
        """Trusted recovery metadata only; exposes neither provider nor an invocation path."""
        return _profile(self._registry._resolve_for_executor(capability_id))

    @property
    def identities(self) -> IdentityRepository:
        return self._identities

    def execute(
        self,
        proposal: ActionProposal,
        *,
        session_token: str,
        context: InvocationContext | None = None,
        before_invoke: Callable[[PolicyDecision, ReplayProfile], None] | None = None,
    ) -> ExecutionResult:
        try:
            return self._execute(
                proposal, session_token=session_token, context=context, before_invoke=before_invoke
            )
        except IdentityError as exc:
            # A durable identity transaction may fail before entering the pipeline.
            # Deny without invoking; if the same database cannot audit, report that failure.
            error = ErrorDetail(code=exc.reason_code, message=exc.reason_code)
            status = "denied"
            try:
                write_audit(
                    self._audit,
                    AuditRecord(
                        principal_id=None,
                        proposal_id=proposal.proposal_id,
                        session_id=None,
                        task_id=proposal.task_id,
                        correlation_id=proposal.correlation_id,
                        action="capability.resolve",
                        outcome="denied",
                        reason_code=exc.reason_code,
                    ),
                )
            except AuditWriteError:
                status = "execution_failure"
                error = ErrorDetail(code="audit_unavailable", message="audit_unavailable")
            logging.getLogger("jarvis").warning(
                "capability.security_unavailable",
                extra={
                    "correlation_id": proposal.correlation_id,
                    "proposal_id": proposal.proposal_id,
                    "reason_code": error.code,
                },
            )
            return ExecutionResult(
                proposal_id=proposal.proposal_id,
                task_id=proposal.task_id,
                correlation_id=proposal.correlation_id,
                status=status,
                error=error,
            )

    def _execute(
        self,
        proposal: ActionProposal,
        *,
        session_token: str,
        context: InvocationContext | None = None,
        before_invoke: Callable[[PolicyDecision, ReplayProfile], None] | None = None,
    ) -> ExecutionResult:
        # A valid schema is only intent. Freeze a validated snapshot before checking it.
        proposal = ActionProposal.model_validate(proposal.model_dump()).model_copy(deep=True)
        if context is not None and context.task_id != proposal.task_id:
            raise ValueError("invocation context must match the proposal's task")
        if (
            context is not None
            and context.idempotency_key != f"task:{context.task_id}/step:{context.step_id}"
        ):
            raise ValueError("invocation idempotency scope must match its task and step")
        with self._identities.transaction():
            decision = None
            principal_id = None
            session_id = None
            action = "capability.resolve"

            def finish(status: str, code: str, output=None) -> ExecutionResult:
                result = ExecutionResult(
                    proposal_id=proposal.proposal_id,
                    task_id=proposal.task_id,
                    correlation_id=proposal.correlation_id,
                    status=status,
                    output=output,
                    error=None if status == "success" else ErrorDetail(code=code, message=code),
                )
                outcome = {
                    "success": "succeeded",
                    "denied": "denied",
                    "cancelled": "cancelled",
                }.get(status, "failed")
                try:
                    write_audit(
                        self._audit,
                        AuditRecord(
                            principal_id=principal_id,
                            session_id=session_id,
                            proposal_id=proposal.proposal_id,
                            task_id=proposal.task_id,
                            decision_id=decision.decision_id if decision else None,
                            correlation_id=proposal.correlation_id,
                            action=action,
                            outcome=outcome,
                            reason_code=code,
                            policy_refs=decision.policy_refs if decision else (),
                        ),
                    )
                except AuditWriteError:
                    # After invocation the side effect may already exist. Never report success
                    # or imply rollback/retry safety when the outcome could not be audited.
                    result = ExecutionResult(
                        proposal_id=proposal.proposal_id,
                        task_id=proposal.task_id,
                        correlation_id=proposal.correlation_id,
                        status="execution_failure",
                        error=ErrorDetail(code="audit_unavailable", message="audit_unavailable"),
                    )
                logging.getLogger("jarvis").log(
                    logging.INFO if result.status == "success" else logging.WARNING,
                    "capability.execution",
                    extra={
                        "proposal_id": proposal.proposal_id,
                        "correlation_id": proposal.correlation_id,
                        "reason_code": result.error.code if result.error else code,
                    },
                )
                return result

            # Bind the transport credential to the proposal before examining capability data.
            try:
                session = self._identities.resolve_session(session_token)
                principal_id = session.principal_id
                session_id = session.session_id
            except IdentityError as exc:
                # A known capability still gets a policy denial audit when credentials fail.
                try:
                    registered = self._registry._resolve_for_executor(proposal.capability_id)
                    action = registered.capability.capability_id
                    decision = self._policy.evaluate(
                        session_token,
                        proposal,
                        registered.capability,
                        access_scope=registered.access_scope,
                    )
                except KeyError:
                    pass
                except AuditWriteError:
                    return finish("execution_failure", "audit_unavailable")
                except Exception:
                    return finish("execution_failure", "policy_unavailable")
                return finish("denied", exc.reason_code)
            except Exception:
                return finish("execution_failure", "identity_unavailable")

            try:
                registered = self._registry._resolve_for_executor(proposal.capability_id)
            except KeyError:
                return finish("validation_failure", "capability_unknown")
            except Exception:
                return finish("execution_failure", "registry_unavailable")
            capability = registered.capability
            action = capability.capability_id
            # Session mismatches/unknown identities are audited by the same policy entry point.
            if session_id != proposal.session_id or principal_id is None:
                try:
                    decision = self._policy.evaluate(
                        session_token, proposal, capability, access_scope=registered.access_scope
                    )
                except AuditWriteError:
                    return finish("execution_failure", "audit_unavailable")
                except Exception:
                    return finish("execution_failure", "policy_unavailable")
                return finish("denied", decision.reason_code)

            try:
                validate_capability_arguments(capability, proposal.arguments)
            except Exception:
                return finish("validation_failure", "input_invalid")
            try:
                decision = self._policy.evaluate(
                    session_token, proposal, capability, access_scope=registered.access_scope
                )
                decision = PolicyDecision.model_validate(decision.model_dump())
            except AuditWriteError:
                return finish("execution_failure", "audit_unavailable")
            except Exception:
                return finish("execution_failure", "policy_unavailable")
            if decision.decision != "allow":
                return finish("denied", decision.reason_code)
            if (
                decision.proposal_id != proposal.proposal_id
                or decision.session_id != session_id
                or decision.principal_id != principal_id
                or decision.correlation_id != proposal.correlation_id
            ):
                return finish("execution_failure", "policy_decision_mismatch")
            if registered.execution_guard_family is not None and (
                context is None
                or before_invoke is None
                or context.execution_guard_ref is None
                or context.execution_guard_ref.split(":", 1)[0] != registered.execution_guard_family
            ):
                return finish("denied", "task_guard_required")
            if registered.provider is None:
                return finish("execution_failure", "provider_unavailable")
            if before_invoke is not None:
                try:
                    # Current policy succeeded. Commit the attempt before any provider effect.
                    before_invoke(decision, _profile(registered))
                except Exception:
                    return finish("execution_failure", "checkpoint_unavailable")
            try:
                if registered.supports_idempotency:
                    output = registered.provider(
                        deepcopy(proposal.arguments),
                        idempotency_key=context.idempotency_key
                        if context
                        else f"proposal:{proposal.proposal_id}",
                    )
                else:
                    output = registered.provider(deepcopy(proposal.arguments))
            except TimeoutError:
                return finish("timeout", "provider_timeout")
            except CancelledError:
                return finish("cancelled", "provider_cancelled")
            except Exception:
                return finish("execution_failure", "provider_failed")
            try:
                output = _JSON_OBJECT.validate_python(output)
                validator = Draft202012Validator(
                    capability.output_schema, format_checker=FormatChecker()
                )
                validator.validate(output)
            except Exception:
                return finish("execution_failure", "output_invalid")
            return finish("success", "executed", deepcopy(output))
