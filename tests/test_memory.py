import json
import sqlite3
from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from jarvis.core.contracts import Provenance, decode_contract
from jarvis.core.memory_records import (
    MemoryDraft,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
    decode_memory,
)
from jarvis.core.time import utc_now
from jarvis.memory import MemoryError, MemoryService
from jarvis.storage.memory import SQLiteMemoryStore
from jarvis.storage.security import SQLiteDevelopmentIdentityService, SQLitePermissionStore
from jarvis.storage.sqlite import SQLiteAuditSink, SQLiteDatabase


@pytest.fixture
def runtime(tmp_path):
    db = SQLiteDatabase(tmp_path / "journal.sqlite3")
    db.initialize()
    ids = SQLiteDevelopmentIdentityService(db)
    users = {
        "owner": ids.bootstrap_owner_for_testing(),
        "a": ids.create_principal_for_testing("A"),
        "b": ids.create_principal_for_testing("B"),
        "guest": ids.create_principal_for_testing("Guest", kind="guest"),
    }
    sessions = {k: ids.establish_session_for_testing(p.principal_id) for k, p in users.items()}
    audit = SQLiteAuditSink(db)
    permissions = SQLitePermissionStore(ids, audit)
    for principal in users.values():
        for permission in ("memory.read", "memory.write", "memory.share", "memory.history"):
            permissions.grant(
                sessions["owner"].token, principal.principal_id, permission, correlation_id=uuid4()
            )
    store = SQLiteMemoryStore(db, ids, audit)
    service = MemoryService(store, permissions)
    return db, ids, users, sessions, audit, permissions, store, service


def draft(text="secret", **kwargs):
    data = dict(
        kind="person",
        content={"text": text},
        source=Provenance(category="user_statement", source_ref="request:test"),
        consent_ref="consent:test",
        retention_ref="retention.manual",
    )
    return MemoryDraft(**(data | kwargs))


def create(runtime, user="owner", **kwargs):
    return runtime[-1].create(draft(**kwargs), session_token=runtime[3][user].token)


def test_default_private_scope_subject_is_not_reader_and_no_owner_bypass(runtime):
    _, _, users, sessions, _, _, _, service = runtime
    record = create(runtime, "a", subject_ids=(users["b"].principal_id,))
    assert record.access_scope.owner_id == record.creator_id == users["a"].principal_id
    assert record.access_scope.kind == "private"
    assert service.get(record.memory_id, session_token=sessions["a"].token) == record
    for user in ("b", "guest", "owner"):
        with pytest.raises(MemoryError, match="^memory_unavailable$"):
            service.get(record.memory_id, session_token=sessions[user].token)
        result = service.search(
            MemoryQuery(text="secret", subject_id=users["b"].principal_id),
            session_token=sessions[user].token,
        )
        assert result.records == () and result.next_cursor is None


@pytest.mark.parametrize("user", ["a", "b", "guest"])
def test_owner_private_denies_id_history_search_and_missing_id_is_indistinguishable(runtime, user):
    service = runtime[-1]
    token = runtime[3][user].token
    record = create(runtime)
    for memory_id in (record.memory_id, uuid4()):
        for historical in (False, True):
            with pytest.raises(MemoryError, match="^memory_unavailable$"):
                service.get(memory_id, session_token=token, historical=historical)
    assert (
        service.search(MemoryQuery(historical=True, text="secret"), session_token=token).records
        == ()
    )


def test_shared_scope_needs_explicit_membership_and_current_read_grant(runtime):
    _, _, users, sessions, _, permissions, _, service = runtime
    scope = MemoryScope(
        owner_id=users["owner"].principal_id, kind="shared", shared_with=(users["a"].principal_id,)
    )
    record = create(runtime, access_scope=scope)
    assert service.get(record.memory_id, session_token=sessions["a"].token) == record
    with pytest.raises(MemoryError, match="memory_unavailable"):
        service.get(record.memory_id, session_token=sessions["b"].token)
    permissions.revoke(
        sessions["owner"].token, users["a"].principal_id, "memory.read", correlation_id=uuid4()
    )
    with pytest.raises(MemoryError, match="access_denied"):
        service.get(record.memory_id, session_token=sessions["a"].token)
    with pytest.raises(MemoryError, match="access_denied"):
        service.search(MemoryQuery(), session_token=sessions["a"].token)


