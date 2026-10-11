"""Trusted development-host inbox. No public authentication or external sends."""

import json
from pathlib import Path
from uuid import UUID

from jarvis.cognitive_runtime import development_runtime
from jarvis.core.notification_records import (
    NotificationPolicy,
    NotificationPreference,
    decode_notification,
)
from jarvis.identity import IdentityError
from jarvis.notifications import NotificationError


def run_notifications(args):
    path = args.database or Path(".jarvis/notifications/journal.sqlite3")
    runtime, credentials = None, None
    try:
        runtime = development_runtime(path, bootstrap=not path.expanduser().exists())
        credentials = runtime.credentials(args.identity)
        service, token = runtime.notifications, credentials.token
        operation = args.initiative_command
        if operation == "list":
            records, cursor = service.list(session_token=token, after=args.after, text=args.text)
            result = {
                "notifications": [r.model_dump(mode="json") for r in records],
                "next_cursor": cursor,
            }
        elif operation == "preferences":
            result = service.preferences(session_token=token).model_dump(mode="json")
        elif operation == "policies":
            result = [p.model_dump(mode="json") for p in service.policies(session_token=token)]
        elif operation in {"configure", "set-preferences"}:
            if args.policy_file is None:
                raise NotificationError("policy_file_required")
            value = decode_notification(args.policy_file.read_text())
            model = NotificationPolicy if operation == "configure" else NotificationPreference
            if not isinstance(value, model):
                raise NotificationError("notification_validation_failure")
            method = service.configure if operation == "configure" else service.set_preferences
            result = method(
                value, session_token=token, expected_revision=args.expected_revision
            ).model_dump(mode="json")
        elif operation in {"process", "recover", "bridge"}:
            created = service.bridge(session_token=token) if operation == "bridge" else ()
            deliveries = (
                () if operation == "bridge" else getattr(service, operation)(session_token=token)
            )
            result = {
                "created": [r.model_dump(mode="json") for r in created],
                "deliveries": [d.model_dump(mode="json") for d in deliveries],
            }
        else:
            if args.initiative_id is None:
                raise NotificationError("notification_id_required")
            notification_id = UUID(args.initiative_id)
            if operation == "show":
                result = {
                    "notification": service.get(notification_id, session_token=token).model_dump(
                        mode="json"
                    ),
                    "deliveries": [
                        d.model_dump(mode="json")
                        for d in service.deliveries(notification_id, session_token=token)
                    ],
                    "current_source": service.source_context(notification_id, session_token=token),
                }
            elif operation in {"approve", "deny"}:
                if args.expected_revision is None:
                    raise NotificationError("expected_revision_required")
                result = service.approval(
                    notification_id,
                    session_token=token,
                    expected_revision=args.expected_revision,
                    deny=operation == "deny",
                ).model_dump(mode="json")
            else:
                if args.expected_revision is None:
                    raise NotificationError("expected_revision_required")
                result = service.interact(
                    notification_id,
                    operation,
                    session_token=token,
                    expected_revision=args.expected_revision,
                ).model_dump(mode="json")
        if args.json:
            print(
                json.dumps(
                    {
                        "mode": "development_only_trusted_host_identity",
                        "simulated": True,
                        "result": result,
                    }
                )
            )
        else:
            print("Development-only trusted host identity; all transports local/simulated.")
            if operation == "list":
                for n in result["notifications"]:
                    print(
                        f"{n['notification_id']} r{n['revision']} {n['inbox_state']} "
                        f"{n['content']['title']}"
                    )
                print(f"Next cursor: {result['next_cursor']}")
            elif operation == "show":
                n = result["notification"]
                print(
                    f"{n['notification_id']} r{n['revision']} {n['inbox_state']}: "
                    f"{n['content']['title']}"
                )
                print(n["content"]["text"])
                print(
                    "Transport: "
                    + ", ".join(
                        f"{d['channel']['channel_id']}={d['state']}" for d in result["deliveries"]
                    )
                )
                print(
                    f"Current source: {result['current_source']['state']} "
                    "(not human receipt/approval)"
                )
            elif operation in {"process", "recover", "bridge"}:
                print(f"Created: {len(result['created'])}; processed: {len(result['deliveries'])}")
                for delivery in result["deliveries"]:
                    print(
                        f"{delivery['delivery_id']} {delivery['channel']['channel_id']} "
                        f"{delivery['state']} attempts={len(delivery['attempts'])} "
                        f"reason={delivery['reason_code']}"
                    )
            elif operation in {"preferences", "set-preferences"}:
                print(f"Preferences r{result['revision']}; timezone={result['timezone']}")
                print("Categories: " + ", ".join(result["categories"]))
                print("Channels: " + ", ".join(result["channels"]))
                print(
                    f"Quiet minutes: {result['quiet_start_minute']}–{result['quiet_end_minute']}; "
                    f"minimum priority={result['minimum_priority']}"
                )
            elif operation == "policies":
                for policy in result:
                    print(
                        f"{policy['policy_id']} r{policy['revision']} "
                        f"enabled={policy['enabled']} source={policy['source_kind']} "
                        f"priority={policy['priority']} channels={','.join(policy['channels'])}"
                    )
            elif operation == "configure":
                print(f"Policy {result['policy_id']} r{result['revision']} configured.")
            elif operation in {"approve", "deny"}:
                print(f"Initiative {result['initiative_id']}: {result['state']}")
            else:
                print(f"{result['notification_id']} r{result['revision']} {result['inbox_state']}")
    except (NotificationError, IdentityError, ValueError, OSError) as exc:
        print(
            json.dumps(
                {
                    "status": "notification_failure",
                    "error": getattr(exc, "code", None)
                    or getattr(exc, "reason_code", "notification_validation_failure"),
                }
            )
        )
        raise SystemExit(1) from None
    finally:
        if runtime is not None and credentials is not None:
            runtime.identities.close_session_for_testing(credentials.token)
