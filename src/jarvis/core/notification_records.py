"""Provisional notification v1: content, inbox interactions and transport are distinct."""

import json
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, model_validator

from jarvis.core.contracts import Contract, Name
from jarvis.core.time import utc_now
from jarvis.core.world_records import Key

Priority = Literal["LOW", "NORMAL", "HIGH", "URGENT"]
Category = Literal[
    "informational",
    "task_status",
    "initiative_proposal",
    "approval_request",
    "system_warning",
    "device_status",
    "security",
]
PRIORITIES = {"LOW": 0, "NORMAL": 1, "HIGH": 2, "URGENT": 3}
DeliveryState = Literal[
    "PENDING",
    "READY",
    "SENDING",
    "DELIVERED",
    "RETRY_WAIT",
    "FAILED",
    "EXPIRED",
    "NEEDS_REVIEW",
    "CANCELLED",
]
TERMINAL = {"DELIVERED", "FAILED", "EXPIRED", "NEEDS_REVIEW", "CANCELLED"}
TRANSITIONS = {
    "PENDING": {"READY", "RETRY_WAIT", "CANCELLED", "EXPIRED"},
    "READY": {"SENDING", "RETRY_WAIT", "CANCELLED", "EXPIRED", "FAILED"},
    "SENDING": {"SENDING", "DELIVERED", "RETRY_WAIT", "FAILED", "NEEDS_REVIEW", "CANCELLED"},
    "RETRY_WAIT": {"READY", "RETRY_WAIT", "CANCELLED", "EXPIRED"},
    **{s: set() for s in TERMINAL},
}


class NotificationReference(Contract):
    contract_type: Literal["notification_reference"] = "notification_reference"
    notification_id: UUID
    revision: Annotated[int, Field(ge=0)]
    source_revision: Annotated[int, Field(ge=0)]


class NotificationSource(Contract):
    contract_type: Literal["notification_source"] = "notification_source"
    kind: Literal["task", "initiative", "memory", "world"]
    source_id: UUID
    revision: Annotated[int, Field(ge=0)]
    event_id: UUID | None = None


class NotificationRecipient(Contract):
    contract_type: Literal["notification_recipient"] = "notification_recipient"
    principal_id: UUID
    address_type: Literal["principal"] = "principal"


class NotificationContent(Contract):
    contract_type: Literal["notification_content"] = "notification_content"
    title: Annotated[str, Field(min_length=1, max_length=160)]
    text: Annotated[str, Field(min_length=1, max_length=2048)]
    evidence_ids: Annotated[tuple[UUID, ...], Field(max_length=64)] = ()
    approval_reference: UUID | None = None


class NotificationPolicy(Contract):
    contract_type: Literal["notification_policy"] = "notification_policy"
    policy_id: Key
    origin_id: UUID
    recipient: NotificationRecipient
    source_kind: Literal["task", "initiative", "memory", "world"]
    # Optional exact resource restriction; broad policies remain confined to source access.
    source_id: UUID | None = None
    enabled: bool = True
    priority: Priority = "NORMAL"
    channels: Annotated[tuple[Key, ...], Field(max_length=4)] = ("simulated-push",)
    urgent_quiet_override: bool = False
    cooldown_seconds: Annotated[int, Field(ge=0, le=86400)] = 60
    recipient_rate: Annotated[int, Field(ge=1, le=1000)] = 30
    channel_rate: Annotated[int, Field(ge=1, le=1000)] = 30
    rate_window_seconds: Annotated[int, Field(ge=1, le=86400)] = 3600
    queue_limit: Annotated[int, Field(ge=1, le=1000)] = 100
    max_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    retry_seconds: Annotated[int, Field(ge=1, le=86400)] = 60
    expires_seconds: Annotated[int, Field(ge=1, le=604800)] = 86400
    revision: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def unique_channels(self) -> Self:
        if len(set(self.channels)) != len(self.channels) or "local-inbox" in self.channels:
            raise ValueError("inbox is implicit; additional channels must be unique")
        return self


