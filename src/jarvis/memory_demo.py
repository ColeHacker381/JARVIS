"""Real two-process offline memory demonstration; fixture tokens travel only through pipes."""

import json
import os
import subprocess
import sys
from datetime import timedelta
from uuid import UUID, uuid4

from jarvis.config import Settings
from jarvis.core.contracts import Provenance
from jarvis.core.memory_records import MemoryDraft, MemoryQuery, MemoryScope
from jarvis.core.time import utc_now
from jarvis.memory import MemoryError, MemoryService
from jarvis.storage.memory import SQLiteMemoryStore
from jarvis.storage.security import SQLiteDevelopmentIdentityService, SQLitePermissionStore
from jarvis.storage.sqlite import SQLiteAuditSink, SQLiteDatabase


def _runtime(path):
    database = SQLiteDatabase(path)
    identity = SQLiteDevelopmentIdentityService(database)
    audit = SQLiteAuditSink(database)
    permissions = SQLitePermissionStore(identity, audit)
    service = MemoryService(SQLiteMemoryStore(database, identity, audit), permissions)
    return database, identity, permissions, service


def _draft(text, *, kind="semantic", **kwargs):
    return MemoryDraft(
        kind=kind,
        content={"text": text},
        source=Provenance(category="user_statement", source_ref="development.memory.request"),
        consent_ref="consent:offline_fixture",
        **kwargs,
    )


def _denied(service, memory_id, token):
    try:
        service.get(memory_id, session_token=token)
    except MemoryError as exc:
        if exc.code == "memory_unavailable":
            return True
        raise
    raise RuntimeError("privacy verification failed")


def _verify(service, info):
    ids = {k: UUID(v) for k, v in info["memories"].items()}
    tokens = info["tokens"]
    owner, friend, guest = (tokens[k] for k in ("owner", "friend", "guest"))
    assert service.get(ids["owner_private"], session_token=owner).content["text"] == "Owner tea"
    assert (
        service.get(ids["friend_private"], session_token=friend).content["text"] == "Friend coffee"
    )
    assert service.get(ids["shared"], session_token=friend).content["text"] == "Shared project"
    assert _denied(service, ids["owner_private"], friend)
    assert _denied(service, ids["friend_private"], owner)
    assert _denied(service, ids["owner_private"], guest)
    assert _denied(service, ids["shared"], guest)
    assert _denied(service, ids["deleted"], owner)
    current = service.search(MemoryQuery(project_ref="project:slipstream"), session_token=owner)
    history = service.search(
        MemoryQuery(project_ref="project:slipstream", historical=True), session_token=owner
    )
    assert [r.memory_id for r in current.records] == [ids["new_fact"]]
    assert {r.memory_id for r in history.records} == {ids["old_fact"], ids["new_fact"]}
    assert current.records[0].content["text"] == "Slipstream uses microcontroller B"
    old = service.get(ids["old_fact"], session_token=owner, historical=True)
    assert old.superseded_by == ids["new_fact"] and old.valid_to is not None
    assert old.source.source_ref == "development.memory.request"
    assert not service.search(MemoryQuery(text="Deleted note"), session_token=owner).records
    assert not service.search(MemoryQuery(text="Owner tea"), session_token=guest).records
    return {
        "private_retrieval": True,
        "explicit_sharing": True,
        "cross_user_denied": True,
        "guest_denied": True,
        "tombstone_hidden": True,
        "current_fact_count": len(current.records),
        "historical_fact_count": len(history.records),
        "current_fact": current.records[0].content,
        "provenance_preserved": True,
    }


def _worker(action, path, info):
    database, identity, permissions, service = _runtime(path)
    if action == "seed":
        database.initialize()
        principals = {"owner": identity.bootstrap_owner_for_testing("Simulated owner")}
        principals["friend"] = identity.create_principal_for_testing("Simulated friend")
        principals["guest"] = identity.create_principal_for_testing("Simulated guest", kind="guest")
        tokens = {
            k: identity.establish_session_for_testing(p.principal_id).token
            for k, p in principals.items()
        }
        for name in ("owner", "friend"):
            for permission in ("memory.read", "memory.write", "memory.history", "memory.share"):
                permissions.grant(
                    tokens["owner"],
                    principals[name].principal_id,
                    permission,
                    correlation_id=uuid4(),
                )
        permissions.grant(
            tokens["owner"], principals["guest"].principal_id, "memory.read", correlation_id=uuid4()
        )
        memories = {}
        memories["owner_private"] = service.create(
            _draft("Owner tea", kind="person", subject_ids=(principals["owner"].principal_id,)),
            session_token=tokens["owner"],
        )
        memories["friend_private"] = service.create(
            _draft(
                "Friend coffee", kind="person", subject_ids=(principals["friend"].principal_id,)
            ),
            session_token=tokens["friend"],
        )
        shared = MemoryScope(
            owner_id=principals["owner"].principal_id,
            kind="shared",
            shared_with=(principals["friend"].principal_id,),
        )
        memories["shared"] = service.create(
            _draft("Shared project", access_scope=shared), session_token=tokens["owner"]
        )
        memories["old_fact"] = service.create(
            _draft(
                "Slipstream uses microcontroller A",
                project_ref="project:slipstream",
                valid_from=utc_now() - timedelta(days=1),
            ),
            session_token=tokens["owner"],
        )
        memories["new_fact"] = service.supersede(
            memories["old_fact"].memory_id,
            _draft("Slipstream uses microcontroller B", project_ref="project:slipstream"),
            expected_revision=0,
            session_token=tokens["owner"],
        )
        memories["deleted"] = service.create(_draft("Deleted note"), session_token=tokens["owner"])
        service.delete(
            memories["deleted"].memory_id, expected_revision=0, session_token=tokens["owner"]
        )
        info = {
            "memories": {k: str(r.memory_id) for k, r in memories.items()},
            "tokens": tokens,
            "principals": {k: str(p.principal_id) for k, p in principals.items()},
        }
    info["checks"] = _verify(service, info)
    info["pid"] = os.getpid()
    return info


def run_memory_demo():
    root = Settings.from_environment().data_dir.expanduser().resolve() / f"memory-demo-{uuid4()}"
    root.mkdir(parents=True, mode=0o700)
    path = root / "journal.sqlite3"

    def worker(action, info):
        result = subprocess.run(
            [sys.executable, "-m", "jarvis.memory_demo", action, str(path)],
            input=json.dumps(info),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode:
            raise RuntimeError("memory_demo_worker_failed")
        return json.loads(result.stdout)

    seed = worker("seed", {})
    reopened = worker("reopen", seed)
    assert seed["pid"] != reopened["pid"] != os.getpid()
    return {
        "status": "ok",
        "mode": "offline_test_fixture",
        "database_path": str(path),
        "process_ids": [seed["pid"], reopened["pid"]],
        "before_restart": seed["checks"],
        "after_restart": reopened["checks"],
    }


if __name__ == "__main__":
    try:
        print(json.dumps(_worker(sys.argv[1], sys.argv[2], json.load(sys.stdin))))
    except Exception:
        # Never print exceptions containing fixture data or credentials.
        print(json.dumps({"status": "memory_demo_worker_failed"}))
        raise SystemExit(1) from None
