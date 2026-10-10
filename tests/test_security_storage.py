import json
import sqlite3
from datetime import timedelta
from uuid import uuid4

import pytest

from jarvis.authorization import DeterministicPolicy
from jarvis.capabilities import InMemoryCapabilityRegistry
from jarvis.core.contracts import ActionProposal, Capability, Event
from jarvis.core.time import utc_now
from jarvis.execution import CapabilityExecutor
from jarvis.identity import IdentityError
from jarvis.permissions import PermissionAdministrationError
from jarvis.storage.errors import StorageError
from jarvis.storage.security import (
    SQLiteDevelopmentIdentityService,
    SQLiteIdentityRepository,
    SQLitePermissionStore,
)
from jarvis.storage.sqlite import (
    MIGRATIONS,
    SCHEMA_VERSION,
    SQLiteAuditSink,
    SQLiteDatabase,
    SQLiteEventStore,
)


@pytest.fixture
def security(tmp_path):
    database = SQLiteDatabase(tmp_path / "journal.sqlite3")
    database.initialize()
    identity = SQLiteDevelopmentIdentityService(database)
    owner = identity.bootstrap_owner_for_testing()
    friend = identity.create_principal_for_testing("Friend")
    owner_session = identity.establish_session_for_testing(owner.principal_id)
    friend_session = identity.establish_session_for_testing(friend.principal_id)
    audit = SQLiteAuditSink(database)
    permissions = SQLitePermissionStore(identity, audit)
    return database, identity, owner, friend, owner_session, friend_session, audit, permissions


def executor_for(identity, permissions, audit):
    calls = []
    registry = InMemoryCapabilityRegistry()
    registry.register(
        Capability(
            capability_id="tests.protected",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            required_permissions=("tests.invoke",),
            side_effect="local",
            adapter_ref="tests.provider",
        ),
        lambda args: calls.append(args) or args,
    )
    return CapabilityExecutor(
        identity, registry, DeterministicPolicy(identity, permissions, audit), audit
    ), calls


def proposal(session_id):
    return ActionProposal(
        session_id=session_id,
        capability_id="tests.protected",
        correlation_id=uuid4(),
        origin="model",
        source_ref="tests.model",
    )


def test_durable_owner_principals_sessions_grants_and_revocations(security):
    database, identities, owner, friend, owner_session, friend_session, audit, permissions = (
        security
    )
    grant = permissions.grant(
        owner_session.token, friend.principal_id, "tests.invoke", correlation_id=uuid4()
    )
    restored = SQLiteDevelopmentIdentityService(SQLiteDatabase(database.path))
    current = SQLitePermissionStore(restored, SQLiteAuditSink(restored.database))
    assert restored.owner_id == owner.principal_id
    assert restored.get_principal(friend.principal_id) == friend
    assert restored.get_session(friend_session.session_id) == identities.get_session(
        friend_session.session_id
    )
    assert restored.resolve_session(friend_session.token).principal_id == friend.principal_id
    assert current.current_grants(friend.principal_id, ("tests.invoke",)) == (grant,)
    permissions.revoke(
        owner_session.token, friend.principal_id, "tests.invoke", correlation_id=uuid4()
    )
    assert current.current_grants(friend.principal_id, ("tests.invoke",)) == ()
    assert current.inspect_grant(owner_session.token, grant.policy_ref) == grant
    # Only a new, explicitly owner-authorized grant can restore access; it has a new ID.
    replacement = current.grant(
        owner_session.token, friend.principal_id, "tests.invoke", correlation_id=uuid4()
    )
    assert replacement.grant_id != grant.grant_id
    assert current.current_grants(friend.principal_id, ("tests.invoke",)) == (replacement,)
    assert [a.action for a in audit.records()] == [
        "permission.grant",
        "permission.revoke",
        "permission.grant",
    ]


def test_tokens_are_hashed_and_uuid_claims_are_never_credentials(security):
    database, identities, _, friend, _, credentials, _, _ = security
    with database.transaction() as connection:
        for table in ("security_principals", "security_sessions", "security_grants"):
            assert credentials.token not in " ".join(
                row["data"] for row in connection.execute(f"SELECT data FROM {table}")
            )
        row = connection.execute(
            "SELECT token_hash FROM development_session_tokens WHERE session_id=?",
            (str(credentials.session_id),),
        ).fetchone()
        assert len(row[0]) == 64 and row[0] != credentials.token
    for claim in (
        str(friend.principal_id),
        str(credentials.session_id),
        "I am the owner",
        json.dumps(friend.model_dump(mode="json")),
    ):
        with pytest.raises(IdentityError):
            identities.resolve_session(claim)