def test_revoke_record_recipient_does_not_require_deleting_record(runtime):
    _, _, users, sessions, _, _, _, service = runtime
    record = create(
        runtime,
        access_scope=MemoryScope(
            owner_id=users["owner"].principal_id,
            kind="shared",
            shared_with=(users["a"].principal_id, users["b"].principal_id),
        ),
    )
    service.revoke_access(
        record.memory_id,
        users["a"].principal_id,
        expected_revision=0,
        session_token=sessions["owner"].token,
    )
    with pytest.raises(MemoryError, match="memory_unavailable"):
        service.get(record.memory_id, session_token=sessions["a"].token)
    assert not service.search(MemoryQuery(), session_token=sessions["a"].token).records
    assert service.get(record.memory_id, session_token=sessions["b"].token).revision == 1
    service.revoke_access(
        record.memory_id,
        users["b"].principal_id,
        expected_revision=1,
        session_token=sessions["owner"].token,
    )
    assert (
        service.get(record.memory_id, session_token=sessions["owner"].token).access_scope.kind
        == "private"
    )

    changes = [
        r
        for r in runtime[4].records()
        if r.action == "memory.revoke_access" and r.outcome == "succeeded"
    ]
    assert len(changes) == 2
    assert all(f"scope:memory:{record.memory_id}" in r.policy_refs for r in changes)
    assert f"principal:{users['a'].principal_id}" in changes[0].policy_refs
    assert f"principal:{users['b'].principal_id}" in changes[1].policy_refs


def test_household_is_explicit_membership_plus_current_established_policy(runtime):
    _, _, users, sessions, _, permissions, _, service = runtime
    scope = MemoryScope(
        owner_id=users["owner"].principal_id,
        kind="household",
        shared_with=(users["a"].principal_id,),
        policy_ref="policy:memory.household",
    )
    record = create(runtime, access_scope=scope)
    for user in ("owner", "a", "b"):
        assert not service.search(MemoryQuery(), session_token=sessions[user].token).records
        permissions.grant(
            sessions["owner"].token,
            users[user].principal_id,
            "memory.household.read",
            correlation_id=uuid4(),
        )
    assert service.get(record.memory_id, session_token=sessions["a"].token) == record
    with pytest.raises(MemoryError, match="memory_unavailable"):
        service.get(record.memory_id, session_token=sessions["b"].token)
    permissions.revoke(
        sessions["owner"].token,
        users["a"].principal_id,
        "memory.household.read",
        correlation_id=uuid4(),
    )
    assert not service.search(MemoryQuery(), session_token=sessions["a"].token).records
    with pytest.raises(MemoryError, match="memory_unavailable"):
        service.get(record.memory_id, session_token=sessions["a"].token)


@pytest.mark.parametrize("claim", ["principal", "session", "json", "unknown", "session_object"])
def test_forged_identity_and_model_claims_are_not_credentials(runtime, claim):
    _, _, users, sessions, _, _, _, service = runtime
    token = {
        "principal": str(users["owner"].principal_id),
        "session": str(sessions["owner"].session_id),
        "json": json.dumps({"principal_id": str(users["owner"].principal_id), "kind": "owner"}),
        "unknown": "not-a-token",
        "session_object": runtime[1].get_session(sessions["owner"].session_id),
    }[claim]
    with pytest.raises(MemoryError, match="access_denied"):
        service.search(MemoryQuery(), session_token=token)
    with pytest.raises(MemoryError, match="access_denied"):
        service.create(draft(), session_token=token)


