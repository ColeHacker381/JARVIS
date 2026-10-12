"""Minimal local text CLI. Host-selected identities are DEVELOPMENT ONLY, not authentication."""

import asyncio
import json

from jarvis.cognitive_runtime import development_runtime
from jarvis.config import Settings
from jarvis.conversations import ConversationError
from jarvis.identity import IdentityError


async def _chat(args):
    settings = Settings.from_environment()
    path = args.database or settings.resolved_chat_database_path
    runtime = development_runtime(
        path,
        bootstrap=not path.expanduser().exists(),
        provider=args.model_provider or settings.model_provider,
        endpoint=args.local_endpoint or settings.local_model_endpoint,
        model=args.local_model or settings.local_model_name,
        timeout=settings.model_timeout_seconds,
        response_format=settings.local_model_response_format,
    )
    credentials = runtime.credentials(args.identity)
    try:
        conversation = (
            runtime.conversations.get(args.conversation, session_token=credentials.token)
            if args.conversation
            else runtime.conversations.start(session_token=credentials.token)
        )
        print(
            json.dumps(
                {
                    "mode": "development_only_trusted_host_identity",
                    "identity": args.identity,
                    "conversation_id": str(conversation.conversation_id),
                    "database_path": str(runtime.database.path),
                    "model": runtime.coordinator.models.identity().model_dump(mode="json"),
                    "notice": "Deterministic fake, not AI reasoning; simulated devices."
                    if runtime.coordinator.models.selected == "fake"
                    else "Local connectivity is checked on requests; failures are reported.",
                }
            )
        )
        while True:
            try:
                text = input("You> ")
            except EOFError:
                break
            if text.strip().casefold() in ("/exit", "/quit", "exit", "quit"):
                break
            if not text.strip():
                continue
            response = await runtime.coordinator.respond(
                conversation.conversation_id, text, session_token=credentials.token
            )
            print("JARVIS> " + response.text)
            print(json.dumps(response.model_dump(mode="json")))
    finally:
        runtime.identities.close_session_for_testing(credentials.token)


def run_chat(args):
    try:
        asyncio.run(_chat(args))
    except KeyboardInterrupt:
        print("\nChat stopped. Persisted tasks remain inspectable and recoverable.")
    except (ConversationError, IdentityError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "chat_failure",
                    "error": getattr(exc, "code", None)
                    or getattr(exc, "reason_code", None)
                    or "configuration_invalid",
                }
            )
        )
        raise SystemExit(1) from None
