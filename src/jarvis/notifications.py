"""Durable scoped inbox and bounded deliveries through the existing task/executor path."""

import hashlib
import json
import re
from contextlib import contextmanager
from datetime import UTC, timedelta
from typing import Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from jarvis.conversations import contains_secret
from jarvis.core.contracts import ActionProposal, AuditRecord, Capability, decode_contract
from jarvis.core.notification_records import (
    PRIORITIES,
    TERMINAL,
    Notification,
    NotificationAttempt,
    NotificationChannel,
    NotificationContent,
    NotificationContext,
    NotificationDelivery,
    NotificationDeliveryRequest,
    NotificationDeliveryResult,
    NotificationInteraction,
    NotificationPolicy,
    NotificationPreference,
    NotificationSource,
    decode_notification,
)
from jarvis.core.task_records import TaskStep
from jarvis.core.time import utc_now
from jarvis.identity import IdentityError
from jarvis.initiative import InitiativeError
from jarvis.memory import MemoryError
from jarvis.notification_channels import LocalInboxChannel
from jarvis.observability import AuditWriteError, write_audit
from jarvis.storage.errors import StorageError
from jarvis.world_state import WorldError


class NotificationError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class NotificationStore(Protocol):
    """Private adapter port. Only the service supplies visibility/authority."""

    identities: object
    audit: object

    def _bind(self) -> object: ...
    def _get(self, notification_id, recipient_id, *, access): ...
    def _list(self, recipient_id, *, limit, after=None, text=None, access): ...
    def _policy(self, policy_id, *, access): ...
    def _policies(self, origin_id, *, access): ...
    def _configure(self, value, expected, audit, *, access): ...
    def _preference(self, principal_id, *, access): ...
    def _admit(
        self,
        notification,
        deliveries,
        policy,
        session_id,
        *,
        now,
        receipt=None,
        suppression=None,
        access,
    ): ...
    def _sources(self, policy, *, limit, access): ...
    def _deliveries(self, notification_id, recipient_id, *, access): ...
    def _context(self, recipient_id, *, terms=(), unread=False, access): ...
    def _delivery(self, delivery_id, recipient_id, *, access): ...
    def _prepared(self, delivery_id, *, access): ...
    def _pending(self, recipient_id, *, limit, access): ...
    def _save_delivery(self, old, new, session_id, *, access): ...
    def _limits(self, delivery, policy, now, *, access): ...
    def _claim(self, delivery_id, *, access): ...
    def _interact(self, old, new, interaction, *, access): ...


def quiet(preference, now):
    start, end = preference.quiet_start_minute, preference.quiet_end_minute
    if start is None:
        return False
    local = now.astimezone(ZoneInfo(preference.timezone))
    minute = local.hour * 60 + local.minute
    return start <= minute < end if start < end else minute >= start or minute < end


def next_allowed(preference, now):
    """Search bounded real UTC minutes: nonexistent local times are never manufactured.

    Each DST fold is evaluated as its actual local wall time. At most two days of
    real instants covers all supported daily quiet periods and ordinary DST changes.
    """
    candidate = now.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(2880):
        if not quiet(preference, candidate):
            return candidate
        candidate += timedelta(minutes=1)
    raise NotificationError("notification_schedule_unavailable")


def is_notification_question(text):
    text = text.casefold()
    return any(
        term in text
        for term in (
            "notification",
            "inbox",
            "while i was away",
            "notify me",
            "waiting for my approval",
            "has that problem been resolved",
        )
    )


def describe_notifications(records):
    if not records:
        return "No notifications are available in your authorized inbox."
    return "\n".join(
        f"{r.title}: {r.text} Inbox={r.inbox_state}; transport={','.join(r.delivery_states)}; "
        f"current source={r.source_state}; notification={r.notification_id}. "
        "Transport delivery does not establish human receipt or approval. "
        "Source status alone does not establish environmental problem resolution."
        for r in records
    )