def test_creator_can_write_other_owner_only_with_owner_authorized_permission_and_cannot_read(
    runtime,
):
    _, _, users, sessions, _, permissions, _, service = runtime
    scope = MemoryScope(owner_id=users["a"].principal_id)
    with pytest.raises(MemoryError, match="access_denied"):
        create(runtime, access_scope=scope)
    permissions.grant(
        sessions["owner"].token,
        users["owner"].principal_id,
        "memory.write_others",
        correlation_id=uuid4(),
    )
    record = create(runtime, access_scope=scope)
    assert record.creator_id == users["owner"].principal_id
    assert service.get(record.memory_id, session_token=sessions["a"].token) == record
    with pytest.raises(MemoryError, match="memory_unavailable"):
        service.get(record.memory_id, session_token=sessions["owner"].token)
    # Even an owner-issued write_others grant does not make an ordinary user admin.
    permissions.grant(
        sessions["owner"].token,
        users["b"].principal_id,
        "memory.write_others",
        correlation_id=uuid4(),
    )
    with pytest.raises(MemoryError, match="access_denied"):
        create(runtime, "b", access_scope=scope)


@pytest.mark.parametrize("operation", ["supersede", "delete", "revoke_access", "set_retention"])
def test_shared_reader_and_system_owner_cannot_modify_other_principal_private_memory(
    runtime, operation
):
    _, _, users, sessions, _, _, _, service = runtime
    record = create(
        runtime,
        "a",
        access_scope=MemoryScope(
            owner_id=users["a"].principal_id, kind="shared", shared_with=(users["b"].principal_id,)
        ),
    )
    for user in ("owner", "b"):
        kwargs = dict(expected_revision=0, session_token=sessions[user].token)
        args = [record.memory_id]
        if operation == "supersede":
            args.append(draft())
        if operation == "revoke_access":
            args.append(users["b"].principal_id)
        if operation == "set_retention":
            kwargs.update(retention_ref="retention.short", expires_at=utc_now())
        with pytest.raises(MemoryError, match="memory_unavailable"):
            getattr(service, operation)(*args, **kwargs)


def test_supersession_preserves_provenance_and_explicit_authorized_history(runtime):
    service = runtime[-1]
    token = runtime[3]["owner"].token
    effective = utc_now() - timedelta(days=2)
    old = create(
        runtime, text="controller A", valid_from=effective, project_ref="project:slipstream"
    )
    unrelated = create(runtime, text="unrelated", project_ref="project:other")
    new = service.supersede(
        old.memory_id,
        draft("controller B", project_ref="project:slipstream"),
        expected_revision=0,
        session_token=token,
    )
    with pytest.raises(MemoryError, match="memory_unavailable"):
        service.get(old.memory_id, session_token=token)
    past = service.get(old.memory_id, session_token=token, historical=True, at=effective)
    assert past.content == old.content and past.source == old.source
    assert past.superseded_by == new.memory_id and new.supersedes == old.memory_id
    assert past.valid_to == new.valid_from and past.superseded_at >= old.created_at
    assert service.search(
        MemoryQuery(project_ref="project:slipstream"), session_token=token
    ).records == (new,)
    assert {
        r.memory_id
        for r in service.search(
            MemoryQuery(historical=True, project_ref="project:slipstream"), session_token=token
        ).records
    } == {old.memory_id, new.memory_id}
    assert service.get(unrelated.memory_id, session_token=token) == unrelated
    runtime[5].revoke(
        token, runtime[2]["owner"].principal_id, "memory.history", correlation_id=uuid4()
    )
    with pytest.raises(MemoryError, match="access_denied"):
        service.get(old.memory_id, session_token=token, historical=True)


