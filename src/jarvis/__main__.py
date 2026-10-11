"""Offline smoke check for installed contracts and capability metadata discovery."""

import argparse
import json
from pathlib import Path
from uuid import UUID, uuid4

from jarvis.capabilities import InMemoryCapabilityRegistry
from jarvis.config import Settings
from jarvis.core.contracts import ActionProposal, Capability, Principal, Session, decode_contract
from jarvis.observability import configure_logging


def smoke() -> dict[str, object]:
    settings = Settings.from_environment()
    logger = configure_logging(settings.log_level)
    principal = Principal(display_name="Local development principal")
    session = Session(principal_id=principal.principal_id, interface="smoke")
    registry = InMemoryCapabilityRegistry()
    registry.register(
        Capability(
            capability_id="development.echo",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            output_schema={"type": "object"},
            adapter_ref="development.fake",
        )
    )
    proposal = ActionProposal(
        session_id=session.session_id,
        capability_id=registry.get("development.echo").capability_id,
        arguments={"text": "offline foundation check"},
        correlation_id=uuid4(),
        origin="rule",
        source_ref="development.smoke",
    )
    if decode_contract(proposal.model_dump_json()) != proposal:
        raise RuntimeError("contract round trip failed")
    logger.info("foundation.smoke.passed", extra={"correlation_id": proposal.correlation_id})
    return {
        "status": "ok",
        "schema_version": proposal.schema_version,
        "capabilities": [item.capability_id for item in registry.discover()],
        "cloud_ai_enabled": settings.cloud_ai_enabled,
        "execution_enabled": False,
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description="JARVIS development foundation")
    parser.add_argument(
        "command",
        choices=[
            "smoke",
            "demo",
            "recovery-demo",
            "memory-demo",
            "cognition-demo",
            "world-demo",
            "initiative-demo",
            "notification-demo",
            "notifications",
            "initiatives",
            "chat",
            "db-init",
            "db-inspect",
            "db-backup",
        ],
    )
    parser.add_argument(
        "initiative_command",
        nargs="?",
        choices=[
            "list",
            "show",
            "approve",
            "deny",
            "cancel",
            "execute",
            "process",
            "recover",
            "configure",
            "policies",
            "pause",
            "read",
            "unread",
            "acknowledge",
            "dismiss",
            "expire",
            "preferences",
            "set-preferences",
            "bridge",
        ],
    )
    parser.add_argument("initiative_id", nargs="?")
    parser.add_argument("--json", action="store_true", help="structured notification output")
    parser.add_argument("--after", type=int, default=0, help="recipient inbox cursor")
    parser.add_argument("--text", help="literal recipient inbox search")
    parser.add_argument("--policy-file", type=Path)
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--destination", type=Path, help="new backup file (db-backup only)")
    parser.add_argument(
        "--database", type=Path, help="development conversation/initiative database"
    )
    parser.add_argument(
        "--identity",
        choices=["owner", "friend", "guest"],
        default="owner",
        help="DEVELOPMENT trusted host identity (chat/inbox)",
    )
    parser.add_argument("--conversation", type=UUID, help="resume owned conversation (chat only)")
    parser.add_argument("--model-provider", choices=["local", "fake"], help="chat provider")
    parser.add_argument("--local-endpoint", help="loopback OpenAI-compatible /v1 URL")
    parser.add_argument("--local-model", help="local server model name")
    args = parser.parse_args()
    if args.command == "notifications":
        from jarvis.notification_cli import run_notifications

        if args.initiative_command not in {
            "list",
            "show",
            "read",
            "unread",
            "acknowledge",
            "dismiss",
            "expire",
            "process",
            "recover",
            "bridge",
            "preferences",
            "set-preferences",
            "policies",
            "configure",
            "approve",
            "deny",
        }:
            parser.error("notifications requires a supported inbox operation")
        run_notifications(args)
    elif args.command == "notification-demo":
        from jarvis.notification_demo import run_notification_demo

        print(json.dumps(run_notification_demo()))
    elif args.command == "initiatives":
        from jarvis.initiative_cli import run_inbox

        if args.initiative_command is None:
            parser.error("initiatives requires an inbox operation")
        run_inbox(args)
    elif args.command == "initiative-demo":
        from jarvis.initiative_demo import run_initiative_demo

        print(json.dumps(run_initiative_demo()))
    elif args.command == "chat":
        from jarvis.chat import run_chat

        run_chat(args)
    elif args.command == "world-demo":
        from jarvis.world_demo import run_world_demo

        print(json.dumps(run_world_demo()))
    elif args.command == "cognition-demo":
        from jarvis.cognition_demo import run_cognition_demo

        print(json.dumps(run_cognition_demo()))
    elif args.command == "memory-demo":
        from jarvis.memory_demo import run_memory_demo

        print(json.dumps(run_memory_demo()))
    elif args.command == "recovery-demo":
        from jarvis.recovery_demo import run_recovery_demo

        print(json.dumps(run_recovery_demo()))
    elif args.command.startswith("db-"):
        from jarvis.storage.sqlite import SCHEMA_VERSION, SQLiteDatabase, SQLiteTaskStore

        database = SQLiteDatabase(Settings.from_environment().resolved_database_path)
        if args.command == "db-init":
            database.initialize()
            result = {
                "status": "ok",
                "database_path": str(database.path),
                "schema_version": SCHEMA_VERSION,
            }
        elif args.command == "db-backup":
            if args.destination is None:
                parser.error("db-backup requires --destination pointing to a new file")
            database.backup(args.destination)
            result = {"status": "ok", "backup_path": str(args.destination.resolve())}
        else:
            tasks = SQLiteTaskStore(database).all()
            result = {
                "status": "ok",
                "database_path": str(database.path),
                "schema_version": SCHEMA_VERSION,
                "tasks": [
                    {
                        "task_id": str(task.task_id),
                        "state": task.state,
                        "revision": task.revision,
                        "completed_steps": sum(step.state == "COMPLETED" for step in task.steps),
                    }
                    for task in tasks
                ],
            }
        print(json.dumps(result))
    elif args.command == "demo":
        from jarvis.development import run_demo

        configure_logging(Settings.from_environment().log_level)
        print(json.dumps(run_demo()))
    else:
        print(json.dumps(smoke()))


def main() -> None:
    from jarvis.storage.errors import StorageError

    try:
        _main()
    except StorageError as exc:
        print(
            json.dumps(
                {"status": "storage_failure", "error": {"code": exc.code, "message": exc.code}}
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