class NotificationService:
    def __init__(
        self,
        store: NotificationStore,
        *,
        permissions,
        policy,
        tasks,
        registry,
        initiatives,
        memory,
        world,
        channels=(),
        clock=utc_now,
    ):
        self.identities, self.audit = store.identities, store.audit
        if any(
            x is not self.identities
            for x in (
                permissions.identities,
                policy.identities,
                tasks.identities,
                initiatives.identities,
                world.identities,
                memory._identities,
            )
        ):
            raise ValueError("notification services must share trusted authority")
        self._store, self._access = store, store._bind()
        self.permissions, self.policy, self.tasks, self.registry = (
            permissions,
            policy,
            tasks,
            registry,
        )
        self.initiatives, self.memory, self.world, self.clock = initiatives, memory, world, clock
        self.channels = {}
        candidates = (LocalInboxChannel(), *channels)
        metadata_values = tuple(
            decode_notification(c.metadata.model_dump_json()) for c in candidates
        )
        if any(m.destination == "cloud" for m in metadata_values):
            raise ValueError("Phase 8 permits only local/simulated channels")
        if len({m.channel_id for m in metadata_values}) != len(metadata_values):
            raise ValueError("duplicate channel registration")
        for channel in candidates:
            metadata = decode_notification(channel.metadata.model_dump_json())
            if not isinstance(metadata, NotificationChannel) or metadata.destination == "cloud":
                raise ValueError("Phase 8 permits only local/simulated channels")
            if metadata.channel_id in self.channels or not callable(channel.send):
                raise ValueError("invalid channel registration")
            self.channels[metadata.channel_id] = channel
            channel_id = metadata.channel_id

            def provider(arguments, *, idempotency_key=None, channel_id=channel_id):
                return self._send_provider(channel_id, UUID(arguments["delivery_id"]))

            registry.register(
                Capability(
                    capability_id=f"notification.deliver:{channel_id}",
                    required_permissions=("notifications.deliver",),
                    side_effect="local",
                    adapter_ref=f"notification.channel:{channel_id}",
                    input_schema={
                        "type": "object",
                        "required": ["delivery_id"],
                        "properties": {"delivery_id": {"type": "string", "format": "uuid"}},
                        "additionalProperties": False,
                    },
                    output_schema={"type": "object"},
                ),
                provider,
                supports_idempotency=metadata.supports_idempotency,
                execution_guard_family="notification",
            )

        def guard(task, token):
            try:
                self._task_guard(task, token)
            except NotificationError as exc:
                raise StorageError(exc.code) from None

        tasks.register_execution_guard("notification", guard)

    @contextmanager
    def _request(self, token, action):
        session = None
        try:
            with self.identities.transaction():
                session = self.identities.resolve_session(token)
                if session.principal_id is None or session.authentication_ref is None:
                    raise NotificationError("unauthenticated")
                try:
                    yield session
                except (StorageError, WorldError, MemoryError, InitiativeError) as exc:
                    raise NotificationError(exc.code) from None
                except (ValueError, TypeError, KeyError, AttributeError):
                    raise NotificationError("notification_validation_failure") from None
                except AuditWriteError:
                    raise NotificationError("audit_unavailable") from None
        except IdentityError as exc:
            raise NotificationError(exc.reason_code) from None
        except NotificationError as exc:
            write_audit(
                self.audit,
                AuditRecord(
                    principal_id=session.principal_id if session else None,
                    session_id=session.session_id if session else None,
                    correlation_id=uuid4(),
                    action=action,
                    outcome="denied",
                    reason_code=exc.code,
                ),
            )
            raise

    def _require(self, session, token, *names):
        capability = Capability(
            capability_id="notification.policy",
            required_permissions=names,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            adapter_ref="notification.service",
        )
        decision = self.policy.evaluate(
            token,
            ActionProposal(
                session_id=session.session_id,
                capability_id=capability.capability_id,
                correlation_id=uuid4(),
                origin="rule",
                source_ref="jarvis.notifications",
            ),
            capability,
        )
        if decision.decision != "allow":
            raise NotificationError("notification_access_denied")

    def _active(self, principal_id):
        principal = self.identities.get_principal(principal_id)
        if principal is None or principal.status != "active":
            raise NotificationError("notification_authority_unavailable")

    def configure(self, value, *, session_token, expected_revision=None):
        with self._request(session_token, "notification.configure") as session:
            self._require(session, session_token, "notifications.manage")
            if session.principal_id != self.identities.owner_id:
                raise NotificationError("notification_access_denied")
            value = decode_notification(
                value.model_dump_json()
                if isinstance(value, NotificationPolicy)
                else json.dumps(value)
            )
            if not isinstance(value, NotificationPolicy):
                raise NotificationError("notification_validation_failure")
            self._active(value.origin_id)
            self._active(value.recipient.principal_id)
            if any(c not in self.channels for c in value.channels):
                raise NotificationError("notification_channel_unavailable")
            return self._store._configure(
                value,
                expected_revision,
                AuditRecord(
                    principal_id=session.principal_id,
                    session_id=session.session_id,
                    correlation_id=uuid4(),
                    action="notification.configure",
                    outcome="succeeded",
                    reason_code="configured",
                ),
                access=self._access,
            )

    def policies(self, *, session_token):
        with self._request(session_token, "notification.policies") as session:
            self._require(session, session_token, "notifications.publish")
            return self._store._policies(session.principal_id, access=self._access)

    def preferences(self, *, session_token):
        with self._request(session_token, "notification.preferences") as session:
            self._require(session, session_token, "notifications.read")
            return self._store._preference(session.principal_id, access=self._access)

    def set_preferences(self, value, *, session_token, expected_revision=None):
        with self._request(session_token, "notification.preferences") as session:
            self._require(session, session_token, "notifications.preferences")
            value = decode_notification(
                value.model_dump_json()
                if isinstance(value, NotificationPreference)
                else json.dumps(value)
            )
            if (
                not isinstance(value, NotificationPreference)
                or value.principal_id != session.principal_id
            ):
                raise NotificationError("notification_access_denied")
            if any(c not in self.channels or c == "local-inbox" for c in value.channels):
                raise NotificationError("notification_channel_unavailable")
            return self._store._configure(
                value,
                expected_revision,
                AuditRecord(
                    principal_id=session.principal_id,
                    session_id=session.session_id,
                    correlation_id=uuid4(),
                    action="notification.preferences",
                    outcome="succeeded",
                    reason_code="configured",
                ),
                access=self._access,
            )

    def _source(self, reference, session, token, *, recipient=None):
        """Read through actual domain services first, then validate an explicit recipient.

        A recipient session is never fabricated. Cross-recipient shared admission uses
        current principal/grant and explicit source scope; retrieval/delivery reads the
        source again through the recipient's own authenticated domain service.
        """
        who = recipient or session.principal_id
        self._active(who)
        if len(self.permissions.current_grants(who, ("notifications.read",))) != 1:
            raise NotificationError("notification_unavailable")
        kind = reference.kind
        if kind == "initiative":
            record = self.initiatives.get(reference.source_id, session_token=token)
            if record.owner_id != who:
                raise NotificationError("notification_unavailable")
            for ref in record.proposal.world_refs:
                self.world.get(ref.state_id, session_token=token)
            for memory_id in record.proposal.memory_ids:
                self.memory.get(memory_id, session_token=token)
            state = record.state
            category = "approval_request" if state == "AWAITING_APPROVAL" else "initiative_proposal"
            content = NotificationContent(
                title=f"Initiative {state}",
                text=f"{record.proposal.explanation} Original state: {state}.",
                evidence_ids=record.proposal.evidence_ids,
                approval_reference=record.initiative_id if category == "approval_request" else None,
            )
            corr = record.correlation_id
            scope = None
        elif kind == "task":
            record = self.tasks.store.get(reference.source_id)
            if record is None or record.owner_id != session.principal_id or record.owner_id != who:
                raise NotificationError("notification_unavailable")
            # No task arguments/output copied into notifications; only actual lifecycle.
            state, category, scope = record.state, "task_status", None
            content = NotificationContent(
                title=f"Task {state}", text=f"Task {record.task_id} has verified state {state}."
            )
            corr = record.correlation_id
        elif kind == "memory":
            record = self.memory.get(reference.source_id, session_token=token)
            state, category, scope = "current", "informational", record.access_scope
            content = NotificationContent(
                title="Authorized memory update",
                text=json.dumps(record.content, ensure_ascii=False),
            )
            corr = uuid4()
        else:
            record = self.world.get(reference.source_id, session_token=token)
            state, category, scope = record.status, "device_status", record.access_scope
            content = NotificationContent(
                title=f"{record.entity_id} {record.attribute}",
                text=(
                    f"Historical report: {record.location} {record.attribute}={record.value}; "
                    f"evidence={record.status}."
                ),
                evidence_ids=record.evidence_ids,
            )
            corr = uuid4()
        if scope is not None:
            if who != scope.owner_id and not (
                scope.kind in {"shared", "household"} and who in scope.shared_with
            ):
                raise NotificationError("notification_unavailable")
            permission = "memory.read" if kind == "memory" else "world.read"
            if scope.kind == "household" and not self.permissions.current_grants(
                who, ("memory.household.read",)
            ):
                raise NotificationError("notification_unavailable")
            if not self.permissions.current_grants(who, (permission,)):
                raise NotificationError("notification_unavailable")
        if contains_secret(content.model_dump_json()):
            raise NotificationError("notification_content_rejected")
        return record, content, category, state, corr, scope

    def _policy_for(self, notification):
        policy = self._store._policy(notification.policy_id, access=self._access)
        if (
            policy is None
            or not policy.enabled
            or policy.origin_id != notification.origin_id
            or policy.recipient != notification.recipient
            or policy.source_kind != notification.source.kind
        ):
            raise NotificationError("notification_unavailable")
        return policy

    def _visible(self, notification, session, token):
        if notification is None or notification.recipient.principal_id != session.principal_id:
            raise NotificationError("notification_unavailable")
        self._policy_for(notification)
        try:
            return self._source(notification.source, session, token)
        except (MemoryError, WorldError, InitiativeError):
            raise NotificationError("notification_unavailable") from None

    def create(self, policy_id, source, *, session_token):
        """Trusted source references only. No recipient/priority/content/authority claims."""
        with self._request(session_token, "notification.create") as session:
            self._require(session, session_token, "notifications.publish")
            policy = self._store._policy(policy_id, access=self._access)
            if policy is None or policy.origin_id != session.principal_id:
                raise NotificationError("notification_access_denied")
            source = decode_notification(
                source.model_dump_json()
                if isinstance(source, NotificationSource)
                else json.dumps(source)
            )
            if not isinstance(source, NotificationSource):
                raise NotificationError("notification_validation_failure")
            return self._create(policy, source, session, session_token)

    def _create(self, policy, source, session, token, *, receipt=None):
        now = self.clock()
        if source.kind != policy.source_kind or (
            policy.source_id is not None and source.source_id != policy.source_id
        ):
            raise NotificationError("notification_source_mismatch")
        record, content, category, state, corr, scope = self._source(
            source, session, token, recipient=policy.recipient.principal_id
        )
        # Caller references cannot forge stale content/revisions or an arbitrary causation ID.
        if source.revision != record.revision or source.event_id is not None:
            raise NotificationError("notification_source_mismatch")
        preference = self._store._preference(policy.recipient.principal_id, access=self._access)
        suppression = None
        if not policy.enabled:
            suppression = "policy_disabled"
        elif category not in preference.categories:
            suppression = "category_disabled"
        elif PRIORITIES[policy.priority] < PRIORITIES[preference.minimum_priority]:
            suppression = "priority_filtered"
        key = hashlib.sha256(
            json.dumps(
                [
                    policy.policy_id,
                    str(policy.recipient.principal_id),
                    source.kind,
                    str(source.source_id),
                    state if source.kind in {"initiative", "task"} else record.revision,
                ]
            ).encode()
        ).hexdigest()
        source = source.model_copy(update={"revision": record.revision})
        notification = Notification(
            origin_id=session.principal_id,
            recipient=policy.recipient,
            source=source,
            policy_id=policy.policy_id,
            policy_revision=policy.revision,
            correlation_id=corr,
            causation_id=source.source_id,
            category=category,
            priority=policy.priority,
            classification="shared"
            if scope and scope.kind in {"shared", "household"}
            else "private",
            content=content,
            created_at=now,
            expires_at=now + timedelta(seconds=policy.expires_seconds),
            deduplication_key=key,
        )
        channels = ["local-inbox", *(c for c in policy.channels if c in preference.channels)]
        deliveries = tuple(
            NotificationDelivery(
                notification_id=notification.notification_id,
                recipient_id=policy.recipient.principal_id,
                channel=self.channels[c].metadata,
                idempotency_key=f"notification:{notification.notification_id}:{policy.recipient.principal_id}:{c}",
                due_at=now,
                state="DELIVERED" if c == "local-inbox" else "PENDING",
                provider_message_ref=f"inbox:{notification.notification_id}"
                if c == "local-inbox"
                else None,
                reason_code="local_commit" if c == "local-inbox" else "accepted",
            )
            for c in channels
        )
        # Local inbox channel delivery is the admission commit itself, not a second send.
        effective = policy.model_copy(
            update={"cooldown_seconds": max(policy.cooldown_seconds, preference.cooldown_seconds)}
        )
        return self._store._admit(
            notification,
            deliveries,
            effective,
            session.session_id,
            now=now,
            receipt=receipt,
            suppression=suppression,
            access=self._access,
        )

    def bridge(self, *, session_token, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise NotificationError("notification_limit_invalid")
        results = []
        with self._request(session_token, "notification.bridge") as session:
            self._require(session, session_token, "notifications.publish")
            for policy in self._store._policies(session.principal_id, access=self._access):
                if not policy.enabled:
                    continue
                for sequence, encoded in self._store._sources(
                    policy, limit=limit, access=self._access
                ):
                    history = decode_contract(encoded)
                    kind = policy.source_kind
                    # Intermediate lifecycle events are consumed, not announced as completed.
                    wanted = (
                        history.state
                        in {"PROPOSED", "AWAITING_APPROVAL", "COMPLETED", "FAILED", "NEEDS_REVIEW"}
                        if kind == "initiative"
                        else history.state in {"COMPLETED", "FAILED", "NEEDS_REVIEW"}
                    )
                    if not wanted or (kind == "task" and history.execution_guard_ref is not None):
                        self._store._admit(
                            None,
                            (),
                            policy,
                            session.session_id,
                            now=self.clock(),
                            receipt=sequence,
                            suppression="irrelevant",
                            access=self._access,
                        )
                        continue
                    source_id = history.initiative_id if kind == "initiative" else history.task_id
                    # Resolve current record after a restart; old approval intent never revives.
                    try:
                        current = (
                            self.initiatives.get(source_id, session_token=session_token)
                            if kind == "initiative"
                            else self.tasks.store.get(source_id)
                        )
                        result = self._create(
                            policy,
                            NotificationSource(
                                kind=kind,
                                source_id=source_id,
                                revision=current.revision,
                            ),
                            session,
                            session_token,
                            receipt=sequence,
                        )
                        if result is not None:
                            results.append(result)
                    except (NotificationError, InitiativeError, MemoryError, WorldError) as exc:
                        if getattr(exc, "code", "") not in {
                            "notification_unavailable",
                            "initiative_unavailable",
                            "memory_unavailable",
                            "world_unavailable",
                            "initiative_access_denied",
                            "access_denied",
                        }:
                            raise
                        self._store._admit(
                            None,
                            (),
                            policy,
                            session.session_id,
                            now=self.clock(),
                            receipt=sequence,
                            suppression="source_unavailable",
                            access=self._access,
                        )
        return tuple(dict((r.notification_id, r) for r in results).values())

    def get(self, notification_id, *, session_token):
        with self._request(session_token, "notification.get") as session:
            self._require(session, session_token, "notifications.read")
            record = self._store._get(notification_id, session.principal_id, access=self._access)
            self._visible(record, session, session_token)
            return record

    def list(self, *, session_token, limit=20, after=0, text=None):
        """Bounded scoped page; hidden dependencies can produce a short page.

        The returned next cursor advances across examined recipient-owned records,
        so callers can reach later authorized items without exposing global counts.
        """
        if type(limit) is not int or not 1 <= limit <= 100 or type(after) is not int or after < 0:
            raise NotificationError("notification_limit_invalid")
        if text is not None and (not isinstance(text, str) or len(text) > 100):
            raise NotificationError("notification_validation_failure")
        with self._request(session_token, "notification.list") as session:
            self._require(session, session_token, "notifications.read")
            rows = self._store._list(
                session.principal_id,
                limit=limit,
                after=after,
                text=text.casefold() if text else None,
                access=self._access,
            )
            visible = []
            for _, record in rows:
                try:
                    self._visible(record, session, session_token)
                    visible.append(record)
                except NotificationError as exc:
                    if exc.code != "notification_unavailable":
                        raise
            return tuple(visible), rows[-1][0] if rows else after

    def deliveries(self, notification_id, *, session_token):
        with self._request(session_token, "notification.deliveries") as session:
            self.get(notification_id, session_token=session_token)
            return self._store._deliveries(
                notification_id, session.principal_id, access=self._access
            )

    def source_context(self, notification_id, *, session_token):
        with self._request(session_token, "notification.source") as session:
            record = self.get(notification_id, session_token=session_token)
            source, _, _, state, _, _ = self._visible(record, session, session_token)
            # Minimal verified state, no general-purpose raw task/memory data endpoint.
            return {
                "kind": record.source.kind,
                "source_id": str(record.source.source_id),
                "revision": source.revision,
                "state": state,
            }

    def interact(self, notification_id, operation, *, expected_revision, session_token):
        with self._request(session_token, "notification.interaction") as session:
            self._require(session, session_token, "notifications.interact")
            record = self.get(notification_id, session_token=session_token)
            if type(expected_revision) is not int or record.revision != expected_revision:
                raise NotificationError("notification_revision_conflict")
            transitions = {
                "UNREAD": {"read": "READ", "acknowledge": "ACKNOWLEDGED", "dismiss": "DISMISSED"},
                "READ": {"unread": "UNREAD", "acknowledge": "ACKNOWLEDGED", "dismiss": "DISMISSED"},
                "ACKNOWLEDGED": {"dismiss": "DISMISSED"},
                "DISMISSED": {},
                "EXPIRED": {},
            }
            state = transitions[record.inbox_state].get(operation)
            if (
                operation == "expire"
                and self.clock() >= record.expires_at
                and record.inbox_state != "EXPIRED"
            ):
                state = "EXPIRED"
            elif self.clock() >= record.expires_at:
                raise NotificationError("notification_expired")
            if state is None:
                raise NotificationError("notification_interaction_invalid")
            interaction = NotificationInteraction(
                notification_id=record.notification_id,
                principal_id=session.principal_id,
                session_id=session.session_id,
                expected_revision=expected_revision,
                interaction_type=operation,
                occurred_at=self.clock(),
                correlation_id=record.correlation_id,
            )
            updated = record.model_copy(
                update={"revision": record.revision + 1, "inbox_state": state, "audit_id": uuid4()}
            )
            self._store._interact(record, updated, interaction, access=self._access)
            return updated

    def approval(self, notification_id, *, session_token, expected_revision, deny=False):
        with self._request(session_token, "notification.approval"):
            record = self.get(notification_id, session_token=session_token)
            if (
                type(expected_revision) is not int
                or record.revision != expected_revision
                or record.content.approval_reference is None
            ):
                raise NotificationError("notification_approval_invalid")
            if self.clock() >= record.expires_at or record.inbox_state in {"EXPIRED", "DISMISSED"}:
                raise NotificationError("notification_expired")
            initiative_id = record.content.approval_reference
            if deny:
                return self.initiatives.deny(initiative_id, session_token=session_token)
            approved = self.initiatives.approve(initiative_id, session_token=session_token)
            if approved.state == "APPROVED":
                return self.initiatives.execute(initiative_id, session_token=session_token)
            return approved

    def _change(self, delivery, session, state, reason, **updates):
        new = delivery.model_copy(
            update={
                "revision": delivery.revision + 1,
                "state": state,
                "reason_code": reason,
                **updates,
            }
        )
        return self._store._save_delivery(delivery, new, session.session_id, access=self._access)

    def _delivery_authority(self, delivery, session, token):
        self._require(session, token, "notifications.read", "notifications.deliver")
        notification = self._store._get(
            delivery.notification_id, session.principal_id, access=self._access
        )
        self._visible(notification, session, token)
        policy = self._policy_for(notification)
        self._active(notification.origin_id)
        if not self.permissions.current_grants(notification.origin_id, ("notifications.publish",)):
            raise NotificationError("notification_origin_revoked")
        if policy.revision != notification.policy_revision:
            raise NotificationError("notification_policy_changed")
        if self.clock() >= notification.expires_at or notification.inbox_state in {
            "EXPIRED",
            "DISMISSED",
        }:
            raise NotificationError("notification_expired")
        metadata = self.channels.get(delivery.channel.channel_id)
        if metadata is None or metadata.metadata != delivery.channel:
            raise NotificationError("notification_channel_changed")
        preference = self._store._preference(session.principal_id, access=self._access)
        if (
            delivery.channel.channel_id not in policy.channels
            or delivery.channel.channel_id not in preference.channels
            or notification.category not in preference.categories
            or PRIORITIES[notification.priority] < PRIORITIES[preference.minimum_priority]
        ):
            raise NotificationError("notification_opt_out")
        if quiet(preference, self.clock()) and not (
            policy.urgent_quiet_override and notification.priority == "URGENT"
        ):
            raise NotificationError("notification_quiet_hours")
        return notification, policy, preference

    def _task_guard(self, task, token):
        with self._request(token, "notification.task_guard") as session:
            try:
                delivery_id = UUID(task.execution_guard_ref.split(":", 1)[1])
            except (ValueError, AttributeError, IndexError):
                raise NotificationError("notification_task_binding") from None
            delivery = self._store._delivery(delivery_id, session.principal_id, access=self._access)
            if (
                delivery is None
                or delivery.state != "SENDING"
                or not delivery.attempts
                or delivery.attempts[-1].task_id != task.task_id
                or task.owner_id != session.principal_id
                or len(task.steps) != 1
                or task.steps[0].arguments != {"delivery_id": str(delivery_id)}
                or task.steps[0].capability_id
                != f"notification.deliver:{delivery.channel.channel_id}"
            ):
                raise NotificationError("notification_task_binding")
            notification, policy, _ = self._delivery_authority(delivery, session, token)
            if notification.correlation_id != task.correlation_id:
                raise NotificationError("notification_task_binding")
            attempt = delivery.attempts[-1]
            if attempt.admitted_at is None:
                self._store._limits(delivery, policy, self.clock(), access=self._access)
                attempt = attempt.model_copy(update={"admitted_at": self.clock()})
                self._change(
                    delivery,
                    session,
                    "SENDING",
                    "send_admitted",
                    attempts=(*delivery.attempts[:-1], attempt),
                )

    def _send_provider(self, channel_id, delivery_id):
        # Invoked only by the executor after the required task-family guard checkpoint.
        # This infrastructure hook is not an untrusted API; no token/recipient argument.
        delivery, notification = self._store._prepared(delivery_id, access=self._access)
        if (
            delivery is None
            or delivery.state != "SENDING"
            or delivery.channel.channel_id != channel_id
        ):
            raise NotificationError("notification_task_binding")
        request = NotificationDeliveryRequest(
            delivery_id=delivery_id,
            notification=notification,
            idempotency_key=delivery.idempotency_key,
        )
        result = self.channels[channel_id].send(request)
        # Never accept arbitrary success/recipient metadata from a malformed adapter.
        result = decode_notification(result.model_dump_json())
        if not isinstance(result, NotificationDeliveryResult):
            raise NotificationError("notification_channel_result_invalid")
        return result.model_dump(mode="json")

    def _outcome(self, delivery_id, task, session):
        delivery = self._store._delivery(delivery_id, session.principal_id, access=self._access)
        if delivery.state in TERMINAL:
            return delivery
        attempt = delivery.attempts[-1]
        result = None
        if task.state == "COMPLETED" and task.steps[0].result.status == "success":
            try:
                result = decode_notification(json.dumps(task.steps[0].result.output))
            except ValueError:
                result = None
        if not isinstance(result, NotificationDeliveryResult):
            result = NotificationDeliveryResult(outcome="unknown", error_code="unknown")
        state = "NEEDS_REVIEW"
        due_at = delivery.due_at
        policy = self._store._policy(
            self._store._get(
                delivery.notification_id, session.principal_id, access=self._access
            ).policy_id,
            access=self._access,
        )
        if result.outcome == "delivered":
            state = "DELIVERED"
        elif result.outcome == "permanent_failure":
            state = "FAILED"
        elif result.outcome == "temporary_failure" or (
            result.outcome == "unknown"
            and delivery.channel.supports_idempotency
            and task.state != "NEEDS_REVIEW"
        ):
            if len(delivery.attempts) < policy.max_attempts:
                state, due_at = "RETRY_WAIT", self.clock() + timedelta(seconds=policy.retry_seconds)
            else:
                state = "FAILED" if result.outcome == "temporary_failure" else "NEEDS_REVIEW"
        # A denied checkpoint is proven unsent, not an ambiguous external effect.
        if (
            task.state in {"FAILED", "CANCELLED"}
            and task.steps[0].attempts
            and not any(
                a.state == "RUNNING"
                or a.retry_safe
                or a.result
                and a.result.error
                and a.result.error.code
                not in {"permission_missing", "task_guard_required", "checkpoint_unavailable"}
                for a in task.steps[0].attempts
            )
        ):
            state = "CANCELLED"
        finished = attempt.model_copy(
            update={
                "state": "FINISHED" if result.outcome != "unknown" else "OUTCOME_UNKNOWN",
                "finished_at": max(self.clock(), attempt.started_at),
                "result": result,
            }
        )
        return self._change(
            delivery,
            session,
            state,
            result.error_code or "transport_delivered",
            attempts=(*delivery.attempts[:-1], finished),
            due_at=due_at,
            provider_message_ref=result.provider_message_ref,
        )

    def deliver(self, delivery_id, *, session_token, after_checkpoint=None):
        # Claim before the host security lock; a second worker returns busy promptly.
        try:
            with self._store._claim(delivery_id, access=self._access):
                with self._request(session_token, "notification.deliver") as session:
                    self._require(session, session_token, "notifications.read")
                    delivery = self._store._delivery(
                        delivery_id, session.principal_id, access=self._access
                    )
                    if delivery is None:
                        raise NotificationError("notification_unavailable")
                    if delivery.state in TERMINAL:
                        self.get(delivery.notification_id, session_token=session_token)
                        return delivery
                    # Finalize known task outcomes without replay, even after send revocation.
                    if delivery.state == "SENDING":
                        task = self.tasks.store.get(delivery.attempts[-1].task_id)
                        if task is None:
                            raise NotificationError("notification_task_binding")
                        if task.state in {"COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"}:
                            return self._outcome(delivery_id, task, session)
                    try:
                        notification, policy, preference = self._delivery_authority(
                            delivery, session, session_token
                        )
                    except NotificationError as exc:
                        if delivery.state == "SENDING":
                            # Existing TaskManager classifies an interrupted attempt before a retry.
                            task = self.tasks.run(
                                delivery.attempts[-1].task_id,
                                session_token=session_token,
                                recovery=True,
                            ).task
                            if task and task.state in {
                                "COMPLETED",
                                "FAILED",
                                "CANCELLED",
                                "NEEDS_REVIEW",
                            }:
                                return self._outcome(delivery_id, task, session)
                            # Recovered intent may be known unsent, or a safe provider's
                            # earlier outcome may still be unknown after access changes.
                            current_task = self.tasks.store.get(delivery.attempts[-1].task_id)
                            known_denial = {
                                "notification_access_denied",
                                "notification_unavailable",
                                "notification_opt_out",
                                "notification_policy_changed",
                                "notification_expired",
                                "notification_channel_changed",
                                "notification_origin_revoked",
                                "notification_authority_unavailable",
                            }
                            if exc.code in known_denial and current_task is not None:
                                uncertain = bool(current_task.steps[0].attempts)
                                if not uncertain:
                                    self.tasks.cancel(
                                        current_task.task_id, session_token=session_token
                                    )
                                finish = delivery.attempts[-1].model_copy(
                                    update={
                                        "state": "OUTCOME_UNKNOWN" if uncertain else "FINISHED",
                                        "finished_at": max(
                                            self.clock(), delivery.attempts[-1].started_at
                                        ),
                                        "result": NotificationDeliveryResult(
                                            outcome="unknown" if uncertain else "permanent_failure",
                                            error_code="unknown"
                                            if uncertain
                                            else "permanent_failure",
                                            evidence="local_commit",
                                        ),
                                    }
                                )
                                return self._change(
                                    delivery,
                                    session,
                                    "NEEDS_REVIEW" if uncertain else "CANCELLED",
                                    exc.code,
                                    attempts=(*delivery.attempts[:-1], finish),
                                )
                            raise
                        if exc.code == "notification_quiet_hours":
                            preference = self._store._preference(
                                session.principal_id, access=self._access
                            )
                            return self._change(
                                delivery,
                                session,
                                "RETRY_WAIT",
                                exc.code,
                                due_at=next_allowed(preference, self.clock()),
                            )
                        return self._change(
                            delivery,
                            session,
                            "EXPIRED" if exc.code == "notification_expired" else "CANCELLED",
                            exc.code,
                        )
                    if delivery.state != "SENDING":
                        if self.clock() < delivery.due_at:
                            return delivery
                        if delivery.state != "READY":
                            delivery = self._change(delivery, session, "READY", "eligible")
                        if len(delivery.attempts) >= policy.max_attempts:
                            return self._change(
                                delivery, session, "FAILED", "notification_attempt_limit"
                            )
                        number = len(delivery.attempts) + 1
                        task = self.tasks.submit(
                            (
                                TaskStep(
                                    step_id="deliver",
                                    capability_id=f"notification.deliver:{delivery.channel.channel_id}",
                                    arguments={"delivery_id": str(delivery_id)},
                                ),
                            ),
                            session_token=session_token,
                            idempotency_key=f"notification:{delivery_id}:{number}",
                            correlation_id=notification.correlation_id,
                            execution_guard_ref=f"notification:{delivery_id}",
                        )
                        attempt = NotificationAttempt(
                            delivery_id=delivery_id,
                            number=number,
                            task_id=task.task_id,
                            provider_id=delivery.channel.provider_id,
                            idempotency_key=delivery.idempotency_key,
                            started_at=self.clock(),
                        )
                        delivery = self._change(
                            delivery,
                            session,
                            "SENDING",
                            "attempt_started",
                            attempts=(*delivery.attempts, attempt),
                        )
                    result = self.tasks.run(
                        delivery.attempts[-1].task_id,
                        session_token=session_token,
                        recovery=True,
                        after_checkpoint=after_checkpoint,
                    )
                    if result.task is not None and result.task.state in {
                        "COMPLETED",
                        "FAILED",
                        "CANCELLED",
                        "NEEDS_REVIEW",
                        "WAITING",
                    }:
                        return self._outcome(delivery_id, result.task, session)
                    # Execution-time budgets can defer without invoking an adapter.
                    if result.error and result.error.code == "notification_rate_limit":
                        current = self._store._delivery(
                            delivery_id, session.principal_id, access=self._access
                        )
                        attempt = current.attempts[-1].model_copy(
                            update={
                                "state": "FINISHED",
                                "finished_at": self.clock(),
                                "result": NotificationDeliveryResult(
                                    outcome="temporary_failure",
                                    error_code="temporary_failure",
                                    retryable=True,
                                    evidence="local_commit",
                                ),
                            }
                        )
                        self.tasks.cancel(attempt.task_id, session_token=session_token)
                        return self._change(
                            current,
                            session,
                            "RETRY_WAIT",
                            "notification_rate_limit",
                            due_at=self.clock() + timedelta(seconds=policy.rate_window_seconds),
                            attempts=(*current.attempts[:-1], attempt),
                        )
                    raise NotificationError(
                        result.error.code if result.error else "notification_delivery_incomplete"
                    )
        except StorageError as exc:
            raise NotificationError(
                "notification_unavailable" if exc.code == "notification_busy" else exc.code
            ) from None

    def process(self, *, session_token, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise NotificationError("notification_limit_invalid")
        with self._request(session_token, "notification.process") as session:
            self._require(session, session_token, "notifications.read", "notifications.deliver")
            pending = self._store._pending(session.principal_id, limit=limit, access=self._access)
        return tuple(self.deliver(d.delivery_id, session_token=session_token) for d in pending)

    def recover(self, *, session_token, limit=20):
        return self.process(session_token=session_token, limit=limit)

    def context(self, text, *, session_token, limit=3):
        if not is_notification_question(text):
            return ()
        if not 1 <= limit <= 3:
            raise NotificationError("notification_limit_invalid")
        words = re.findall(r"[\w-]+", text.casefold())[:32]
        stop = set(
            (
                "do i have any new notifications notification what happened "
                "while was away why did you "
                "notify me about the inbox show my please which has that "
                "problem been resolved current "
                "and for unread read acknowledge actions are waiting approval"
            ).split()
        )
        terms = tuple(dict.fromkeys(w for w in words if len(w) >= 3 and w not in stop))[:4]
        with self._request(session_token, "notification.context") as session:
            self._require(session, session_token, "notifications.read")
            records = self._store._context(
                session.principal_id,
                terms=terms,
                unread="new" in words or "unread" in words,
                access=self._access,
            )
            summaries = []
            for record in records:
                try:
                    self._visible(record, session, session_token)
                except NotificationError as exc:
                    if exc.code == "notification_unavailable":
                        continue
                    raise
                source = self.source_context(record.notification_id, session_token=session_token)
                if (
                    "waiting for my approval" in text.casefold()
                    and source["state"] != "AWAITING_APPROVAL"
                ):
                    continue
                summaries.append(
                    NotificationContext(
                        notification_id=record.notification_id,
                        revision=record.revision,
                        title=record.content.title,
                        text=record.content.text,
                        inbox_state=(
                            "EXPIRED" if self.clock() >= record.expires_at else record.inbox_state
                        ),
                        delivery_states=tuple(
                            d.state
                            for d in self.deliveries(
                                record.notification_id, session_token=session_token
                            )
                        ),
                        source=record.source,
                        source_state=source["state"],
                        source_revision=source["revision"],
                    )
                )
                if len(summaries) == limit:
                    break
        return tuple(summaries)
