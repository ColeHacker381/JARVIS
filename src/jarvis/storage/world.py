"""Private replaceable SQLite evidence/projection adapter; no public global query API."""

from datetime import UTC
from uuid import NAMESPACE_URL, uuid5

from jarvis.core.contracts import Event
from jarvis.core.world_records import attribute, decode_world, partition
from jarvis.storage.errors import StorageError
from jarvis.storage.sqlite import _append_audit, _append_event, _decode_event


def timestamp(value):
    return value.astimezone(UTC).isoformat()


class SQLiteWorldStore:
    def __init__(self, database, identities, audit):
        if database.path != identities.database.path or database.path != audit.database.path:
            raise ValueError("world, identity and audit must share a database")
        self.database, self.identities, self.audit = database, identities, audit
        self._access = None

    def _bind(self):
        if self._access is not None:
            raise ValueError("world store already bound")
        self._access = object()
        return self._access

    def _guard(self, access):
        if access is not self._access or access is None:
            raise StorageError("world_store_private")

    @staticmethod
    def _source_decode(row):
        if row is None:
            return None
        try:
            source = decode_world(row["data"])
            if (
                source.source_id,
                str(source.principal_id),
                partition(source.access_scope),
                source.revision,
            ) != tuple(row[k] for k in ("source_id", "principal_id", "partition_id", "revision")):
                raise ValueError()
            return source
        except (ValueError, TypeError, KeyError, AttributeError):
            raise StorageError("world_record_invalid") from None

    @staticmethod
    def _observation_decode(row):
        try:
            record = decode_world(row["data"])
            if (
                str(record.observation_id),
                str(record.event_id),
                record.source_id,
                partition(record.access_scope),
                record.entity_id,
                attribute(record.observation_type),
                timestamp(record.observed_at),
            ) != tuple(
                row[k]
                for k in (
                    "observation_id",
                    "event_id",
                    "source_id",
                    "partition_id",
                    "entity_id",
                    "attribute",
                    "observed_at",
                )
            ):
                raise ValueError()
            return record
        except (ValueError, TypeError, KeyError, AttributeError):
            raise StorageError("world_record_invalid") from None

    @staticmethod
    def _scope_index(c, scope):
        actual = {
            r[0]
            for r in c.execute(
                "SELECT principal_id FROM world_readers WHERE partition_id=?", (partition(scope),)
            )
        }
        expected = set(map(str, (scope.owner_id, *scope.shared_with)))
        if actual != expected:
            raise StorageError("world_record_invalid")

    @staticmethod
    def _state_decode(c, row):
        try:
            state = decode_world(row["data"])
            SQLiteWorldStore._scope_index(c, state.access_scope)
            if (
                str(state.state_id),
                partition(state.access_scope),
                state.entity_id,
                state.attribute,
                state.location,
                state.revision,
            ) != tuple(
                row[k]
                for k in (
                    "state_id",
                    "partition_id",
                    "entity_id",
                    "attribute",
                    "location",
                    "revision",
                )
            ):
                raise ValueError()
            evidence = {
                r[0]
                for r in c.execute(
                    "SELECT observation_id FROM world_evidence WHERE state_id=?",
                    (str(state.state_id),),
                )
            }
            if evidence != set(map(str, state.evidence_ids)):
                raise ValueError()
            return state
        except (ValueError, TypeError, KeyError, AttributeError):
            raise StorageError("world_record_invalid") from None

    def _source(self, source_id, *, access):
        self._guard(access)
        with self.database.transaction() as c:
            return self._source_decode(
                c.execute("SELECT * FROM world_sources WHERE source_id=?", (source_id,)).fetchone()
            )

    def _register(self, source, audit, *, expected_revision=None, access):
        self._guard(access)
        pid = partition(source.access_scope)
        with self.database.transaction(write=True) as c:
            existing = self._source_decode(
                c.execute(
                    "SELECT * FROM world_sources WHERE source_id=?", (source.source_id,)
                ).fetchone()
            )
            if expected_revision is None:
                if existing is not None:
                    raise StorageError("source_conflict")
                count = c.execute(
                    "SELECT COUNT(*) FROM world_sources WHERE partition_id=?", (pid,)
                ).fetchone()[0]
                if count >= 32:
                    raise StorageError("source_limit")
                c.execute(
                    "INSERT INTO world_sources VALUES (?,?,?,?,?)",
                    (
                        source.source_id,
                        str(source.principal_id),
                        pid,
                        source.revision,
                        source.model_dump_json(),
                    ),
                )
            else:
                if existing is None or existing.revision != expected_revision:
                    raise StorageError("world_revision_conflict")
                # Only enabling/disabling is a v0 update. Never widen stored evidence scopes.
                if source.model_dump(exclude={"revision", "enabled"}) != existing.model_dump(
                    exclude={"revision", "enabled"}
                ):
                    raise StorageError("source_conflict")
                c.execute(
                    "UPDATE world_sources SET revision=?,data=? WHERE source_id=? AND revision=?",
                    (
                        source.revision,
                        source.model_dump_json(),
                        source.source_id,
                        expected_revision,
                    ),
                )
            for reader in (source.access_scope.owner_id, *source.access_scope.shared_with):
                c.execute("INSERT OR IGNORE INTO world_readers VALUES (?,?)", (pid, str(reader)))
            _append_audit(c, audit)

    def _evidence(self, c, pid, entity, attr, now):
        # Latest timestamp per source, including ties (contradictions cannot disappear by ID order).
        rows = c.execute(
            """SELECT o.* FROM observations o WHERE o.partition_id=?
            AND o.entity_id=? AND o.attribute=? AND o.observed_at<=?
            AND o.observed_at=(SELECT MAX(n.observed_at) FROM observations n
              WHERE n.partition_id=o.partition_id AND n.entity_id=o.entity_id
                AND n.attribute=o.attribute AND n.source_id=o.source_id AND n.observed_at<=?)
            ORDER BY o.source_id,o.observation_id LIMIT 65""",
            (pid, entity, attr, timestamp(now), timestamp(now)),
        ).fetchall()
        if len(rows) > 64:
            raise StorageError("evidence_limit")
        records = tuple(self._observation_decode(row) for row in rows)
        sources, availability = {}, {}
        for record in records:
            sources[record.source_id] = self._source_decode(
                c.execute(
                    "SELECT * FROM world_sources WHERE source_id=?", (record.source_id,)
                ).fetchone()
            )
            available_rows = c.execute(
                """SELECT * FROM observations WHERE source_id=?
                AND attribute='online' AND observed_at=(SELECT MAX(observed_at) FROM observations
                WHERE source_id=? AND attribute='online' AND observed_at<=?)
                ORDER BY observation_id LIMIT 65""",
                (record.source_id, record.source_id, timestamp(now)),
            ).fetchall()
            if len(available_rows) > 64:
                raise StorageError("evidence_limit")
            availability[record.source_id] = tuple(
                self._observation_decode(r) for r in available_rows
            )
        return records, sources, availability

    def _refresh(self, c, pid, entity, attr, now, project, cause=None):
        row = c.execute(
            "SELECT * FROM world_states WHERE partition_id=? AND entity_id=? AND attribute=?",
            (pid, entity, attr),
        ).fetchone()
        old = self._state_decode(c, row) if row else None
        evidence, sources, availability = self._evidence(c, pid, entity, attr, now)
        if not evidence:
            return old
        state = project(evidence, sources, availability, now)
        for record in evidence:
            if record.expires_at <= now:
                inserted = c.execute(
                    "INSERT OR IGNORE INTO world_expirations VALUES (?)",
                    (str(record.observation_id),),
                ).rowcount
                if inserted:
                    _append_event(
                        c,
                        Event(
                            event_id=uuid5(NAMESPACE_URL, f"world-expired:{record.observation_id}"),
                            event_type="world.observation_expired",
                            occurred_at=now,
                            correlation_id=record.correlation_id,
                            causation_id=record.event_id,
                            principal_id=record.source_principal_id,
                            source="world.model",
                            payload={
                                "schema_version": 1,
                                "observation_id": str(record.observation_id),
                            },
                        ),
                    )
        ignored = {"revision", "updated_at", "evaluated_at"}
        if old and old.model_dump(exclude=ignored) == state.model_dump(exclude=ignored):
            return old.model_copy(update={"evaluated_at": now})
        state = state.model_copy(update={"revision": old.revision + 1 if old else 0})
        if old:
            changed = c.execute(
                "UPDATE world_states SET revision=?,data=? WHERE state_id=? AND revision=?",
                (state.revision, state.model_dump_json(), str(state.state_id), old.revision),
            ).rowcount
            if changed != 1:
                raise StorageError("world_revision_conflict")
            c.execute("DELETE FROM world_evidence WHERE state_id=?", (str(state.state_id),))
        else:
            c.execute(
                "INSERT INTO world_states VALUES (?,?,?,?,?,?,?)",
                (
                    str(state.state_id),
                    pid,
                    entity,
                    attr,
                    state.location,
                    state.revision,
                    state.model_dump_json(),
                ),
            )
        c.executemany(
            "INSERT INTO world_evidence VALUES (?,?)",
            [(str(state.state_id), str(e)) for e in state.evidence_ids],
        )
        cause = cause or max(
            (*evidence, *(a for values in availability.values() for a in values)),
            key=lambda e: (e.observed_at, str(e.observation_id)),
        )
        _append_event(
            c,
            Event(
                event_id=uuid5(NAMESPACE_URL, f"world-state:{state.state_id}:{state.revision}"),
                event_type="world.entity_state_changed",
                occurred_at=now,
                correlation_id=cause.correlation_id,
                causation_id=cause.event_id,
                principal_id=cause.source_principal_id,
                source="world.model",
                payload={
                    "schema_version": 1,
                    "state_id": str(state.state_id),
                    "revision": state.revision,
                },
            ),
        )
        return state

    def _ingest(self, incoming, record, audit, now, project, *, access):
        self._guard(access)
        pid = partition(record.access_scope)
        with self.database.transaction(write=True) as c:
            row = c.execute(
                "SELECT * FROM observations WHERE observation_id=? OR event_id=?",
                (str(record.observation_id), str(record.event_id)),
            ).fetchone()
            if row:
                if row["source_id"] != record.source_id or row["partition_id"] != pid:
                    raise StorageError("access_denied")
                existing = self._observation_decode(row)
                if row["input_data"] != incoming.model_dump_json():
                    raise StorageError("observation_conflict")
                _append_audit(c, audit)
                return existing, True
            location = c.execute(
                "SELECT location FROM world_entities WHERE partition_id=? AND entity_id=?",
                (pid, record.entity_id),
            ).fetchone()
            if location and location[0] != record.location:
                raise StorageError("entity_location_conflict")
            c.execute(
                "INSERT OR IGNORE INTO world_entities VALUES (?,?,?)",
                (pid, record.entity_id, record.location),
            )
            c.execute(
                """INSERT INTO observations(observation_id,event_id,source_id,partition_id,
                entity_id,attribute,observed_at,input_data,data) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    str(record.observation_id),
                    str(record.event_id),
                    record.source_id,
                    pid,
                    record.entity_id,
                    attribute(record.observation_type),
                    timestamp(record.observed_at),
                    incoming.model_dump_json(),
                    record.model_dump_json(),
                ),
            )
            event_type = {
                "person_detected": "perception.person_detected",
                "occupancy": "sensor.occupancy_changed",
                "motion": "sensor.motion_detected",
                "availability": (
                    "device.connected"
                    if record.payload["value"] is True and record.source_available
                    else "device.disconnected"
                    if record.payload["value"] is False and record.source_available
                    else "device.availability_unknown"
                ),
            }
            _append_event(
                c,
                Event(
                    event_id=record.event_id,
                    event_type=event_type[record.observation_type],
                    occurred_at=record.observed_at,
                    correlation_id=record.correlation_id,
                    principal_id=record.source_principal_id,
                    source=record.source_id,
                    payload={"schema_version": 1, "observation_id": str(record.observation_id)},
                ),
            )
            self._refresh(
                c, pid, record.entity_id, attribute(record.observation_type), now, project
            )
            if record.observation_type == "availability":
                rows = c.execute(
                    "SELECT entity_id,attribute FROM world_states WHERE partition_id=?", (pid,)
                ).fetchall()
                for row in rows:
                    self._refresh(
                        c, pid, row["entity_id"], row["attribute"], now, project, cause=record
                    )
            _append_audit(c, audit)
            return record, False

    def _query(
        self,
        principal_id,
        *,
        now,
        project,
        entity=None,
        location=None,
        attr=None,
        state_id=None,
        history=False,
        terms=None,
        limit=10,
        access,
    ):
        self._guard(access)
        conditions = [
            "EXISTS (SELECT 1 FROM world_readers r WHERE r.partition_id=w.partition_id "
            "AND r.principal_id=?)"
        ]
        params = [str(principal_id)]
        for column, value in (("entity_id", entity), ("location", location), ("attribute", attr)):
            if value is not None:
                conditions.append(f"w.{column}=?")
                params.append(value)
        if terms is not None:
            slots = ",".join("?" for _ in terms)
            if not slots:
                return ()
            conditions.append(f"(w.entity_id IN ({slots}) OR w.location IN ({slots}))")
            params.extend((*terms, *terms))
        if state_id is not None:
            conditions.append("w.state_id=?")
            params.append(str(state_id))
        with self.database.transaction(write=True) as c:
            # Scoped projection keys bound the query; history joins only visible partitions.
            rows = c.execute(
                "SELECT w.* FROM world_states w WHERE "
                + " AND ".join(conditions)
                + " ORDER BY w.entity_id,w.attribute,w.state_id LIMIT ?",
                (*params, limit),
            ).fetchall()
            if history:
                for row in rows:
                    self._state_decode(c, row)
                if not rows:
                    return ()
                cond = []
                values = []
                for row in rows:
                    cond.append("(partition_id=? AND entity_id=? AND attribute=?)")
                    values.extend((row["partition_id"], row["entity_id"], row["attribute"]))
                evidence = c.execute(
                    "SELECT * FROM observations WHERE "
                    + " OR ".join(cond)
                    + " ORDER BY observed_at DESC,observation_id DESC LIMIT ?",
                    (*values, limit),
                ).fetchall()
                return tuple(self._observation_decode(r) for r in evidence)
            return tuple(
                self._refresh(c, r["partition_id"], r["entity_id"], r["attribute"], now, project)
                for r in rows
            )

    def _events(self, principal_id, limit, *, after_sequence=None, access):
        self._guard(access)
        with self.database.transaction() as c:
            rows = c.execute(
                """SELECT e.* FROM events e WHERE e.event_type IN (
                  'perception.person_detected','sensor.occupancy_changed','sensor.motion_detected',
                  'device.connected','device.disconnected','device.availability_unknown',
                  'world.entity_state_changed',
                  'world.observation_expired') AND EXISTS (
                SELECT 1 FROM observations o JOIN world_readers r ON r.partition_id=o.partition_id
                WHERE r.principal_id=? AND (e.event_id=o.event_id OR e.causation_id=o.event_id))
                AND e.sequence>? ORDER BY e.sequence """
                + ("DESC" if after_sequence is None else "ASC")
                + " LIMIT ?",
                (str(principal_id), after_sequence or 0, limit),
            ).fetchall()
            for row in rows:
                evidence = c.execute(
                    """SELECT o.* FROM observations o
                    WHERE (o.event_id=? OR o.event_id=?) AND EXISTS (SELECT 1 FROM world_readers r
                    WHERE r.partition_id=o.partition_id AND r.principal_id=?) LIMIT 1""",
                    (row["event_id"], row["causation_id"], str(principal_id)),
                ).fetchone()
                if evidence is None:
                    raise StorageError("world_record_invalid")
                self._scope_index(c, self._observation_decode(evidence).access_scope)
            if after_sequence is not None:
                return tuple((r["sequence"], _decode_event(r)) for r in rows)
            return tuple(_decode_event(r) for r in reversed(rows))
