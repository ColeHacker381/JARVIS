"""Host-only operator commands and foreground loopback development deployment."""

import argparse
import asyncio
import json
import os
import signal
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import uvicorn
from pydantic import ValidationError

from jarvis.application import FRIEND_ID, GUEST_ID, build_application
from jarvis.identity import IdentityError
from jarvis.local_api import create_api
from jarvis.local_credentials import (
    LocalDevelopmentIdentityRepository,
    issue_credential,
    resolve_worker_tokens,
    revoke_credential,
    write_private_json,
)
from jarvis.models import OpenAILocalModel
from jarvis.observability import configure_logging
from jarvis.runtime_config import RuntimeSettings
from jarvis.runtime_service import RuntimeService
from jarvis.storage.errors import StorageError
from jarvis.storage.sqlite import SCHEMA_VERSION, SQLiteDatabase, _file_lock

RUNTIME_COMMANDS = {
    "serve",
    "doctor",
    "runtime-init",
    "credentials",
    "runtime-demo",
    "docs-check",
    "permissions",
}


class LocalServer(uvicorn.Server):
    def __init__(self, config, runtime, *, ready_callback=None):
        super().__init__(config)
        self.runtime = runtime
        self.ready_callback = ready_callback

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started and self.ready_callback is not None:
            self.ready_callback()

    def handle_exit(self, sig, frame):
        # Stop job/request admission at the signal, before Uvicorn drains HTTP.
        startup = self.runtime._startup_task
        self.runtime.begin_shutdown()
        if startup is not None:
            startup.cancel()
        super().handle_exit(sig, frame)

    @contextmanager
    def capture_signals(self):
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        saved = {
            sig: signal.signal(sig, self.handle_exit) for sig in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            yield
        finally:
            for sig, handler in saved.items():
                signal.signal(sig, handler)
        # Handled signals are not replayed; report clean exit or explicit timeout code 2.


def parser():
    p = argparse.ArgumentParser(description="JARVIS v0.9 LOCAL DEVELOPMENT runtime")
    p.add_argument("command", choices=sorted(RUNTIME_COMMANDS))
    p.add_argument("operation", nargs="?", choices=["issue", "revoke", "grant", "curl-config"])
    p.add_argument("--config", type=Path, help="explicit JSON settings file")
    p.add_argument("--data-dir", type=Path)
    p.add_argument("--database", type=Path)
    p.add_argument("--host", choices=["127.0.0.1", "::1"])
    p.add_argument("--port", type=int)
    p.add_argument("--worker-credentials", type=Path)
    p.add_argument("--model-provider", choices=["fake", "local"])
    p.add_argument("--local-endpoint")
    p.add_argument("--local-model")
    p.add_argument("--local-response-format", choices=["json_object", "json_schema"])
    p.add_argument(
        "--no-memory-fixture",
        action="store_true",
        help="new runtime: do not seed Slipstream demo memory",
    )
    p.add_argument(
        "--demo-fixtures",
        action="store_true",
        help="explicit simulated source and approval policy (init only)",
    )
    p.add_argument(
        "--identity",
        choices=["owner", "friend", "guest"],
        default="owner",
        help="TRUSTED OS provisioning selector; never accepted by HTTP",
    )
    p.add_argument("--principal", type=UUID)
    p.add_argument("--permission", help="exact permission name (host administration)")
    p.add_argument("--credential-file", type=Path, help="explicit owner client credential file")
    p.add_argument("--kind", choices=["client", "worker"], default="client")
    p.add_argument("--credential-hours", type=int, default=24)
    p.add_argument("--output", type=Path, help="new private credential file")
    p.add_argument("--credential-id", type=UUID)
    p.add_argument("--verbose", action="store_true", help="structured runtime-demo diagnostics")
    return p


def settings_from_args(args):
    return RuntimeSettings.load(
        args.config,
        data_dir=args.data_dir,
        database_path=args.database,
        runtime_host=args.host,
        runtime_port=args.port,
        worker_credentials_file=args.worker_credentials,
        model_provider=args.model_provider,
        local_model_endpoint=args.local_endpoint,
        local_model_name=args.local_model,
        local_model_response_format=args.local_response_format,
    )


def initialize_development(settings, *, demo_fixtures=False, hours=24, seed_memory_fixture=True):
    path = settings.resolved_runtime_database_path.expanduser().resolve()
    directory = settings.data_dir.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = directory / "credentials"
    config_path = directory / "runtime.json"
    if config_path.exists() or output.exists():
        raise ValueError("runtime_output_already_exists")
    with _file_lock(path.with_name(path.name + ".init.lock")):
        if path.exists():
            raise ValueError("runtime_bootstrap_requires_new_database")
        app = build_application(
            path,
            bootstrap=True,
            provider=settings.model_provider,
            endpoint=settings.local_model_endpoint,
            model=settings.local_model_name,
            timeout=settings.model_timeout_seconds,
            response_format=settings.local_model_response_format,
            seed_memory_fixture=seed_memory_fixture,
            notification_channels=None if settings.simulated_notification_channels else (),
        )
        if demo_fixtures:
            owner = app.credentials("owner")
            try:
                from jarvis.runtime_fixtures import configure_demo_fixtures

                configure_demo_fixtures(app, owner.token)
            finally:
                app.identities.close_session_for_testing(owner.token)
        files = {}
        for name, principal in (
            ("owner", app.identities.owner_id),
            ("friend", FRIEND_ID),
            ("guest", GUEST_ID),
        ):
            credential = issue_credential(app.database, principal, hours=hours)
            file = output / (name + ".json")
            write_private_json(file, credential)
            files[name] = {"path": str(file), "credential_id": credential["credential_id"]}
        worker = issue_credential(app.database, app.identities.owner_id, kind="worker", hours=hours)
        worker_path = output / "workers.json"
        write_private_json(worker_path, {"tokens": [worker["token"]]})
        configured = settings.model_copy(
            update={
                "data_dir": directory,
                "database_path": path,
                "worker_credentials_file": worker_path,
            }
        )
        write_private_json(config_path, configured.model_dump(mode="json"))
    return {
        "status": "initialized",
        "mode": "development",
        "config": str(config_path),
        "database": str(path),
        "schema_version": SCHEMA_VERSION,
        "credentials": files,
        "worker_credential_id": worker["credential_id"],
        "demo_fixtures": demo_fixtures,
    }


async def doctor(settings):
    result = {
        "schema_version": 1,
        "mode": "development",
        "database": False,
        "authority": False,
        "model_available": False,
        "workers": False,
        "errors": [],
    }
    database = SQLiteDatabase(settings.resolved_runtime_database_path)
    try:
        repo = LocalDevelopmentIdentityRepository(database)
        repo.validate_authority()
        result["database"], result["authority"] = True, True
        if settings.worker_credentials_file:
            resolve_worker_tokens(
                repo, settings.worker_credentials_file, settings.max_worker_principals
            )
            result["workers"] = True
    except (StorageError, IdentityError, ValueError, OSError, TypeError):
        result["errors"].append("storage_authority_or_worker_configuration_unavailable")
    if settings.model_provider == "fake":
        result["model_available"] = True
    else:
        result["model_available"] = await OpenAILocalModel(
            endpoint=settings.local_model_endpoint,
            model=settings.local_model_name,
            timeout=settings.model_timeout_seconds,
        ).available()
        if not result["model_available"]:
            result["errors"].append("local_model_unavailable")
    result["status"] = (
        "ready"
        if result["authority"] and result["model_available"] and result["workers"]
        else "degraded"
    )
    return result


def serve_runtime(settings, *, sockets=None, ready_callback=None):
    """Shared foreground host; owned test/demo listeners use the same lifecycle."""
    configure_logging(settings.log_level)
    runtime = RuntimeService(settings)
    server_config = uvicorn.Config(
        create_api(runtime),
        host=settings.runtime_host,
        port=settings.runtime_port,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=int(settings.shutdown_timeout_seconds),
        limit_concurrency=settings.max_requests + 8,
        backlog=32,
        h11_max_incomplete_event_size=16384,
    )
    server = LocalServer(server_config, runtime, ready_callback=ready_callback)
    server.run(sockets=sockets)
    if runtime.drain_timed_out or server.force_exit:
        # A thread/provider cannot be killed safely. Actual exit releases OS claims;
        # persisted RUNNING attempts recover under the existing unknown-effect rules.
        os._exit(2)
    if runtime.state == "FAILED":
        raise SystemExit(1)


def run_runtime_cli(argv):
    args = parser().parse_args(argv)
    try:
        if args.command == "docs-check":
            from jarvis.documentation import check_documentation

            print(json.dumps(check_documentation(Path.cwd())))
            return
        if args.command == "runtime-demo":
            from jarvis.runtime_demo import run_runtime_demo

            print(json.dumps(run_runtime_demo(verbose=args.verbose)))
            return
        if args.demo_fixtures and args.command != "runtime-init":
            raise ValueError("demo_fixtures_require_explicit_initialization")
        if args.no_memory_fixture and args.command != "runtime-init":
            raise ValueError("memory_fixture_option_requires_initialization")
        if args.operation is not None and args.command not in {"credentials", "permissions"}:
            raise ValueError("operator_command_invalid")
        settings = settings_from_args(args)
        if args.command == "runtime-init":
            print(
                json.dumps(
                    initialize_development(
                        settings,
                        demo_fixtures=args.demo_fixtures,
                        hours=args.credential_hours,
                        seed_memory_fixture=not args.no_memory_fixture,
                    )
                )
            )
        elif args.command == "doctor":
            result = asyncio.run(doctor(settings))
            print(json.dumps(result))
            if not result["authority"]:
                raise SystemExit(1)
        elif args.command == "permissions":
            if (
                args.operation not in {"grant", "revoke"}
                or args.principal is None
                or args.permission is None
                or args.credential_file is None
            ):
                raise ValueError("permission_operation_arguments_required")
            from uuid import uuid4

            from jarvis.local_credentials import read_private_json

            token = read_private_json(args.credential_file)["token"]
            app = build_application(
                settings.resolved_runtime_database_path,
                identities_factory=LocalDevelopmentIdentityRepository,
                initialize=False,
                provider=settings.model_provider,
                endpoint=settings.local_model_endpoint,
                model=settings.local_model_name,
            )
            app.identities.resolve_kind(token, "client")
            operation = (
                app.permissions.grant if args.operation == "grant" else app.permissions.revoke
            )
            operation(token, args.principal, args.permission, correlation_id=uuid4())
            print(
                json.dumps(
                    {
                        "status": "updated",
                        "operation": args.operation,
                        "principal_id": str(args.principal),
                        "permission": args.permission,
                    }
                )
            )
        elif args.command == "credentials":
            database = SQLiteDatabase(settings.resolved_runtime_database_path)
            if args.operation == "curl-config":
                if args.credential_file is None or args.output is None:
                    raise ValueError("credential_file_and_new_output_required")
                from jarvis.local_credentials import export_curl_configuration

                export_curl_configuration(args.credential_file, args.output)
                print(json.dumps({"status": "exported", "path": str(args.output)}))
            elif args.operation == "revoke":
                if args.credential_id is None:
                    raise ValueError("credential_id_required")
                revoke_credential(database, args.credential_id)
                print(json.dumps({"status": "revoked", "credential_id": str(args.credential_id)}))
            elif args.operation == "issue":
                if args.output is None or args.output.exists():
                    raise ValueError("new_credential_output_required")
                repo = LocalDevelopmentIdentityRepository(database)
                principal = (
                    args.principal
                    or {"owner": repo.owner_id, "friend": FRIEND_ID, "guest": GUEST_ID}[
                        args.identity
                    ]
                )
                result = issue_credential(
                    database, principal, kind=args.kind, hours=args.credential_hours
                )
                write_private_json(
                    args.output, {"tokens": [result["token"]]} if args.kind == "worker" else result
                )
                print(
                    json.dumps(
                        {
                            "status": "issued",
                            "path": str(args.output),
                            "credential_id": result["credential_id"],
                            "expires_at": result["expires_at"],
                        }
                    )
                )
            else:
                raise ValueError("credential_operation_required")
        else:
            serve_runtime(settings)
    except (StorageError, IdentityError, ValidationError, ValueError, OSError) as exc:
        code = (
            exc.code
            if isinstance(exc, StorageError)
            else exc.reason_code
            if isinstance(exc, IdentityError)
            else "runtime_configuration_or_operation_invalid"
        )
        fields = []
        if isinstance(exc, ValidationError):
            fields = [
                {"field": ".".join(map(str, error["loc"])), "type": error["type"]}
                for error in exc.errors(include_input=False, include_context=False)
            ]
        elif (
            isinstance(exc, ValueError)
            and exc.args
            and exc.args[0]
            in {
                "configuration_too_large",
                "configuration_object_required",
                "configuration_unknown_fields",
                "runtime_output_already_exists",
                "runtime_bootstrap_requires_new_database",
                "new_credential_output_required",
                "credential_file_unsafe",
                "credential_options_invalid",
                "permission_operation_arguments_required",
                "credential_operation_required",
                "documentation_repository_required",
            }
        ):
            code = exc.args[0]
        print(
            json.dumps(
                {"status": "failed", "error": {"code": code, "message": code, "fields": fields}}
            )
        )
        raise SystemExit(1) from None
