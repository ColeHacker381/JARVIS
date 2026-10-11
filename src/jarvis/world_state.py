"""Trusted ingestion and deterministic scoped world beliefs, separate from memory and cognition."""

import time
from contextlib import contextmanager
from datetime import timedelta
from typing import Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import ValidationError

from jarvis.authorization import DeterministicPolicy
from jarvis.core.contracts import (
    AccessScope,
    ActionProposal,
    AuditRecord,
    Capability,
    Observation,
    Provenance,
    Session,
)
from jarvis.core.time import utc_now
from jarvis.core.world_records import (
    ObservationInput,
    ObservationRecord,
    SourceRegistration,
    WorldState,
    attribute,
    decode_world,
    partition,
)
from jarvis.identity import IdentityError
from jarvis.observability import AuditWriteError, NullMetrics, write_audit
from jarvis.storage.errors import StorageError


class ObservationSource(Protocol):
    def observe(self, session: Session) -> tuple[Observation, ...]: ...


class SensorAdapter(Protocol):
    """Adapters submit data with a separately established credential, never self-authenticate."""

    def publish(
        self, ingestion, *, session_token: str, **values
    ) -> tuple[ObservationRecord, bool]: ...


class WorldStore(Protocol):
    identities: object
    audit: object

    def _bind(self) -> object: ...
    def _source(self, source_id, *, access): ...
    def _register(self, source, audit, *, expected_revision=None, access): ...
    def _ingest(self, incoming, record, audit, now, project, *, access): ...
    def _events(self, principal_id, limit, *, after_sequence=None, access): ...

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
    ): ...


class WorldError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def project_state(evidence, sources, availability, now):
    """Latest per-source evidence; max confidence, no probabilistic fusion or identity inference."""
    support, dependencies = [], {e.observation_id for e in evidence}
    for observation in evidence:
        source = sources[observation.source_id]
        available = availability[observation.source_id]
        latest = (
            tuple(a for a in available if a.observed_at == available[0].observed_at)
            if available
            else ()
        )
        disconnected = any(a.payload["value"] is not True or not a.source_available for a in latest)
        if attribute(observation.observation_type) != "online" and latest:
            dependencies.update(a.observation_id for a in latest)
        if (
            source is None
            or not source.enabled
            or not observation.source_available
            or observation.expires_at <= now
            or (disconnected and observation.observation_type != "availability")
        ):
            continue
        value = observation.payload["value"]
        # No person detected is NOT proof that the room is empty.
        if value is None or (observation.observation_type == "person_detected" and value is False):
            continue
        confidence = observation.confidence * source.reliability
        if confidence > 0:
            support.append((observation, value, confidence))
    values = {s[1] for s in support}
    status, value, confidence, inference = "unknown", None, 0.0, None
    chosen = evidence
    if len(values) > 1:
        status = "conflicting"
        chosen = tuple(s[0] for s in support)
    elif support:
        value = support[0][1]
        confidence = max(s[2] for s in support)
        chosen = tuple(s[0] for s in support)
        status = (
            "observed"
            if any(s[0].observation_type != "person_detected" for s in support)
            else "inferred"
        )
        if status == "inferred":
            inference = "camera-presence-implies-possible-occupancy-v1"
    elif all(e.expires_at <= now for e in evidence):
        status = "stale"
    elif all(
        not sources[e.source_id].enabled
        or not e.source_available
        or (
            e.observation_type != "availability"
            and any(a.payload["value"] is not True for a in availability[e.source_id])
        )
        for e in evidence
    ):
        status = "unavailable"
    latest = max(chosen, key=lambda e: (e.observed_at, str(e.observation_id)))
    # The first expiry can change support/conflict. Last evidence time is distinct from update time.
    expiry = min(e.expires_at for e in chosen)
    return WorldState(
        state_id=uuid5(
            NAMESPACE_URL,
            f"world:{partition(latest.access_scope)}:{latest.entity_id}:{attribute(latest.observation_type)}",
        ),
        entity_id=latest.entity_id,
        attribute=attribute(latest.observation_type),
        location=latest.location,
        value=value,
        status=status,
        confidence=confidence,
        evidence_ids=tuple(sorted(dependencies, key=str)),
        inference_ref=inference,
        last_observed_at=latest.observed_at,
        updated_at=now,
        expires_at=expiry,
        evaluated_at=now,
        access_scope=latest.access_scope,
    )