def test_correction_cannot_change_scope_or_collection_basis(runtime):
    service = runtime[-1]
    token = runtime[3]["owner"].token
    old = create(runtime)
    with pytest.raises(MemoryError, match="validation_failure"):
        service.supersede(
            old.memory_id, draft(consent_ref="different"), expected_revision=0, session_token=token
        )
    with pytest.raises(MemoryError, match="validation_failure"):
        service.supersede(
            old.memory_id,
            draft(valid_from=utc_now() + timedelta(days=1)),
            expected_revision=0,
            session_token=token,
        )
    assert service.get(old.memory_id, session_token=token) == old


@pytest.mark.parametrize("mode", ["future", "past", "end_boundary"])
def test_validity_current_and_authorized_past_time(runtime, mode):
    service = runtime[-1]
    token = runtime[3]["owner"].token
    now = utc_now()
    start, end = {
        "future": (now + timedelta(days=1), None),
        "past": (now - timedelta(days=2), now - timedelta(days=1)),
        "end_boundary": (now - timedelta(days=2), now),
    }[mode]
    record = create(runtime, valid_from=start, valid_to=end)
    assert not service.search(MemoryQuery(), session_token=token).records
    assert service.get(record.memory_id, historical=True, at=start, session_token=token) == record
    if end:
        with pytest.raises(MemoryError, match="memory_unavailable"):
            service.get(record.memory_id, historical=True, at=end, session_token=token)


def test_tombstone_cannot_be_retrieved_by_history_search_or_rebuilt_index(runtime):
    db, _, _, sessions, _, _, _, service = runtime
    token = sessions["owner"].token
    record = create(runtime, text="deletion sentinel")
    service.delete(record.memory_id, expected_revision=0, session_token=token)
    for historical in (False, True):
        with pytest.raises(MemoryError, match="memory_unavailable"):
            service.get(record.memory_id, historical=historical, session_token=token)
        assert not service.search(
            MemoryQuery(historical=historical, text="deletion sentinel"), session_token=token
        ).records
    with db.transaction() as connection:
        row = connection.execute("SELECT search_text FROM memories").fetchone()
        assert row[0] == ""
        assert connection.execute("SELECT count(*) FROM memory_readers").fetchone()[0] == 0
    # Rebuild ordinary indexes: deletion remains authoritative, not an index-cache property.
    with db.transaction(write=True) as connection:
        connection.execute("REINDEX")
    assert not service.search(MemoryQuery(historical=True), session_token=token).records
    with pytest.raises(MemoryError, match="memory_unavailable"):
        service.set_retention(
            record.memory_id,
            retention_ref="forever",
            expires_at=None,
            expected_revision=1,
            session_token=token,
        )


def test_retention_expiry_cannot_be_bypassed_by_historical_date_or_extension(runtime):
    service = runtime[-1]
    token = runtime[3]["owner"].token
    record = create(runtime)
    service.set_retention(
        record.memory_id,
        retention_ref="retention.test",
        expires_at=utc_now(),
        expected_revision=0,
        session_token=token,
    )
    for historical in (False, True):
        assert not service.search(
            MemoryQuery(historical=historical, at=record.created_at), session_token=token
        ).records
        with pytest.raises(MemoryError, match="memory_unavailable"):
            service.get(
                record.memory_id, historical=historical, at=record.created_at, session_token=token
            )
    with pytest.raises(MemoryError, match="memory_unavailable"):
        service.set_retention(
            record.memory_id,
            retention_ref="forever",
            expires_at=None,
            expected_revision=1,
            session_token=token,
        )
    service.delete(record.memory_id, expected_revision=1, session_token=token)


