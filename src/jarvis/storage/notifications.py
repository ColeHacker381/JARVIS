"""Private notification adapter. Admission, receipts and cursors commit atomically."""

from contextlib import contextmanager
from datetime import UTC, timedelta
from uuid import uuid4

from jarvis.core.contracts import AuditRecord, Event
from jarvis.core.notification_records import (
    TRANSITIONS,
    Notification,
    NotificationDelivery,
    NotificationPolicy,
    NotificationPreference,
    decode_notification,
)
from jarvis.storage.errors import StorageError
from jarvis.storage.sqlite import _append_audit, _append_event, _file_lock
from jarvis.tasks import TaskBusyError


def timestamp(value):
    return value.astimezone(UTC).isoformat()


class SQLiteNotificationStore:
    def __init__(self, database, identities, audit):
        if database.path != identities.database.path or database.path != audit.database.path:
            raise ValueError("notification storage must share authority and journal")
        self.database, self.identities, self.audit = database, identities, audit
        self.__access = None

    def _bind(self):
        if self.__access is not None:
            raise ValueError("store already bound")
        self.__access = object()
        return self.__access

    def _guard(self, access):
        if access is None or access is not self.__access:
            raise StorageError("notification_access_denied")

    @staticmethod
    def _decode(row, model):
        try:
            value = decode_notification(row["data"])
            if not isinstance(value, model):
                raise ValueError()
            for key in ("notification_id", "delivery_id", "policy_id", "revision", "state"):
                if key in row.keys() and str(getattr(value, key)) != str(row[key]):
                    raise ValueError()
            if "recipient_id" in row.keys():
                principal = (
                    value.recipient.principal_id
                    if isinstance(value, (Notification, NotificationPolicy))
                    else value.recipient_id
                    if isinstance(value, NotificationDelivery)
                    else value.principal_id
                )
                if str(principal) != row["recipient_id"]:
                    raise ValueError()
            if isinstance(value, Notification) and (
                row["deduplication_key"] != value.deduplication_key
                or row["created_at"] != timestamp(value.created_at)
            ):
                raise ValueError()
            if (
                isinstance(value, NotificationDelivery)
                and row["channel_id"] != value.channel.channel_id
            ):
                raise ValueError()
            return value
        except (ValueError, TypeError, AttributeError, KeyError):
            raise StorageError("notification_record_invalid") from None

    def _policy(self, policy_id, *, access):
        self._guard(access)
        with self.database.transaction() as c:
            row = c.execute(
                "SELECT * FROM notification_policies WHERE policy_id=?", (policy_id,)
            ).fetchone()
            return self._decode(row, NotificationPolicy) if row else None

    def _policies(self, origin_id, *, access):
        self._guard(access)
        with self.database.transaction() as c:
            rows = c.execute(
                "SELECT * FROM notification_policies WHERE origin_id=? ORDER BY policy_id LIMIT 21",
                (str(origin_id),),
            ).fetchall()
            if len(rows) > 20:
                raise StorageError("notification_policy_limit")
            return tuple(self._decode(r, NotificationPolicy) for r in rows)

    def _configure(self, value, expected, audit, *, access):
        self._guard(access)
        policy = isinstance(value, NotificationPolicy)
        table, key, identity = (
            ("notification_policies", "policy_id", value.policy_id)
            if policy
            else ("notification_preferences", "recipient_id", str(value.principal_id))
        )
        with self.database.transaction(write=True) as c:
            row = c.execute(f"SELECT * FROM {table} WHERE {key}=?", (identity,)).fetchone()
            if row:
                old = self._decode(row, type(value))
                if (
                    old.revision != expected
                    or value.revision != old.revision + 1
                    or (
                        policy
                        and (
                            value.origin_id != old.origin_id
                            or value.recipient != old.recipient
                            or value.source_kind != old.source_kind
                            or value.source_id != old.source_id
                        )
                    )
                ):
                    raise StorageError("notification_revision_conflict")
                c.execute(
                    f"UPDATE {table} SET revision=?,data=? WHERE {key}=?",
                    (value.revision, value.model_dump_json(), identity),
                )
            else:
                if expected is not None or value.revision != 0:
                    raise StorageError("notification_revision_conflict")
                if policy:
                    count = c.execute(
                        "SELECT count(*) FROM notification_policies WHERE origin_id=?",
                        (str(value.origin_id),),
                    ).fetchone()[0]
                    if count >= 20:
                        raise StorageError("notification_policy_limit")
                    c.execute(
                        "INSERT INTO notification_policies VALUES(?,?,?,?,?)",
                        (
                            identity,
                            str(value.origin_id),
                            str(value.recipient.principal_id),
                            value.revision,
                            value.model_dump_json(),
                        ),
                    )
                    c.execute("INSERT INTO notification_cursors VALUES(?,0)", (identity,))
                else:
                    c.execute(
                        "INSERT INTO notification_preferences VALUES(?,?,?)",
                        (identity, value.revision, value.model_dump_json()),
                    )
            _append_audit(c, audit)
        return value

    def _preference(self, principal_id, *, access):
        self._guard(access)
        with self.database.transaction() as c:
            row = c.execute(
                "SELECT * FROM notification_preferences WHERE recipient_id=?", (str(principal_id),)
            ).fetchone()
            return (
                self._decode(row, NotificationPreference)
                if row
                else NotificationPreference(principal_id=principal_id)
            )

    def _get(self, notification_id, recipient_id, *, access):
        self._guard(access)
        with self.database.transaction() as c:
            row = c.execute(
                "SELECT * FROM notifications WHERE notification_id=? AND recipient_id=?",
                (str(notification_id), str(recipient_id)),
            ).fetchone()
            return self._decode(row, Notification) if row else None

    def _list(self, recipient_id, *, limit, after=None, text=None, access):
        self._guard(access)
        with self.database.transaction() as c:
            rows = c.execute(
                "WITH owned AS (SELECT *,ROW_NUMBER() OVER (ORDER BY sequence) "
                "AS recipient_cursor FROM notifications WHERE recipient_id=?) "
                "SELECT * FROM owned WHERE recipient_cursor>? "
                "AND (? IS NULL OR instr(lower(json_extract(data,'$.content.text')),?)>0 "
                "OR instr(lower(json_extract(data,'$.content.title')),?)>0) ORDER "
                "BY sequence LIMIT ?",
                (str(recipient_id), after or 0, text, text, text, limit),
            ).fetchall()
            return tuple((r["recipient_cursor"], self._decode(r, Notification)) for r in rows)

    def _context(self, recipient_id, *, terms=(), unread=False, access):
        self._guard(access)
        with self.database.transaction() as c:
            where = ""
            args = [str(recipient_id), int(unread)]
            if terms:
                where = (
                    " AND ("
                    + " OR ".join(
                        (
                            "instr(lower(json_extract(data,'$.content.text')),?)>0 OR "
                            "instr(lower(json_extract(data,'$.content.title')),?)>0"
                        )
                        for _ in terms
                    )
                    + ")"
                )
                args.extend(term for term in terms for _ in range(2))
            rows = c.execute(
                (
                    "SELECT * FROM notifications WHERE recipient_id=? "
                    "AND (?=0 OR json_extract(data,'$.inbox_state')='UNREAD')"
                )
                + where
                + " ORDER BY sequence DESC LIMIT 20",
                args,
            ).fetchall()
            return tuple(self._decode(r, Notification) for r in rows)

    def _deliveries(self, notification_id, recipient_id, *, access):
        self._guard(access)
        with self.database.transaction() as c:
            rows = c.execute(
                "SELECT * FROM notification_deliveries WHERE notification_id=? AND"
                " recipient_id=? ORDER BY channel_id LIMIT 5",
                (str(notification_id), str(recipient_id)),
            ).fetchall()
            return tuple(self._decode(r, NotificationDelivery) for r in rows)

    def _delivery(self, delivery_id, recipient_id, *, access):
        self._guard(access)
        with self.database.transaction() as c:
            row = c.execute(
                "SELECT * FROM notification_deliveries WHERE delivery_id=? AND recipient_id=?",
                (str(delivery_id), str(recipient_id)),
            ).fetchone()
            return self._decode(row, NotificationDelivery) if row else None

    def _prepared(self, delivery_id, *, access):
        """Trusted executor-only adapter hook, after the task guard authenticated owner."""
        self._guard(access)
        with self.database.transaction() as c:
            row = c.execute(
                "SELECT * FROM notification_deliveries WHERE delivery_id=?", (str(delivery_id),)
            ).fetchone()
            if row is None:
                raise StorageError("notification_unavailable")
            delivery = self._decode(row, NotificationDelivery)
            notification = self._get(delivery.notification_id, delivery.recipient_id, access=access)
            return delivery, notification

    def _pending(self, recipient_id, *, limit, access):
        self._guard(access)
        with self.database.transaction() as c:
            rows = c.execute(
                "SELECT d.* FROM notification_deliveries d JOIN notifications n US"
                "ING(notification_id) "
                "WHERE d.recipient_id=? AND d.state IN ('PENDING','READY','RETRY_WAIT','SENDING') "
                "ORDER BY CASE json_extract(n.data,'$.priority') WHEN 'URGENT' THEN 0 "
                "WHEN 'HIGH' THEN 1 "
                "WHEN 'NORMAL' THEN 2 ELSE 3 END,d.due_at,d.delivery_id LIMIT ?",
                (str(recipient_id), limit),
            ).fetchall()
            return tuple(self._decode(r, NotificationDelivery) for r in rows)

    def _journal(self, c, notification, action, session_id, *, reason="recorded", delivery=None):
        event = Event(
            event_type=action,
            principal_id=notification.recipient.principal_id,
            source="jarvis.notifications",
            correlation_id=notification.correlation_id,
            causation_id=notification.causation_id,
            payload={
                "schema_version": 1,
                "notification_id": str(notification.notification_id),
                "revision": notification.revision,
                "reason_code": reason,
                **(
                    {
                        "delivery_id": str(delivery.delivery_id),
                        "state": delivery.state,
                        "attempt_id": str(delivery.attempts[-1].attempt_id)
                        if delivery.attempts
                        else None,
                    }
                    if delivery is not None
                    else {}
                ),
            },
        )
        _append_event(c, event)
        _append_audit(
            c,
            AuditRecord(
                audit_id=notification.audit_id,
                principal_id=notification.recipient.principal_id,
                session_id=session_id,
                correlation_id=notification.correlation_id,
                action=action,
                outcome="failed"
                if delivery and delivery.state in {"FAILED", "NEEDS_REVIEW"}
                else "succeeded",
                reason_code=reason,
                task_id=delivery.attempts[-1].task_id if delivery and delivery.attempts else None,
            ),
        )

    def _sources(self, policy, *, limit, access):
        """Only origin-owned task/initiative journal pages, never global events."""
        self._guard(access)
        if policy.source_kind not in {"initiative", "task"}:
            return ()
        prefix, table, key = (
            ("initiative", "initiatives", "initiative_id")
            if policy.source_kind == "initiative"
            else ("task", "tasks", "task_id")
        )
        with self.database.transaction() as c:
            cursor = c.execute(
                "SELECT sequence FROM notification_cursors WHERE policy_id=?", (policy.policy_id,)
            ).fetchone()[0]
            rows = c.execute(
                f"SELECT h.sequence,h.data FROM {prefix}_history h JOIN {table} s USING({key}) "
                f"WHERE s.owner_id=? AND h.sequence>? AND (? IS NULL OR s.{key}=?) "
                "ORDER BY h.sequence LIMIT ?",
                (
                    str(policy.origin_id),
                    cursor,
                    str(policy.source_id) if policy.source_id else None,
                    str(policy.source_id) if policy.source_id else None,
                    limit,
                ),
            ).fetchall()
            return tuple((r[0], r[1]) for r in rows)

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
    ):
        self._guard(access)
        with self.database.transaction(write=True) as c:
            if receipt is not None:
                sequence = receipt
                if c.execute(
                    "SELECT 1 FROM notification_receipts WHERE policy_id=? AND sequence=?",
                    (policy.policy_id, sequence),
                ).fetchone():
                    return None
            existing = None
            if notification is not None:
                row = c.execute(
                    "SELECT * FROM notifications WHERE recipient_id=? AND deduplication_key=?",
                    (str(notification.recipient.principal_id), notification.deduplication_key),
                ).fetchone()
                if row:
                    existing = self._decode(row, Notification)
                    suppression = "duplicate"
                if not suppression:
                    cooldown = c.execute(
                        "SELECT 1 FROM notifications WHERE recipient_id=? AND policy_id=? AND "
                        "julianday(created_at)>julianday(?) LIMIT 1",
                        (
                            str(notification.recipient.principal_id),
                            policy.policy_id,
                            timestamp(now - timedelta(seconds=policy.cooldown_seconds)),
                        ),
                    ).fetchone()
                    count = c.execute(
                        "SELECT count(*) FROM notification_deliveries WHERE recipient_id=? AND "
                        "state IN ('PENDING','READY','RETRY_WAIT','SENDING')",
                        (str(notification.recipient.principal_id),),
                    ).fetchone()[0]
                    if cooldown:
                        suppression = "source_cooldown"
                    elif (
                        count
                        + sum(
                            d.state
                            not in {"DELIVERED", "FAILED", "EXPIRED", "NEEDS_REVIEW", "CANCELLED"}
                            for d in deliveries
                        )
                        > policy.queue_limit
                    ):
                        suppression = "queue_limit"
                if not suppression:
                    c.execute(
                        "INSERT INTO notifications(notification_id,recipient_id,policy_id,"
                        "revision,deduplication_key,created_at,data) VALUES(?,?,?,?,?,?,?)",
                        (
                            str(notification.notification_id),
                            str(notification.recipient.principal_id),
                            notification.policy_id,
                            notification.revision,
                            notification.deduplication_key,
                            timestamp(notification.created_at),
                            notification.model_dump_json(),
                        ),
                    )
                    for d in deliveries:
                        c.execute(
                            "INSERT INTO notification_deliveries VALUES(?,?,?,?,?,?,?,?)",
                            (
                                str(d.delivery_id),
                                str(d.notification_id),
                                str(d.recipient_id),
                                d.channel.channel_id,
                                d.state,
                                d.revision,
                                timestamp(d.due_at),
                                d.model_dump_json(),
                            ),
                        )
                    self._journal(c, notification, "notification.created", session_id)
                    existing = notification
            if suppression:
                c.execute(
                    "INSERT INTO notification_suppressions VALUES(?,?,?,?,?)",
                    (
                        str(uuid4()),
                        policy.policy_id,
                        str(policy.recipient.principal_id),
                        timestamp(now),
                        suppression,
                    ),
                )
                _append_audit(
                    c,
                    AuditRecord(
                        principal_id=policy.origin_id,
                        session_id=session_id,
                        correlation_id=notification.correlation_id if notification else uuid4(),
                        action="notification.suppressed",
                        outcome="denied",
                        reason_code=suppression,
                    ),
                )
            if receipt is not None:
                c.execute(
                    "INSERT INTO notification_receipts VALUES(?,?,?,?)",
                    (
                        policy.policy_id,
                        receipt,
                        str(existing.notification_id) if existing else None,
                        suppression or "accepted",
                    ),
                )
                c.execute(
                    "UPDATE notification_cursors SET sequence=max(sequence,?) WHERE policy_id=?",
                    (receipt, policy.policy_id),
                )
        return existing

    def _update_notification(self, c, old, new, session_id, action, *, delivery=None):
        if new.revision != old.revision + 1:
            raise StorageError("notification_revision_conflict")
        if old.model_dump(
            exclude={"revision", "inbox_state", "delivery_status", "audit_id"}
        ) != new.model_dump(exclude={"revision", "inbox_state", "delivery_status", "audit_id"}):
            raise StorageError("notification_immutable_source")
        changed = c.execute(
            "UPDATE notifications SET revision=?,data=? WHERE notification_id="
            "? AND recipient_id=? AND revision=?",
            (
                new.revision,
                new.model_dump_json(),
                str(new.notification_id),
                str(new.recipient.principal_id),
                old.revision,
            ),
        ).rowcount
        if changed != 1:
            raise StorageError("notification_revision_conflict")
        self._journal(
            c,
            new,
            action,
            session_id,
            reason=delivery.reason_code if delivery else "recorded",
            delivery=delivery,
        )

    def _interact(self, old, new, interaction, *, access):
        self._guard(access)
        with self.database.transaction(write=True) as c:
            row = c.execute(
                "SELECT data FROM notification_interactions WHERE interaction_id=?",
                (str(interaction.interaction_id),),
            ).fetchone()
            if row:
                if decode_notification(row[0]) != interaction:
                    raise StorageError("notification_interaction_conflict")
                return
            self._update_notification(
                c, old, new, interaction.session_id, "notification.interaction"
            )
            c.execute(
                "INSERT INTO notification_interactions VALUES(?,?,?,?,?)",
                (
                    str(interaction.interaction_id),
                    str(old.notification_id),
                    str(interaction.principal_id),
                    timestamp(interaction.occurred_at),
                    interaction.model_dump_json(),
                ),
            )

    def _save_delivery(self, old, new, session_id, *, access):
        self._guard(access)
        new = decode_notification(new.model_dump_json())
        if (
            new.state not in TRANSITIONS[old.state]
            or new.revision != old.revision + 1
            or old.model_dump(
                exclude={
                    "state",
                    "revision",
                    "due_at",
                    "attempts",
                    "reason_code",
                    "provider_message_ref",
                }
            )
            != new.model_dump(
                exclude={
                    "state",
                    "revision",
                    "due_at",
                    "attempts",
                    "reason_code",
                    "provider_message_ref",
                }
            )
        ):
            raise StorageError("notification_transition_invalid")
        if (
            len(new.attempts) < len(old.attempts)
            or new.attempts[: max(0, len(old.attempts) - 1)] != old.attempts[:-1]
        ):
            raise StorageError("notification_attempt_immutable")
        if (
            old.attempts
            and old.attempts[-1].state != "RUNNING"
            and new.attempts[: len(old.attempts)] != old.attempts
        ):
            raise StorageError("notification_attempt_immutable")
        if len(new.attempts) > len(old.attempts):
            if (
                len(new.attempts) != len(old.attempts) + 1
                or new.attempts[:-1] != old.attempts
                or old.state != "READY"
            ):
                raise StorageError("notification_attempt_immutable")
        elif old.attempts:
            mutable = {"state", "admitted_at", "finished_at", "result"}
            if old.attempts[-1].model_dump(exclude=mutable) != new.attempts[-1].model_dump(
                exclude=mutable
            ):
                raise StorageError("notification_attempt_immutable")
            if (
                old.attempts[-1].admitted_at is not None
                and new.attempts[-1].admitted_at != old.attempts[-1].admitted_at
            ):
                raise StorageError("notification_attempt_immutable")
        with self.database.transaction(write=True) as c:
            row = c.execute(
                "SELECT * FROM notification_deliveries WHERE delivery_id=? AND recipient_id=?",
                (str(old.delivery_id), str(old.recipient_id)),
            ).fetchone()
            if row is None or self._decode(row, NotificationDelivery) != old:
                raise StorageError("notification_revision_conflict")
            c.execute(
                "UPDATE notification_deliveries SET revision=?,state=?,due_at=?,da"
                "ta=? WHERE delivery_id=? AND revision=?",
                (
                    new.revision,
                    new.state,
                    timestamp(new.due_at),
                    new.model_dump_json(),
                    str(new.delivery_id),
                    old.revision,
                ),
            )
            for a in new.attempts:
                c.execute(
                    "INSERT INTO notification_attempts VALUES(?,?,?,?,?,?,?) ON CONFLI"
                    "CT(attempt_id) DO UPDATE SET admitted_at=excluded.admitted_at,dat"
                    "a=excluded.data",
                    (
                        str(a.attempt_id),
                        str(new.delivery_id),
                        str(new.recipient_id),
                        new.channel.channel_id,
                        a.number,
                        timestamp(a.admitted_at) if a.admitted_at else None,
                        a.model_dump_json(),
                    ),
                )
            row = c.execute(
                "SELECT * FROM notifications WHERE notification_id=? AND recipient_id=?",
                (str(new.notification_id), str(new.recipient_id)),
            ).fetchone()
            n = self._decode(row, Notification)
            states = [
                r[0]
                for r in c.execute(
                    "SELECT state FROM notification_deliveries WHERE notification_id=?",
                    (str(n.notification_id),),
                )
            ]
            status = (
                "ATTENTION_REQUIRED"
                if any(s in {"FAILED", "NEEDS_REVIEW"} for s in states)
                else "TRANSPORT_DELIVERED"
                if all(s == "DELIVERED" for s in states)
                else "ACCEPTED"
            )
            self._update_notification(
                c,
                n,
                n.model_copy(
                    update={
                        "revision": n.revision + 1,
                        "audit_id": uuid4(),
                        "delivery_status": status,
                    }
                ),
                session_id,
                "notification.delivery",
                delivery=new,
            )
        return new

    def _limits(self, delivery, policy, now, *, access):
        self._guard(access)
        with self.database.transaction() as c:
            cutoff = timestamp(now - timedelta(seconds=policy.rate_window_seconds))
            recipient = c.execute(
                "SELECT count(*) FROM notification_attempts WHERE recipient_id=? A"
                "ND julianday(admitted_at)>julianday(?)",
                (str(delivery.recipient_id), cutoff),
            ).fetchone()[0]
            channel = c.execute(
                "SELECT count(*) FROM notification_attempts WHERE channel_id=? AND"
                " julianday(admitted_at)>julianday(?)",
                (delivery.channel.channel_id, cutoff),
            ).fetchone()[0]
            if recipient >= policy.recipient_rate or channel >= policy.channel_rate:
                raise StorageError("notification_rate_limit")

    @contextmanager
    def _claim(self, delivery_id, *, access):
        self._guard(access)
        try:
            with _file_lock(
                self.database.path.with_name(
                    f"{self.database.path.name}.notification-{delivery_id}.lock"
                )
            ):
                yield
        except TaskBusyError:
            raise StorageError("notification_busy") from None