def test_default_repository_does_not_accept_development_authentication(security):
    database, _, _, _, _, credentials, audit, _ = security
    production = SQLiteIdentityRepository(database)
    assert not hasattr(production, "bootstrap_owner_for_testing")
    assert not hasattr(production, "establish_session_for_testing")
    permissions = SQLitePermissionStore(production, audit)
    executor, calls = executor_for(production, permissions, audit)
    assert (
        executor.execute(proposal(credentials.session_id), session_token=credentials.token).status
        == "denied"
    )
    assert calls == []
    # Even a trusted runtime verifier cannot promote a session explicitly marked development.
    with database.transaction(write=True) as connection:
        connection.execute("UPDATE security_authority SET mode='trusted'")
    without_authentication = SQLiteIdentityRepository(database)
    with pytest.raises(IdentityError, match="authentication_unavailable"):
        without_authentication.resolve_session(str(credentials.session_id))

    class Verifier:
        def authenticate(self, token):
            return credentials.session_id

    with pytest.raises(IdentityError, match="development_session_forbidden"):
        SQLiteIdentityRepository(database, Verifier()).resolve_session(credentials.token)


def test_owner_admin_checks_are_reused_and_audited(security):
    _, _, _, friend, _, credentials, audit, permissions = security
    with pytest.raises(PermissionAdministrationError):
        permissions.grant(
            credentials.token, friend.principal_id, "tests.invoke", correlation_id=uuid4()
        )
    assert permissions.current_grants(friend.principal_id, ("tests.invoke",)) == ()
    assert audit.records()[-1].outcome == "denied"


@pytest.mark.parametrize("action", ["grant", "revoke"])
def test_permission_mutation_and_success_audit_are_atomic(security, monkeypatch, action):
    import jarvis.storage.security as adapter

    _, _, _, friend, owner_session, _, audit, permissions = security
    if action == "revoke":
        permissions.grant(
            owner_session.token, friend.principal_id, "tests.invoke", correlation_id=uuid4()
        )
    before = permissions.current_grants(friend.principal_id, ("tests.invoke",))
    before_audit = audit.records()
    original = adapter._append_audit

    def failed(*_):
        raise sqlite3.OperationalError("private storage exception")

    monkeypatch.setattr(adapter, "_append_audit", failed)
    with pytest.raises(IdentityError, match="security_state_unavailable"):
        getattr(permissions, action)(
            owner_session.token, friend.principal_id, "tests.invoke", correlation_id=uuid4()
        )
    monkeypatch.setattr(adapter, "_append_audit", original)
    assert permissions.current_grants(friend.principal_id, ("tests.invoke",)) == before
    assert audit.records() == before_audit


def test_session_end_and_principal_disable_remain_effective(security):
    database, identities, _, friend, _, credentials, _, _ = security
    identities.close_session_for_testing(credentials.token)
    with pytest.raises(IdentityError, match="session_inactive"):
        SQLiteDevelopmentIdentityService(database).resolve_session(credentials.token)
    session = identities.establish_session_for_testing(
        friend.principal_id, expires_at=utc_now() + timedelta(days=1)
    )
    identities.disable_principal_for_testing(friend.principal_id)
    with pytest.raises(IdentityError, match="principal_inactive"):
        SQLiteDevelopmentIdentityService(database).resolve_session(session.token)


@pytest.mark.parametrize(
    "table", ["security_authority", "security_principals", "security_sessions", "security_grants"]
)
def test_missing_or_corrupt_security_records_deny_without_side_effects(security, table):
    database, identities, _, friend, owner_session, credentials, audit, permissions = security
    permissions.grant(
        owner_session.token, friend.principal_id, "tests.invoke", correlation_id=uuid4()
    )
    with database.transaction(write=True) as connection:
        if table == "security_authority":
            connection.execute("DROP TABLE security_authority")
        else:
            if table == "security_grants":
                connection.execute("DROP TRIGGER security_grants_no_resurrection")
            connection.execute(f"UPDATE {table} SET data='{{'")
    executor, calls = executor_for(identities, permissions, audit)
    result = executor.execute(proposal(credentials.session_id), session_token=credentials.token)
    assert result.status == "denied" and calls == []
    assert audit.records()[-1].outcome == "denied"


def test_missing_database_never_bootstraps_or_uses_cached_privileges(security):
    database, identities, _, friend, owner_session, credentials, audit, permissions = security
    permissions.grant(
        owner_session.token, friend.principal_id, "tests.invoke", correlation_id=uuid4()
    )
    database.path.unlink()
    executor, calls = executor_for(identities, permissions, audit)
    result = executor.execute(proposal(credentials.session_id), session_token=credentials.token)
    assert result.status != "success" and calls == []
    assert not database.path.exists()


