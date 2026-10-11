"""Trusted development-host initiative inbox; IDs never authenticate callers."""

import json
from pathlib import Path

from jarvis.cognitive_runtime import development_runtime
from jarvis.core.initiative_records import InitiativePolicy, decode_initiative
from jarvis.initiative import InitiativeError


def run_inbox(args):
    path = args.database or Path(".jarvis/initiatives/journal.sqlite3")
    runtime = development_runtime(path, bootstrap=not path.expanduser().exists())
    credentials = runtime.credentials(args.identity)
    engine, token = runtime.initiatives, credentials.token
    operation = args.initiative_command
    try:
        if operation == "list":
            result = [r.model_dump(mode="json") for r in engine.list(session_token=token)]
        elif operation == "policies":
            result = [p.model_dump(mode="json") for p in engine.policies(session_token=token)]
        elif operation == "configure":
            if args.policy_file is None:
                raise InitiativeError("policy_file_required")
            value = decode_initiative(args.policy_file.read_text())
            if not isinstance(value, InitiativePolicy):
                raise InitiativeError("initiative_validation_failure")
            result = engine.configure(
                value, session_token=token, expected_revision=args.expected_revision
            ).model_dump(mode="json")
        elif operation == "process":
            result = [r.model_dump(mode="json") for r in engine.process(session_token=token)]
        elif operation == "recover":
            result = [r.model_dump(mode="json") for r in engine.recover(session_token=token)]
        elif operation == "pause":
            if args.initiative_id is None or args.expected_revision is None:
                raise InitiativeError("policy_id_and_revision_required")
            result = engine.pause(
                args.initiative_id, session_token=token, expected_revision=args.expected_revision
            ).model_dump(mode="json")
        else:
            if args.initiative_id is None:
                raise InitiativeError("initiative_id_required")
            from uuid import UUID

            initiative_id = UUID(args.initiative_id)
            method = {
                "show": engine.get,
                "approve": engine.approve,
                "deny": engine.deny,
                "cancel": engine.cancel,
                "execute": engine.execute,
            }[operation]
            record = method(initiative_id, session_token=token)
            if operation == "approve" and record.state == "APPROVED":
                record = engine.execute(initiative_id, session_token=token)
            result = record.model_dump(mode="json")
        print(
            json.dumps(
                {
                    "mode": "development_only_trusted_host_identity",
                    "simulated": True,
                    "result": result,
                }
            )
        )
    except (InitiativeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "initiative_failure",
                    "error": getattr(exc, "code", "initiative_validation_failure"),
                }
            )
        )
        raise SystemExit(1) from None
    finally:
        runtime.identities.close_session_for_testing(token)