def test_search_filters_literal_text_order_and_keyset_pages_do_not_leak_hidden_records(runtime):
    service = runtime[-1]
    sessions = runtime[3]
    users = runtime[2]
    visible = []
    for i in range(4):
        visible.append(
            create(
                runtime,
                "a",
                text=f"needle {i} %_",
                kind="custom.module.v1",
                subject_ids=(users["b"].principal_id,),
                project_ref="project:x",
            )
        )
        create(runtime, text="needle private hidden")
    query = MemoryQuery(
        text="%_",
        kind="custom.module.v1",
        subject_id=users["b"].principal_id,
        project_ref="project:x",
        limit=2,
    )
    first = service.search(query, session_token=sessions["a"].token)
    assert [r.memory_id for r in first.records] == [r.memory_id for r in reversed(visible[2:])]
    second = service.search(
        MemoryQuery.model_validate({**query.model_dump(), "cursor": first.next_cursor}),
        session_token=sessions["a"].token,
    )
    assert [r.memory_id for r in second.records] == [r.memory_id for r in reversed(visible[:2])]
    assert second.next_cursor is None
    assert not service.search(
        MemoryQuery(text="private hidden", limit=1), session_token=sessions["a"].token
    ).records
    assert "total" not in first.model_dump() and "score" not in first.model_dump()
    assert decode_contract(first.model_dump_json()) == first
    serialized = first.model_dump(mode="json")
    del serialized["next_cursor"]["schema_version"]
    with pytest.raises(ValueError, match="cursor version"):
        decode_contract(json.dumps(serialized))


@pytest.mark.parametrize("limit", [0, 101, -1, True])
def test_queries_are_bounded(limit):
    with pytest.raises(ValidationError):
        MemoryQuery(limit=limit)


@pytest.mark.parametrize(
    "bad", ["missing_consent", "direct_derived", "bad_interval", "oversized", "nan"]
)
def test_invalid_drafts_and_unsupported_persistent_versions(bad):
    values = draft().model_dump()
    if bad == "missing_consent":
        del values["consent_ref"]
    if bad == "direct_derived":
        values["source"] = Provenance(
            category="user_statement", source_ref="x", derived_from=("evidence:missing",)
        )
    if bad == "bad_interval":
        values.update(valid_from=utc_now(), valid_to=utc_now() - timedelta(days=1))
    if bad == "oversized":
        values["content"] = {"text": "x" * 65537}
    if bad == "nan":
        values["content"] = {"confidence": float("nan")}
    with pytest.raises((ValueError, ValidationError)):
        MemoryDraft.model_validate(values)


def test_derived_provenance_and_missing_reference_are_not_promoted_to_direct_evidence(runtime):
    source = Provenance(
        category="derived",
        source_ref="derived:test",
        derived_from=("evidence:unverified_missing",),
        model_ref="fake:model:v1",
    )
    record = create(runtime, source=source, confidence=0.3, kind="observation-derived")
    restored = decode_memory(record.model_dump_json())
    assert restored.source == source and restored.confidence == 0.3
    assert restored.source.category == "derived"
    for field in ("source", "access_scope"):
        data = record.model_dump(mode="json")
        del data[field]["schema_version"]
        with pytest.raises(ValueError):
            decode_memory(json.dumps(data))
    for version in (1, 3, True):
        data = record.model_dump(mode="json")
        data["schema_version"] = version
        with pytest.raises(ValueError):
            decode_memory(json.dumps(data))
    assert isinstance(
        decode_contract(
            runtime[-1]
            .get(record.memory_id, session_token=runtime[3]["owner"].token)
            .model_dump_json()
        ),
        MemoryRecord,
    )


def test_stale_revisions_never_overwrite_current_state(runtime):
    service = runtime[-1]
    token = runtime[3]["owner"].token
    record = create(runtime)
    service.set_retention(
        record.memory_id,
        retention_ref="new",
        expires_at=None,
        expected_revision=0,
        session_token=token,
    )
    with pytest.raises(MemoryError, match="revision_conflict"):
        service.delete(record.memory_id, expected_revision=0, session_token=token)
    assert service.get(record.memory_id, session_token=token).retention_ref == "new"


