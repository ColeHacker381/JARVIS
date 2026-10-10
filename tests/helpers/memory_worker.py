"""Process fixture. Credentials use stdin/stdout IPC, never files, arguments or logs."""

import json
import os
import sys
from uuid import UUID

from jarvis.core.contracts import Provenance
from jarvis.core.memory_records import MemoryDraft, MemoryQuery
from jarvis.core.time import utc_now
from jarvis.memory import MemoryError
from jarvis.memory_demo import _runtime, _worker


def respond(value):
    print(json.dumps(value), flush=True)


def run(request):
    action = request["action"]
    path = request["path"]
    if action == "init":
        return _worker("seed", path, {})
    db, ids, permissions, service = _runtime(path)
    token = request.get("token", request["tokens"]["owner"])
    memory_id = UUID(request.get("memory_id", request["memories"]["owner_private"]))
    if request.get("barrier"):
        respond({"event": "ready", "pid": os.getpid()})
        if sys.stdin.readline().strip() != "GO":
            raise RuntimeError("barrier_protocol")
    if action == "get":
        record = service.get(
            memory_id, session_token=token, historical=request.get("historical", False)
        )
        return {"status": "ok", "record": record.model_dump(mode="json")}
    if action == "search":
        result = service.search(
            MemoryQuery.model_validate_json(json.dumps(request.get("query", {}))),
            session_token=token,
        )
        return {"status": "ok", "result": result.model_dump(mode="json")}
    if action == "retention":
        service.set_retention(
            memory_id,
            expected_revision=request.get("revision", 0),
            retention_ref=request.get("retention_ref", "retention.test"),
            expires_at=utc_now() if request.get("expire") else None,
            session_token=token,
        )
    elif action == "delete":
        service.delete(memory_id, expected_revision=request.get("revision", 0), session_token=token)
    elif action == "revoke_access":
        service.revoke_access(
            memory_id,
            UUID(request["principals"]["friend"]),
            expected_revision=request.get("revision", 0),
            session_token=token,
        )
    elif action == "revoke_grant":
        from uuid import uuid4

        permissions.revoke(
            request["tokens"]["owner"],
            UUID(request["principals"]["friend"]),
            "memory.read",
            correlation_id=uuid4(),
        )
    elif action == "derived":
        record = service.create(
            MemoryDraft(
                kind="observation-derived",
                content={"text": "derived claim"},
                source=Provenance(
                    category="derived",
                    source_ref="source:fake",
                    derived_from=("evidence:unverified",),
                    model_ref="fake:model:v1",
                ),
                consent_ref="consent:offline_fixture",
                confidence=0.2,
            ),
            session_token=token,
        )
        return {"status": "ok", "record": record.model_dump(mode="json")}
    elif action == "audit":
        return {
            "status": "ok",
            "audits": [r.model_dump(mode="json") for r in service._audit.records()],
        }
    else:
        raise RuntimeError("unknown_action")
    return {"status": "ok"}


if __name__ == "__main__":
    try:
        respond(run(json.loads(sys.stdin.readline())))
    except MemoryError as exc:
        respond({"status": "error", "error": exc.detail.model_dump(mode="json")})
    except Exception:
        respond({"status": "fixture_failure"})
        raise SystemExit(1) from None
