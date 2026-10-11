"""Replaceable local/simulated transports. No network sends in Phase 8."""

import json
from typing import Protocol

from jarvis.core.notification_records import (
    NotificationChannel,
    NotificationDeliveryRequest,
    NotificationDeliveryResult,
)


class DeliveryChannel(Protocol):
    metadata: NotificationChannel

    def send(self, request: NotificationDeliveryRequest) -> NotificationDeliveryResult: ...


class LocalInboxChannel:
    metadata = NotificationChannel(
        channel_id="local-inbox",
        provider_id="local-inbox",
        destination="local",
        supports_idempotency=True,
        semantics_ref="committed-inbox-v1",
    )

    def send(self, request):
        # The service invokes this only after the inbox/intent transaction committed.
        return NotificationDeliveryResult(
            outcome="delivered",
            evidence="local_commit",
            provider_message_ref=f"inbox:{request.notification.notification_id}",
        )


class SimulatedPushChannel:
    """SQLite result ledger provides stable key/payload binding across process death.

    Outcomes are synthetic provider reports, never evidence that a human received/read.
    The fake ledger is adapter state, not authoritative notification/inbox state.
    """

    def __init__(
        self,
        database,
        *,
        channel_id="simulated-push",
        outcomes=(),
        supports_idempotency=True,
        after_effect=None,
    ):
        self.database = database
        self.metadata = NotificationChannel(
            channel_id=channel_id,
            provider_id=channel_id,
            destination="simulated",
            supports_idempotency=supports_idempotency,
            semantics_ref="durable-fake-key-result-v1" if supports_idempotency else "no-replay-v1",
        )
        self.outcomes = list(outcomes)
        self.calls = []
        self.after_effect = after_effect

    def send(self, request):
        from hashlib import sha256

        self.calls.append(request.delivery_id)
        payload = json.dumps(
            {
                "delivery_id": str(request.delivery_id),
                "key": request.idempotency_key,
                "notification": request.notification.model_dump(
                    mode="json", exclude={"revision", "inbox_state", "delivery_status", "audit_id"}
                ),
            },
            sort_keys=True,
        )
        result = None
        if self.metadata.supports_idempotency:
            with self.database.transaction() as c:
                row = c.execute(
                    "SELECT payload,result FROM simulated_notification_ledger WHERE ch"
                    "annel_id=? AND operation_key=?",
                    (self.metadata.channel_id, request.idempotency_key),
                ).fetchone()
                if row:
                    if row[0] != payload:
                        return NotificationDeliveryResult(
                            outcome="permanent_failure", error_code="permanent_failure"
                        )
                    result = NotificationDeliveryResult.model_validate_json(row[1])
        if result is None:
            outcome = self.outcomes.pop(0) if self.outcomes else "delivered"
            if outcome == "timeout":
                raise TimeoutError()
            if outcome in {"delivered", "duplicate"}:
                result = NotificationDeliveryResult(
                    outcome="delivered",
                    provider_message_ref="fake:"
                    + sha256(request.idempotency_key.encode()).hexdigest()[:32],
                )
            else:
                result = NotificationDeliveryResult(
                    outcome=outcome,
                    error_code=outcome,
                    retryable=outcome == "temporary_failure",
                )
            if self.metadata.supports_idempotency and result.outcome == "delivered":
                with self.database.transaction(write=True) as c:
                    c.execute(
                        "INSERT INTO simulated_notification_ledger VALUES(?,?,?,?)",
                        (
                            self.metadata.channel_id,
                            request.idempotency_key,
                            payload,
                            result.model_dump_json(),
                        ),
                    )
        if self.after_effect:
            self.after_effect(request, result)
        return result


class SimulatedUnreliableChannel(SimulatedPushChannel):
    def __init__(self, database, *, outcomes=(), after_effect=None, supports_idempotency=False):
        super().__init__(
            database,
            channel_id="simulated-unreliable",
            outcomes=outcomes,
            supports_idempotency=supports_idempotency,
            after_effect=after_effect,
        )