def is_world_question(text):
    """Small replaceable intent rule; never derive permissions or person identity from text."""
    import re

    words = set(re.findall(r"[a-z]+", text.casefold()))
    return bool(
        words
        & {
            "occupancy",
            "occupied",
            "anyone",
            "someone",
            "motion",
            "movement",
            "online",
            "offline",
            "connected",
            "disconnected",
            "sensor",
        }
    )


def describe_world(states):
    """Render evidence; no invented identity or confirmed current occupancy."""
    lines = []
    for state in states:
        label = {
            "observed": "direct sensor report",
            "inferred": "inferred possible state",
            "conflicting": "conflicting evidence",
            "stale": "stale evidence; current state unknown",
            "unavailable": "source unavailable; current state unknown",
            "unknown": "unknown",
        }[state.status]
        value = "unknown" if state.value is None else str(state.value).lower()
        lines.append(
            f"{state.entity_id} {state.attribute}: {value} ({label}; "
            f"confidence strength {state.confidence:.2f}; "
            f"last observed {state.last_observed_at.isoformat()}; "
            f"evidence {', '.join(map(str, state.evidence_ids[:2]))}"
            f" (total {len(state.evidence_ids)}))."
        )
    return " ".join(lines)


class WorldModel:
    def __init__(self, store: WorldStore, permissions, *, clock=utc_now, metrics=None):
        if permissions.identities is not store.identities:
            raise ValueError("world and policy must share identity state")
        self.identities, self.permissions, self.audit = store.identities, permissions, store.audit
        self._store, self._access = store, store._bind()
        self.policy = DeterministicPolicy(self.identities, permissions, self.audit)
        self.clock, self.metrics = clock, metrics or NullMetrics()

    @contextmanager
    def _request(self, token, action, correlation=None):
        session = None
        correlation = correlation or uuid4()
        try:
            with self.identities.transaction():
                session = self.identities.resolve_session(token)
                if session.principal_id is None or session.authentication_ref is None:
                    raise IdentityError("unauthenticated")
                # Normalize domain/storage errors before leaving the identity claim:
                # that adapter intentionally maps raw errors to authentication denial.
                try:
                    yield session, correlation
                except StorageError as exc:
                    raise WorldError(exc.code) from None
                except (ValidationError, ValueError, TypeError, KeyError, AttributeError):
                    raise WorldError("validation_failure") from None
        except (
            WorldError,
            StorageError,
            ValidationError,
            ValueError,
            TypeError,
            AttributeError,
            IdentityError,
            AuditWriteError,
        ) as exc:
            code = (
                exc.code
                if isinstance(exc, (WorldError, StorageError))
                else "access_denied"
                if isinstance(exc, IdentityError)
                else "audit_unavailable"
                if isinstance(exc, AuditWriteError)
                else "validation_failure"
            )
            try:
                write_audit(
                    self.audit,
                    self._audit(
                        session,
                        action,
                        "failed"
                        if code not in {"access_denied", "world_unavailable"}
                        else "denied",
                        code,
                        correlation,
                    ),
                )
            except AuditWriteError:
                raise WorldError("audit_unavailable") from None
            raise WorldError(code) from None

    @staticmethod
    def _audit(session, action, outcome, code, correlation):
        return AuditRecord(
            principal_id=session.principal_id if session else None,
            session_id=session.session_id if session else None,
            correlation_id=correlation,
            action=action,
            outcome=outcome,
            reason_code=code,
        )

    def _authorize(self, session, token, permission, correlation):
        proposal = ActionProposal(
            session_id=session.session_id,
            capability_id=permission,
            correlation_id=correlation,
            origin="rule",
            source_ref="world.service",
        )
        capability = Capability(
            capability_id=permission,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            required_permissions=(permission,),
            adapter_ref="world.service",
        )
        decision = self.policy.evaluate(
            token, proposal, capability, access_scope=AccessScope(owner_id=session.principal_id)
        )
        if decision.decision != "allow":
            raise WorldError("access_denied")

    def register_source(self, source: SourceRegistration, *, session_token):
        with self._request(session_token, "world.source.register") as (session, corr):
            source = decode_world(source.model_dump_json())
            self._authorize(session, session_token, "world.sources.manage", corr)
            if source.access_scope.owner_id != session.principal_id or source.revision != 0:
                raise WorldError("access_denied")
            for principal_id in (source.principal_id, *source.access_scope.shared_with):
                principal = self.identities.get_principal(principal_id)
                if principal is None or principal.status != "active":
                    raise WorldError("access_denied")
            self._store._register(
                source,
                self._audit(
                    session, "world.source.register", "succeeded", "source_registered", corr
                ),
                access=self._access,
            )
            return source

    def set_source_enabled(self, source_id, enabled, *, expected_revision, session_token):
        with self._request(session_token, "world.source.update") as (session, corr):
            if (
                type(enabled) is not bool
                or type(expected_revision) is not int
                or expected_revision < 0
            ):
                raise WorldError("validation_failure")
            self._authorize(session, session_token, "world.sources.manage", corr)
            source = self._store._source(source_id, access=self._access)
            if source is None or source.access_scope.owner_id != session.principal_id:
                raise WorldError("world_unavailable")
            updated = SourceRegistration.model_validate(
                source.model_copy(
                    update={"enabled": enabled, "revision": expected_revision + 1}
                ).model_dump()
            )
            self._store._register(
                updated,
                self._audit(session, "world.source.update", "succeeded", "source_updated", corr),
                expected_revision=expected_revision,
                access=self._access,
            )
            return updated

    def _project(self, *args):
        start = time.monotonic()
        try:
            return project_state(*args)
        finally:
            self.metrics.record("world.project_ms", (time.monotonic() - start) * 1000)

    def ingest(self, incoming, *, session_token):
        start = time.monotonic()
        try:
            with self._request(session_token, "world.observation.ingest") as (session, _):
                data = incoming if isinstance(incoming, str) else incoming.model_dump_json()
                if len(data.encode()) > 16384:
                    raise WorldError("observation_too_large")
                incoming = decode_world(data)
                if not isinstance(incoming, ObservationInput):
                    raise WorldError("validation_failure")
                now = self.clock()
                source = self._store._source(incoming.source_id, access=self._access)
                if (
                    source is None
                    or not source.enabled
                    or source.principal_id != session.principal_id
                    or incoming.observation_type not in source.observation_types
                    or incoming.entity_id not in source.entities
                    or incoming.location != source.location
                ):
                    raise WorldError("access_denied")
                self._authorize(session, session_token, "world.ingest", incoming.correlation_id)
                if incoming.observed_at > now:
                    raise WorldError("invalid_observation_time")
                expiry = incoming.observed_at + timedelta(seconds=source.ttl_seconds)
                if incoming.expires_at:
                    expiry = min(expiry, incoming.expires_at)
                record = ObservationRecord(
                    observation_id=incoming.observation_id,
                    source=Provenance(category="observation", source_ref=source.source_id),
                    observed_at=incoming.observed_at,
                    recorded_at=now,
                    payload=incoming.payload.model_dump(),
                    confidence=incoming.confidence,
                    evidence_kind="observed"
                    if incoming.source_available and incoming.payload.value is not None
                    else "unknown",
                    access_scope=source.access_scope,
                    source_id=source.source_id,
                    source_principal_id=session.principal_id,
                    observation_type=incoming.observation_type,
                    entity_id=incoming.entity_id,
                    location=incoming.location,
                    subject_id=incoming.subject_id,
                    event_id=incoming.event_id,
                    correlation_id=incoming.correlation_id,
                    source_available=incoming.source_available,
                    expires_at=expiry,
                    raw_evidence_refs=incoming.raw_evidence_refs,
                )
                return self._store._ingest(
                    incoming,
                    record,
                    self._audit(
                        session,
                        "world.observation.ingest",
                        "succeeded",
                        "observation_accepted",
                        incoming.correlation_id,
                    ),
                    now,
                    self._project,
                    access=self._access,
                )
        finally:
            self.metrics.record("observation.ingest_ms", (time.monotonic() - start) * 1000)

    def query(
        self,
        *,
        session_token,
        entity=None,
        location=None,
        attr=None,
        history=False,
        limit=10,
        state_id=None,
    ):
        start = time.monotonic()
        try:
            with self._request(session_token, "world.query") as (session, corr):
                if type(limit) is not int or not 1 <= limit <= 20 or type(history) is not bool:
                    raise WorldError("validation_failure")
                self._authorize(session, session_token, "world.read", corr)
                if history:
                    self._authorize(session, session_token, "world.history", corr)
                result = self._store._query(
                    session.principal_id,
                    now=self.clock(),
                    project=self._project,
                    entity=entity,
                    location=location,
                    attr=attr,
                    state_id=state_id,
                    history=history,
                    limit=limit,
                    access=self._access,
                )
                write_audit(
                    self.audit,
                    self._audit(session, "world.query", "succeeded", "world_query_completed", corr),
                )
                return result
        finally:
            self.metrics.record("world.query_ms", (time.monotonic() - start) * 1000)

    def get(self, state_id, *, session_token):
        with self._request(session_token, "world.get"):
            states = self.query(state_id=state_id, session_token=session_token, limit=1)
            if not states:
                raise WorldError("world_unavailable")
            return states[0]

    def events(self, *, session_token, limit=20, after_sequence=None):
        with self._request(session_token, "world.events") as (session, corr):
            if type(limit) is not int or not 1 <= limit <= 100:
                raise WorldError("validation_failure")
            self._authorize(session, session_token, "world.read", corr)
            self._authorize(session, session_token, "world.history", corr)
            if after_sequence is not None and (
                type(after_sequence) is not int or after_sequence < 0
            ):
                raise WorldError("validation_failure")
            records = self._store._events(
                session.principal_id, limit, after_sequence=after_sequence, access=self._access
            )
            write_audit(
                self.audit,
                self._audit(session, "world.events", "succeeded", "world_events_read", corr),
            )
            return records

    def relevant(self, text, *, session_token, limit=3):
        # Only explicit registered entity/location words, not arbitrary full-world context dumps.
        import re

        terms = list(dict.fromkeys(re.findall(r"[a-z0-9][a-z0-9_.:-]{2,95}", text.casefold())))[:32]
        attributes = {
            "motion": "motion",
            "movement": "motion",
            "online": "online",
            "offline": "online",
            "connected": "online",
            "disconnected": "online",
            "occupancy": "occupancy",
            "occupied": "occupancy",
            "anyone": "occupancy",
            "someone": "occupancy",
            "person": "occupancy",
        }
        attr = next((attributes[t] for t in terms if t in attributes), None)
        # Structured keyword variants handle logical names such as "front-door".
        pairs = [terms[i : i + 2] for i in range(len(terms) - 1)]
        variants = [sep.join(pair) for pair in pairs for sep in ("-", "_", "")]
        terms = list(dict.fromkeys((*terms, *variants)))[:48]
        with self._request(session_token, "world.context") as (session, corr):
            if type(limit) is not int or not 1 <= limit <= 5:
                raise WorldError("validation_failure")
            self._authorize(session, session_token, "world.read", corr)
            start = time.monotonic()
            try:
                result = self._store._query(
                    session.principal_id,
                    now=self.clock(),
                    project=self._project,
                    terms=terms,
                    attr=attr,
                    limit=limit,
                    access=self._access,
                )
                write_audit(
                    self.audit,
                    self._audit(session, "world.context", "succeeded", "world_context_read", corr),
                )
                return result
            finally:
                self.metrics.record("world.query_ms", (time.monotonic() - start) * 1000)