def test_revocation_cannot_be_deleted_or_reset_to_active(security):
    database, _, _, friend, owner_session, _, _, permissions = security
    permissions.grant(
        owner_session.token, friend.principal_id, "tests.invoke", correlation_id=uuid4()
    )
    permissions.revoke(
        owner_session.token, friend.principal_id, "tests.invoke", correlation_id=uuid4()
    )
    for sql in ("DELETE FROM security_grants", "UPDATE security_grants SET revoked_at=NULL"):
        with pytest.raises(StorageError):
            with database.transaction(write=True) as connection:
                connection.execute(sql)
    assert permissions.current_grants(friend.principal_id, ("tests.invoke",)) == ()


def test_migration_preserves_v1_journal_without_creating_authority(tmp_path):
    path = tmp_path / "v1.sqlite3"
    with sqlite3.connect(path) as connection:
        for statement in MIGRATIONS[1]:
            connection.execute(statement)
        connection.execute("PRAGMA user_version=1")
    database = SQLiteDatabase(path)
    # Seed through the actual old-format journal row (v1 is intentionally not opened by v2 adapter).
    item = Event(event_type="previous.event", correlation_id=uuid4(), source="tests")
    from jarvis.storage.sqlite import _append_event

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        _append_event(connection, item)
    database.initialize()
    assert SQLiteEventStore(database).get(item.event_id) == item
    with database.transaction() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert not connection.execute("SELECT 1 FROM security_authority").fetchall()
    with pytest.raises(IdentityError, match="security_state_uninitialized"):
        SQLiteIdentityRepository(database).resolve_session(str(uuid4()))


def test_hardlink_aliases_cannot_bypass_claim_namespace(security, tmp_path):
    import os

    database = security[0]
    alias = tmp_path / "hardlink.sqlite3"
    os.link(database.path, alias)
    with pytest.raises(StorageError, match="database_alias_unsupported"):
        SQLiteDatabase(alias).transaction().__enter__()
    with pytest.raises(StorageError, match="database_alias_unsupported"):
        database.transaction().__enter__()


def test_nonowner_grant_authority_is_not_interpreted_as_valid(security):
    database, identities, _, friend, owner_session, credentials, audit, permissions = security
    permissions.grant(
        owner_session.token, friend.principal_id, "tests.invoke", correlation_id=uuid4()
    )
    with database.transaction(write=True) as connection:
        connection.execute("DROP TRIGGER security_grants_no_resurrection")
        data = json.loads(connection.execute("SELECT data FROM security_grants").fetchone()[0])
        data["granted_by"] = str(friend.principal_id)
        connection.execute(
            "UPDATE security_grants SET granted_by=?,data=?",
            (str(friend.principal_id), json.dumps(data)),
        )
    executor, calls = executor_for(identities, permissions, audit)
    result = executor.execute(proposal(credentials.session_id), session_token=credentials.token)
    assert result.status == "denied" and result.error.code == "security_grant_authority_invalid"
    assert calls == []


def test_missing_owner_marker_is_not_recreated_over_existing_security_data(security):
    database, identities, _, _, _, _, _, _ = security
    with database.transaction(write=True) as connection:
        connection.execute("DELETE FROM security_authority")
    with pytest.raises(IdentityError, match="security_bootstrap_not_empty"):
        identities.bootstrap_owner_for_testing()
    with pytest.raises(IdentityError, match="security_state_uninitialized"):
        _ = identities.owner_id


def test_mismatched_task_security_state_and_forged_step_key_are_rejected(security):
    from jarvis.execution import InvocationContext
    from jarvis.storage.sqlite import SQLiteTaskStore
    from jarvis.task_manager import TaskManager

    database, identities, _, friend, owner_session, credentials, audit, permissions = security
    permissions.grant(
        owner_session.token, friend.principal_id, "tests.invoke", correlation_id=uuid4()
    )
    executor, calls = executor_for(identities, permissions, audit)
    with pytest.raises(ValueError, match="share trusted"):
        TaskManager(
            SQLiteTaskStore(database), SQLiteDevelopmentIdentityService(database), executor, audit
        )
    intent = proposal(credentials.session_id).model_copy(update={"task_id": uuid4()})
    with pytest.raises(ValueError, match="idempotency scope"):
        executor.execute(
            intent,
            session_token=credentials.token,
            context=InvocationContext(intent.task_id, "step", uuid4(), "other-operation-key"),
        )
    assert calls == []