def test_atomic_supersession_and_mutation_audit_rollback(runtime, monkeypatch):
    import jarvis.storage.memory as adapter

    service = runtime[-1]
    token = runtime[3]["owner"].token
    record = create(runtime)

    def fail(*args):
        raise sqlite3.OperationalError("private content should not appear")

    monkeypatch.setattr(adapter, "_append_audit", fail)
    with pytest.raises(MemoryError, match="storage_unavailable"):
        service.supersede(
            record.memory_id, draft("changed"), expected_revision=0, session_token=token
        )
    assert service.get(record.memory_id, session_token=token) == record
    assert service.search(MemoryQuery(historical=True), session_token=token).records == (record,)


def test_audit_failure_denies_reads_and_writes_without_side_effect(runtime, monkeypatch):
    service = runtime[-1]
    token = runtime[3]["owner"].token
    record = create(runtime)

    def fail(record):
        raise RuntimeError("private payload")

    monkeypatch.setattr(runtime[4], "append", fail)
    with pytest.raises(MemoryError, match="audit_unavailable"):
        service.get(record.memory_id, session_token=token)
    with pytest.raises(MemoryError, match="audit_unavailable"):
        create(runtime, text="must not exist")
    with runtime[0].transaction() as connection:
        assert connection.execute("SELECT count(*) FROM memories").fetchone()[0] == 1


@pytest.mark.parametrize(
    "corruption", ["json", "version", "scope_index", "owner_index", "search_index"]
)
def test_corrupt_memory_fails_closed_with_sanitized_errors(runtime, corruption):
    db, _, _, sessions, _, _, _, service = runtime
    record = create(runtime)
    with db.transaction(write=True) as connection:
        if corruption == "scope_index":
            connection.execute(
                "INSERT INTO memory_readers VALUES(?,?)",
                (str(record.memory_id), str(runtime[2]["a"].principal_id)),
            )
        elif corruption == "owner_index":
            # Versioned metadata mismatch without changing immutable SQL ownership.
            data = record.model_dump(mode="json")
            data["access_scope"]["owner_id"] = str(uuid4())
            connection.execute("UPDATE memories SET revision=1,data=?", (json.dumps(data),))
        elif corruption == "search_index":
            connection.execute("UPDATE memories SET revision=1,search_text='poisoned'")
        else:
            data = record.model_dump(mode="json")
            data["schema_version"] = 99
            connection.execute(
                "UPDATE memories SET revision=1,data=?",
                ("{private" if corruption == "json" else json.dumps(data),),
            )
    with pytest.raises(MemoryError, match="^memory_record_invalid$"):
        service.get(record.memory_id, session_token=sessions["owner"].token)


def test_backend_has_no_public_unscoped_read_and_rejects_unbound_access(runtime):
    store = SQLiteMemoryStore(runtime[0], runtime[1], runtime[4])
    assert not hasattr(store, "get") and not hasattr(store, "all") and not hasattr(store, "search")
    with pytest.raises(MemoryError, match="memory_store_private"):
        store._get(uuid4(), uuid4(), access=object())
    with pytest.raises(ValueError, match="already bound"):
        runtime[6]._bind()


def test_private_content_and_search_inputs_never_appear_in_audits_or_logs(runtime, caplog):
    service = runtime[-1]
    token = runtime[3]["owner"].token
    record = create(runtime, text="ULTRA_PRIVATE_SENTINEL", project_ref="PRIVATE_PROJECT_SENTINEL")
    service.search(MemoryQuery(text="PRIVATE_QUERY_SENTINEL"), session_token=token)
    service.delete(record.memory_id, expected_revision=0, session_token=token)
    encoded = "\n".join(r.model_dump_json() for r in runtime[4].records())
    assert all(
        v not in encoded + caplog.text
        for v in (
            "ULTRA_PRIVATE_SENTINEL",
            "PRIVATE_PROJECT_SENTINEL",
            "PRIVATE_QUERY_SENTINEL",
            token,
        )
    )
    assert any(
        r.action == "memory.delete" and r.outcome == "succeeded" for r in runtime[4].records()
    )