class NotificationPreference(Contract):
    contract_type: Literal["notification_preference"] = "notification_preference"
    principal_id: UUID
    categories: Annotated[tuple[Category, ...], Field(max_length=7)] = (
        "informational",
        "task_status",
        "initiative_proposal",
        "approval_request",
        "system_warning",
        "device_status",
        "security",
    )
    channels: Annotated[tuple[Key, ...], Field(max_length=4)] = ("simulated-push",)
    timezone: Annotated[str, Field(min_length=1, max_length=100)] = "UTC"
    quiet_start_minute: Annotated[int, Field(ge=0, le=1439)] | None = None
    quiet_end_minute: Annotated[int, Field(ge=0, le=1439)] | None = None
    minimum_priority: Priority = "LOW"
    cooldown_seconds: Annotated[int, Field(ge=0, le=86400)] = 0
    revision: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def valid(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("unknown recipient timezone") from None
        if (self.quiet_start_minute is None) != (self.quiet_end_minute is None):
            raise ValueError("quiet hours require both endpoints")
        if self.quiet_start_minute is not None and self.quiet_start_minute == self.quiet_end_minute:
            raise ValueError("zero length quiet hours unsupported")
        if len(set(self.channels)) != len(self.channels) or len(set(self.categories)) != len(
            self.categories
        ):
            raise ValueError("duplicate preference values")
        return self


class Notification(Contract):
    contract_type: Literal["notification"] = "notification"
    notification_id: UUID = Field(default_factory=uuid4)
    origin_id: UUID
    origin_subsystem: Literal["jarvis.notifications"] = "jarvis.notifications"
    recipient: NotificationRecipient
    source: NotificationSource
    policy_id: Key
    policy_revision: Annotated[int, Field(ge=0)]
    correlation_id: UUID
    causation_id: UUID | None = None
    category: Category
    priority: Priority
    classification: Literal["private", "shared"] = "private"
    content: NotificationContent
    created_at: AwareDatetime = Field(default_factory=utc_now)
    expires_at: AwareDatetime
    retention_ref: Literal["retention.manual"] = "retention.manual"
    deduplication_key: Name
    delivery_status: Literal["ACCEPTED", "TRANSPORT_DELIVERED", "ATTENTION_REQUIRED"] = "ACCEPTED"
    inbox_state: Literal["UNREAD", "READ", "ACKNOWLEDGED", "DISMISSED", "EXPIRED"] = "UNREAD"
    revision: Annotated[int, Field(ge=0)] = 0
    audit_id: UUID = Field(default_factory=uuid4)

    @model_validator(mode="after")
    def lifetime(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("invalid notification lifetime")
        if self.content.approval_reference is not None and (
            self.source.kind != "initiative"
            or self.content.approval_reference != self.source.source_id
        ):
            raise ValueError("approval references bind only to the source initiative")
        return self


class NotificationChannel(Contract):
    contract_type: Literal["notification_channel"] = "notification_channel"
    channel_id: Key
    provider_id: Key
    recipient_types: tuple[Literal["principal"], ...] = ("principal",)
    destination: Literal["local", "simulated", "cloud"]
    privacy: Literal["local-only"] = "local-only"
    supports_idempotency: bool = False
    semantics_ref: Key


class NotificationDeliveryRequest(Contract):
    contract_type: Literal["notification_delivery_request"] = "notification_delivery_request"
    delivery_id: UUID
    notification: Notification
    idempotency_key: Name


class NotificationDeliveryResult(Contract):
    contract_type: Literal["notification_delivery_result"] = "notification_delivery_result"
    outcome: Literal["delivered", "temporary_failure", "permanent_failure", "unknown"]
    provider_message_ref: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    error_code: Literal["temporary_failure", "permanent_failure", "timeout", "unknown"] | None = (
        None
    )
    retryable: bool = False
    evidence: Literal["local_commit", "simulated_provider_report"] = "simulated_provider_report"

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.outcome == "delivered":
            if self.provider_message_ref is None or self.error_code is not None or self.retryable:
                raise ValueError("delivered requires a provider reference, not a human receipt")
        elif self.error_code is None:
            raise ValueError("failed outcomes require a classified error")
        if self.retryable != (self.outcome == "temporary_failure"):
            raise ValueError("only known temporary failures are retryable")
        return self


class NotificationAttempt(Contract):
    contract_type: Literal["notification_attempt"] = "notification_attempt"
    attempt_id: UUID = Field(default_factory=uuid4)
    delivery_id: UUID
    number: Annotated[int, Field(ge=1, le=10)]
    task_id: UUID
    provider_id: Key
    idempotency_key: Name
    state: Literal["RUNNING", "FINISHED", "OUTCOME_UNKNOWN"] = "RUNNING"
    started_at: AwareDatetime
    admitted_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    result: NotificationDeliveryResult | None = None

    @model_validator(mode="after")
    def valid(self) -> Self:
        if (self.state == "RUNNING") != (self.finished_at is None):
            raise ValueError("only running attempts lack a finish time")
        if self.state == "FINISHED" and self.result is None:
            raise ValueError("finished attempts need a verified adapter outcome")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("invalid attempt time")
        return self


class NotificationDelivery(Contract):
    contract_type: Literal["notification_delivery"] = "notification_delivery"
    delivery_id: UUID = Field(default_factory=uuid4)
    notification_id: UUID
    recipient_id: UUID
    channel: NotificationChannel
    idempotency_key: Name
    state: DeliveryState = "PENDING"
    revision: Annotated[int, Field(ge=0)] = 0
    due_at: AwareDatetime
    attempts: Annotated[tuple[NotificationAttempt, ...], Field(max_length=10)] = ()
    provider_message_ref: Annotated[str, Field(max_length=160)] | None = None
    reason_code: Key = "accepted"

    @model_validator(mode="after")
    def valid(self) -> Self:
        if any(
            a.delivery_id != self.delivery_id
            or a.number != i + 1
            or a.idempotency_key != self.idempotency_key
            or a.provider_id != self.channel.provider_id
            for i, a in enumerate(self.attempts)
        ):
            raise ValueError("attempt binding mismatch")
        if self.state == "SENDING" and (not self.attempts or self.attempts[-1].state != "RUNNING"):
            raise ValueError("sending requires a running attempt")
        if (
            self.state == "DELIVERED"
            and self.channel.channel_id != "local-inbox"
            and (
                not self.attempts
                or self.attempts[-1].state != "FINISHED"
                or self.attempts[-1].result is None
                or self.attempts[-1].result.outcome != "delivered"
                or self.attempts[-1].result.provider_message_ref != self.provider_message_ref
            )
        ):
            raise ValueError("transport delivery requires a recorded adapter outcome")
        if self.state == "DELIVERED" and self.provider_message_ref is None:
            raise ValueError("delivery needs a durable/provider reference")
        return self


class NotificationInteraction(Contract):
    contract_type: Literal["notification_interaction"] = "notification_interaction"
    interaction_id: UUID = Field(default_factory=uuid4)
    notification_id: UUID
    principal_id: UUID
    session_id: UUID
    expected_revision: Annotated[int, Field(ge=0)]
    interaction_type: Literal["read", "unread", "acknowledge", "dismiss", "expire"]
    occurred_at: AwareDatetime
    correlation_id: UUID


class NotificationContext(Contract):
    contract_type: Literal["notification_context"] = "notification_context"
    notification_id: UUID
    revision: Annotated[int, Field(ge=0)]
    title: Annotated[str, Field(max_length=160)]
    text: Annotated[str, Field(max_length=2048)]
    inbox_state: Name
    delivery_states: Annotated[tuple[DeliveryState, ...], Field(max_length=5)]
    source: NotificationSource
    source_state: Name
    source_revision: Annotated[int, Field(ge=0)]


TYPES = {
    m.model_fields["contract_type"].default: m
    for m in (
        Notification,
        NotificationSource,
        NotificationRecipient,
        NotificationContent,
        NotificationPolicy,
        NotificationChannel,
        NotificationDelivery,
        NotificationAttempt,
        NotificationInteraction,
        NotificationPreference,
        NotificationReference,
        NotificationDeliveryRequest,
        NotificationDeliveryResult,
        NotificationContext,
    )
}


def decode_notification(serialized):
    from jarvis.core.cognitive_records import explicit_versions

    value = json.loads(serialized)
    if not isinstance(value, dict) or value.get("contract_type") not in TYPES:
        raise ValueError("unknown notification contract")
    model = TYPES[value["contract_type"]]
    explicit_versions(value, model)
    return model.model_validate_json(serialized)
