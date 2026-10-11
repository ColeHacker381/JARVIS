"""Private initiative persistence. Receipts/checkpoints and accepted intent commit
together."""

from contextlib import contextmanager
from datetime import UTC, timedelta

from jarvis.core.contracts import AuditRecord, Event
from jarvis.core.initiative_records import (
    TRANSITIONS,
    InitiativePolicy,
    InitiativeRecord,
    InitiativeSuppressionRecord,
    decode_initiative,
)
from jarvis.storage.errors import StorageError
from jarvis.storage.sqlite import _append_audit, _append_event, _file_lock
from jarvis.tasks import TaskBusyError


def timestamp(value):
    return value.astimezone(UTC).isoformat()


class SQLiteInitiativeStore:
    def __init__(self, database, identities, audit):
        if database.path != identities.database.path or database.path != audit.database.path:
            raise ValueError("initiative stores must share authority/journal")
        self.database, self.identities, self.audit = database, identities, audit
        self.__access = None

    def _bind(self):
        if self.__access is not None:
            raise ValueError("initiative store already bound")
        self.__access = object()
        return self.__access

    def _guard(self, access):
        if access is not self.__access or access is None:
            raise StorageError("initiative_access_denied")

    @staticmethod
    def _decode(row, model):
        try:
            value = decode_initiative(row["data"])
            if not isinstance(value, model):
                raise ValueError()
            for key in (
                "initiative_id",
                "owner_id",
                "principal_id",
                "correlation_id",
                "revision",
                "state",
                "created_at",
            ):
                if key in row.keys():
                    actual = getattr(value, key)
                    if key == "created_at":
                        actual = timestamp(actual)
                    if str(actual) != str(row[key]):
                        raise ValueError()
            if "policy_id" in row.keys():
                policy_id = (
                    value.proposal.policy_id
                    if isinstance(value, InitiativeRecord)
                    else value.policy_id
                )
                if policy_id != row["policy_id"]:
                    raise ValueError()
            if (
                isinstance(value, InitiativeRecord)
                and value.proposal.deduplication_key != row["deduplication_key"]
            ):
                raise ValueError()
            return value
        except (ValueError, TypeError, AttributeError, KeyError):
            raise StorageError("initiative_record_invalid") from None

    def _policy(self, policy_id, *, access):
        self._guard(access)
        with self.database.transaction() as c:
            row = c.execute(
                "SELECT * FROM initiative_policies WHERE policy_id=?", (policy_id,)
            ).fetchone()
            return self._decode(row, InitiativePolicy) if row else None

    def _policies(self, principal_id, *, access):
        self._guard(access)
        with self.database.transaction() as c:
            rows = c.execute(
                (
                    "SELECT * FROM initiative_policies WHERE principal_id=? ORDER BY "
                    "policy_id LIMIT 21"
                ),
                (str(principal_id),),
            ).fetchall()
            if len(rows) > 20:
                raise StorageError("initiative_policy_limit")
            return tuple(self._decode(row, InitiativePolicy) for row in rows)

    def _configure(self, policy, expected_revision, audit, *, access):
        self._guard(access)
        with self.database.transaction(write=True) as c:
            old = c.execute(
                "SELECT * FROM initiative_policies WHERE policy_id=?", (policy.policy_id,)
            ).fetchone()
            if old is None:
                if expected_revision is not None or policy.revision != 0:
                    raise StorageError("initiative_revision_conflict")
                count = c.execute(
                    "SELECT count(*) FROM initiative_policies WHERE principal_id=?",
                    (str(policy.principal_id),),
                ).fetchone()[0]
                if count >= 20:
                    raise StorageError("initiative_policy_limit")
                c.execute(
                    "INSERT INTO initiative_policies VALUES(?,?,?,?)",
                    (
                        policy.policy_id,
                        str(policy.principal_id),
                        policy.revision,
                        policy.model_dump_json(),
                    ),
                )
                c.execute("INSERT INTO initiative_cursors VALUES(?,0)", (policy.policy_id,))
            else:
                old = self._decode(old, InitiativePolicy)
                if (
                    old.revision != expected_revision
                    or policy.revision != expected_revision + 1
                    or old.principal_id != policy.principal_id
                ):
                    raise StorageError("initiative_revision_conflict")
                c.execute(
                    (
                        "UPDATE initiative_policies SET revision=?,data=? WHERE "
                        "policy_id=? AND revision=?"
                    ),
                    (
                        policy.revision,
                        policy.model_dump_json(),
                        policy.policy_id,
                        expected_revision,
                    ),
                )
            _append_audit(c, audit)
        return policy

    def _cursor(self, policy_id, *, access):
        self._guard(access)
        with self.database.transaction() as c:
            row = c.execute(
                "SELECT sequence FROM initiative_cursors WHERE policy_id=?", (policy_id,)
            ).fetchone()
            if row is None or type(row[0]) is not int or row[0] < 0:
                raise StorageError("initiative_cursor_invalid")
            return row[0]

    def _get(self, initiative_id, owner_id, *, access):
        self._guard(access)
        with self.database.transaction() as c:
            row = c.execute(
                "SELECT * FROM initiatives WHERE initiative_id=? AND owner_id=?",
                (str(initiative_id), str(owner_id)),
            ).fetchone()
            return self._decode(row, InitiativeRecord) if row else None

    def _list(self, owner_id, *, limit, active=False, state=None, terms=None, access):
        self._guard(access)
        conditions = "owner_id=?"
        parameters = [str(owner_id)]
        if state is not None:
            conditions += " AND state=?"
            parameters.append(state)
        if terms:
            slots = ",".join("?" for _ in terms)
            conditions += f" AND (json_extract(data,'$.proposal.entity_id') IN ({slots}) "
            conditions += f"OR json_extract(data,'$.proposal.location') IN ({slots}))"
            parameters.extend((*terms, *terms))
        if active:
            conditions += (
                " AND state NOT IN "
                "('SUPPRESSED','COMPLETED','FAILED','CANCELLED','EXPIRED','NEEDS_REVIEW')"
            )
        order = "ASC" if active else "DESC"
        with self.database.transaction() as c:
            rows = c.execute(
                f"SELECT * FROM initiatives WHERE {conditions} "
                f"ORDER BY created_at {order},initiative_id LIMIT ?",
                (*parameters, limit),
            ).fetchall()
            return tuple(self._decode(row, InitiativeRecord) for row in rows)

    @staticmethod
    def _journal(c, record, session_id):
        _append_event(
            c,
            Event(
                event_type="initiative.state_changed",
                principal_id=record.owner_id,
                correlation_id=record.correlation_id,
                causation_id=record.proposal.causation_id,
                source="jarvis.initiatives",
                payload={
                    "schema_version": 1,
                    "initiative_id": str(record.initiative_id),
                    "state": record.state,
                    "revision": record.revision,
                    "reason_code": record.decision.reason_code,
                },
            ),
        )
        _append_audit(
            c,
            AuditRecord(
                audit_id=record.audit_id,
                principal_id=record.owner_id,
                session_id=session_id,
                correlation_id=record.correlation_id,
                task_id=record.execution.task_id if record.execution else None,
                action="initiative.transition",
                outcome="denied" if record.state in {"FAILED", "SUPPRESSED"} else "succeeded",
                reason_code=record.decision.reason_code,
                policy_refs=record.decision.policy_refs,
            ),
        )
        c.execute(
            "INSERT INTO initiative_history(initiative_id,revision,data) VALUES(?,?,?)",
            (str(record.initiative_id), record.revision, record.model_dump_json()),
        )

    def _accept(self, policy, event, sequence, expected_cursor, record, session_id, *, access):
        self._guard(access)
        with self.database.transaction(write=True) as c:
            cursor = c.execute(
                "SELECT sequence FROM initiative_cursors WHERE policy_id=?", (policy.policy_id,)
            ).fetchone()[0]
            if cursor != expected_cursor:
                raise StorageError("initiative_revision_conflict")
            # No high-water skip: each accepted visible event is durably acknowledged.
            if sequence <= cursor:
                raise StorageError("initiative_cursor_invalid")
            reason = "irrelevant_event"
            if record is not None:
                now = record.created_at
                cooldown = timestamp(now - timedelta(seconds=policy.cooldown_seconds))
                repeated = c.execute(
                    (
                        "SELECT 1 FROM initiatives WHERE owner_id=? AND "
                        "deduplication_key=? AND created_at>? AND state!='SUPPRESSED' LIMIT 1"
                    ),
                    (str(record.owner_id), record.proposal.deduplication_key, cooldown),
                ).fetchone()
                count = c.execute(
                    (
                        "SELECT count(*) FROM initiatives WHERE owner_id=? AND "
                        "created_at>? AND state!='SUPPRESSED'"
                    ),
                    (
                        str(record.owner_id),
                        timestamp(now - timedelta(seconds=policy.rate_window_seconds)),
                    ),
                ).fetchone()[0]
                active = c.execute(
                    (
                        "SELECT count(*) FROM initiatives WHERE owner_id=? AND state NOT "
                        "IN ('SUPPRESSED','COMPLETED','FAILED','CANCELLED',"
                        "'EXPIRED','NEEDS_REVIEW')"
                    ),
                    (str(record.owner_id),),
                ).fetchone()[0]
                reason = (
                    "cooldown"
                    if repeated
                    else "rate_limit"
                    if count >= policy.rate_limit
                    else "active_limit"
                    if active >= policy.max_active
                    else record.decision.reason_code
                )
                if reason in {"cooldown", "rate_limit", "active_limit"}:
                    record = record.model_copy(
                        update={
                            "state": "SUPPRESSED",
                            "decision": record.decision.model_copy(
                                update={"disposition": "suppress", "reason_code": reason}
                            ),
                        }
                    )
                    suppression = InitiativeSuppressionRecord(
                        policy_id=policy.policy_id,
                        principal_id=record.owner_id,
                        event_id=event.event_id,
                        reason_code=reason,
                        deduplication_key=record.proposal.deduplication_key,
                        recorded_at=now,
                    )
                    c.execute(
                        "INSERT INTO initiative_suppressions VALUES(?,?,?,?,?)",
                        (
                            str(suppression.suppression_id),
                            str(suppression.principal_id),
                            policy.policy_id,
                            str(event.event_id),
                            suppression.model_dump_json(),
                        ),
                    )
                record = decode_initiative(record.model_dump_json())
                c.execute(
                    "INSERT INTO initiatives VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        str(record.initiative_id),
                        str(record.owner_id),
                        str(record.correlation_id),
                        policy.policy_id,
                        record.proposal.deduplication_key,
                        record.state,
                        record.revision,
                        timestamp(record.created_at),
                        record.model_dump_json(),
                    ),
                )
                self._journal(c, record, session_id)
            c.execute(
                "INSERT INTO initiative_receipts VALUES(?,?,?,?,?)",
                (
                    policy.policy_id,
                    str(event.event_id),
                    sequence,
                    str(record.initiative_id) if record else None,
                    reason,
                ),
            )
            c.execute(
                "UPDATE initiative_cursors SET sequence=? WHERE policy_id=?",
                (sequence, policy.policy_id),
            )
        return record

    def _save(self, old, new, session_id, *, access):
        self._guard(access)
        new = decode_initiative(new.model_dump_json())
        if (
            new.state not in TRANSITIONS[old.state]
            or new.revision != old.revision + 1
            or new.proposal != old.proposal
            or new.proposal_hash != old.proposal_hash
        ):
            raise StorageError("initiative_transition_invalid")
        with self.database.transaction(write=True) as c:
            row = c.execute(
                "SELECT * FROM initiatives WHERE initiative_id=? AND owner_id=?",
                (str(old.initiative_id), str(old.owner_id)),
            ).fetchone()
            if row is None or self._decode(row, InitiativeRecord) != old:
                raise StorageError("initiative_revision_conflict")
            changed = c.execute(
                (
                    "UPDATE initiatives SET state=?,revision=?,data=? WHERE "
                    "initiative_id=? AND revision=?"
                ),
                (
                    new.state,
                    new.revision,
                    new.model_dump_json(),
                    str(old.initiative_id),
                    old.revision,
                ),
            ).rowcount
            if changed != 1:
                raise StorageError("initiative_revision_conflict")
            self._journal(c, new, session_id)
        return new

    @contextmanager
    def _claim(self, initiative_id, *, access):
        self._guard(access)
        path = self.database.path.with_name(
            f"{self.database.path.name}.initiative-{initiative_id}.lock"
        )
        try:
            with _file_lock(path):
                yield
        except TaskBusyError:
            raise StorageError("initiative_busy") from None

    def _history(self, initiative_id, owner_id, *, limit, access):
        self._guard(access)
        with self.database.transaction() as c:
            rows = c.execute(
                (
                    "SELECT h.data FROM initiative_history h JOIN initiatives i "
                    "USING(initiative_id) WHERE i.owner_id=? AND i.initiative_id=? "
                    "ORDER BY h.revision DESC LIMIT ?"
                ),
                (str(owner_id), str(initiative_id), limit),
            ).fetchall()
            return tuple(decode_initiative(r[0]) for r in rows)