def test_returned_record_mutation_does_not_modify_stored_data(runtime):
    service = runtime[-1]
    token = runtime[3]["owner"].token
    record = create(runtime)
    record.content["text"] = "caller changed"
    assert service.get(record.memory_id, session_token=token).content == {"text": "secret"}


def test_corruption_of_inaccessible_memory_does_not_change_missing_id_error(runtime):
    db, _, _, sessions, _, _, _, service = runtime
    record = create(runtime)
    with db.transaction(write=True) as connection:
        connection.execute("UPDATE memories SET revision=1,data='{corrupt'")
    for memory_id in (record.memory_id, uuid4()):
        with pytest.raises(MemoryError, match="^memory_unavailable$"):
            service.get(memory_id, session_token=sessions["a"].token)
    assert service.search(MemoryQuery(), session_token=sessions["a"].token).records == ()


def test_date_queries_require_current_history_permission_even_without_history_flag(runtime):
    _, _, users, sessions, _, permissions, _, service = runtime
    record = create(runtime)
    permissions.revoke(
        sessions["owner"].token,
        users["owner"].principal_id,
        "memory.history",
        correlation_id=uuid4(),
    )
    assert service.get(record.memory_id, session_token=sessions["owner"].token) == record
    with pytest.raises(MemoryError, match="access_denied"):
        service.get(record.memory_id, session_token=sessions["owner"].token, at=record.created_at)
    with pytest.raises(MemoryError, match="access_denied"):
        service.search(MemoryQuery(at=record.created_at), session_token=sessions["owner"].token)


def test_default_production_identity_wiring_refuses_fixture_memory(runtime):
    from jarvis.storage.security import SQLiteIdentityRepository

    db, _, _, sessions, audit, _, _, _ = runtime
    create(runtime)
    identity = SQLiteIdentityRepository(db)
    permissions = SQLitePermissionStore(identity, audit)
    service = MemoryService(SQLiteMemoryStore(db, identity, audit), permissions)
    with pytest.raises(MemoryError, match="access_denied"):
        service.search(MemoryQuery(), session_token=sessions["owner"].token)


def test_permission_changes_and_deletion_audits_link_to_current_policy_decision(runtime):
    service = runtime[-1]
    token = runtime[3]["owner"].token
    record = create(runtime)
    service.delete(record.memory_id, expected_revision=0, session_token=token)
    audit = runtime[4].records()
    for outcome in (
        r for r in audit if r.action.startswith("memory.") and r.outcome == "succeeded"
    ):
        assert outcome.decision_id is not None and outcome.proposal_id is not None
        assert f"scope:memory:{record.memory_id}" in outcome.policy_refs
        policy = next(
            r for r in audit if r.decision_id == outcome.decision_id and r.outcome == "allowed"
        )
        assert policy.correlation_id == outcome.correlation_id
        assert policy.principal_id == outcome.principal_id


def test_revoked_write_and_share_permissions_block_new_operations(runtime):
    _, _, users, sessions, _, permissions, _, service = runtime
    token = sessions["owner"].token
    record = create(runtime)
    permissions.revoke(token, users["owner"].principal_id, "memory.share", correlation_id=uuid4())
    scope = MemoryScope(
        owner_id=users["owner"].principal_id, kind="shared", shared_with=(users["a"].principal_id,)
    )
    with pytest.raises(MemoryError, match="access_denied"):
        create(runtime, access_scope=scope)
    permissions.revoke(token, users["owner"].principal_id, "memory.write", correlation_id=uuid4())
    with pytest.raises(MemoryError, match="access_denied"):
        create(runtime)
    with pytest.raises(MemoryError, match="access_denied"):
        service.delete(record.memory_id, expected_revision=0, session_token=token)
    assert service.get(record.memory_id, session_token=token) == record
