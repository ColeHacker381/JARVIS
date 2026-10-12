"""Generate and verify references against the actual local runtime interfaces."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi.openapi.utils import get_openapi

from jarvis.local_api import create_api
from jarvis.runtime_config import RuntimeSettings
from jarvis.runtime_service import RuntimeService

PURPOSES = {
    "data_dir": "Private persistent runtime directory",
    "database_path": "Explicit existing runtime SQLite store; no serve bootstrap",
    "log_level": "Sanitized structured log severity; never enable raw access payloads",
    "cloud_ai_enabled": "Cloud transmission disabled; true is rejected",
    "model_provider": "Explicit local AI or deterministic fake; no automatic fallback",
    "local_model_endpoint": "Pinned loopback compatible API base, no credentials/proxy/redirect",
    "local_model_name": "Exact advertised installed model ID; availability is not inference",
    "local_model_response_format": (
        "JSON syntax or explicit JSON Schema mode; core validation always applies"
    ),
    "model_timeout_seconds": "Bound model call/provider resource lifetime",
    "mode": "Development only; production mode rejected",
    "runtime_host": "Loopback listener; public addresses rejected",
    "runtime_port": "Local TCP port; use unprivileged port above 1024",
    "worker_credentials_file": "Private 0600 worker token file; never settings tokens",
    "initiative_interval_seconds": "Delay after finite initiative pass; no overlapping loop",
    "notification_interval_seconds": "Delay after finite inbox bridge/delivery pass",
    "recovery_interval_seconds": "Delay after guarded recovery and advertised-model probe",
    "maintenance_interval_seconds": "Delay after scoped lazy world/notification expiry pass",
    "worker_batch_size": "Bound records per owned page/policy batch; limits finite work",
    "max_workers": "Single serial scheduler; parallel workers are currently rejected",
    "max_worker_principals": "Bound distinct authenticated worker principals; no implicit owner",
    "max_requests": "Bound active HTTP/request threads; excess is rejected without queue",
    "startup_timeout_seconds": "Bound startup/authority/recovery/probe; pending work retains claim",
    "shutdown_timeout_seconds": "Shared HTTP/worker drain deadline; ambiguous attempts preserved",
    "simulated_notification_channels": "Enable simulations; false retains local inbox",
    "request_body_limit_bytes": "Bound HTTP body; reject excess before domain use",
    "request_body_timeout_seconds": "Bound total body receive time against slow local clients",
}
ALIASES = {
    "data_dir": "--data-dir",
    "database_path": "--database",
    "runtime_host": "--host",
    "runtime_port": "--port",
    "worker_credentials_file": "--worker-credentials",
    "model_provider": "--model-provider",
    "local_model_endpoint": "--local-endpoint",
    "local_model_name": "--local-model",
    "local_model_response_format": "--local-response-format",
}


def openapi_reference():
    runtime = RuntimeService(RuntimeSettings())
    try:
        api = create_api(runtime)
        return get_openapi(title=api.title, version=api.version, routes=api.routes)
    finally:
        runtime._executor.shutdown(wait=True)
        runtime._request_executor.shutdown(wait=True)


def cli_help(command=None):
    args = [sys.executable, "-m", "jarvis"]
    if command:
        args.append(command)
    result = subprocess.run(args + ["--help"], capture_output=True, text=True, check=True)
    return result.stdout.replace("__main__.py", "jarvis").rstrip()


def references():
    schema = RuntimeSettings.model_json_schema()
    rows = []
    if set(PURPOSES) != RuntimeSettings.model_fields.keys():
        raise ValueError("configuration_documentation_coverage_invalid")
    for name, field in schema["properties"].items():
        constraints = {
            k: v
            for k, v in field.items()
            if k in {"enum", "const", "minimum", "maximum", "exclusiveMinimum", "anyOf"}
        }
        constraints.pop("anyOf", None)
        default = json.dumps(field.get("default"), ensure_ascii=True)
        types = (
            field.get("type")
            or "/".join(nested.get("type", "value") for nested in field.get("anyOf", []))
            or ("boolean" if isinstance(field.get("const"), bool) else "string")
        )
        example = {
            "database_path": '".jarvis/dev/runtime/journal.sqlite3"',
            "worker_credentials_file": '".jarvis/dev/credentials/workers.json"',
        }.get(name, default)
        rows.append(
            f"| `{name}` ({types}) | {PURPOSES[name]}; example `{example}` | `{default}` | "
            f"`JARVIS_{name.upper()}` | `{ALIASES.get(name, 'file/env')}` | Yes | "
            f"`{json.dumps(constraints, sort_keys=True)}` |"
        )
    configuration = (
        """# Configuration reference

Generated from `RuntimeSettings.model_json_schema()`. JSON defaults below are
literal; `{}` means no additional numeric/enum constraints. All fields are
optional. Unknown fields fail validation. `mode` supports development only;
`cloud_ai_enabled` accepts false only. Endpoints must be HTTP loopback `/v1`
URLs without credentials, query, fragments or redirects. The configured path
is the compatible API base (normally `/v1`).

Precedence: defaults < explicit JSON file < `JARVIS_*` environment < explicit CLI.
Paths resolve relative to the process working directory. No `.env` file is loaded.
The runtime database defaults to `data_dir/runtime/journal.sqlite3`;
`database_path` overrides that. Legacy chat/inbox defaults are separate stores.
Credential tokens belong in private credential files, never settings/environment.
Intervals are finite seconds; one serial scheduler worker is the current limit.
An absent worker file disables authenticated background processing and degrades
readiness; an invalid configured file fails startup. Startup and shutdown deadlines
include underlying thread work. See [configuration guide](../getting-started/configuration.md).

Every field is captured at startup and requires restart to apply; credential/
session/grant revocation remains effective immediately from the current store.
The purpose column identifies the safety/privacy consequence of each setting.
Numeric JSON examples are numbers; environment equivalents are plain text.

| Field (type) | Purpose/security/example | Default | Environment | CLI | Restart | Constraints |
| --- | --- | --- | --- | --- | --- | --- |
"""
        + "\n".join(rows)
        + """

CLI aliases: `--data-dir` → data_dir; `--database` → database_path;
`--host` → runtime_host; `--port` → runtime_port;
`--worker-credentials` → worker_credentials_file;
`--model-provider` → model_provider; `--local-endpoint` → local_model_endpoint;
`--local-model` → local_model_name. Other values use the explicit JSON file or
environment. `--config` selects that file.

Voice configuration is explicit in the separate `voice`/`audio` CLI: source ID,
location/consent, selected input/output devices, capture duration <=10 seconds,
local STT binary/model/language, TTS voice/rate, and owned conversation ID. Source
activation/continuous/candidate eligibility and ephemeral transcript TTL (1–300s)
are stored as versioned source metadata. No microphone is enabled by runtime
settings. See [Phase 10](../PHASE10.md) and the generated voice CLI reference.
`--identity` is a trusted host
provisioning/legacy fixture selector, never runtime authentication or configuration.
"""
    )
    root, operator = cli_help(), cli_help("serve")
    cli = (
        """# CLI reference

Generated help below is verified against the installed Python entry point.
Prefix commands with `uv run --no-sync jarvis` after `uv sync --locked`.
Root help includes legacy options; runtime commands have the separate operator
parser below. An accepted option still needs an applicable operation.

| Command | Purpose / authority |
| --- | --- |
| smoke | Offline contract/metadata check, no effects |
| demo | Phase 2 fake capability and permission checks |
| recovery-demo | Phase 3 actual process-crash recovery |
| memory-demo | Phase 4 persistent scoped memory |
| cognition-demo | Phase 5 fake cognition/conversation restart |
| world-demo | Phase 6 simulated scoped observations |
| initiative-demo | Phase 7 simulated approval/autonomy |
| notification-demo | Phase 8 durable simulated notifications |
| runtime-demo | Phase 9 two-process integrated HTTP runtime; `--verbose` for details |
| voice | Explicit local push-to-talk client of authenticated conversation API |
| audio | Trusted local source/device/STT/TTS/session diagnostics and consent controls |
| voice-demo | Offline speech pipeline plus real authenticated HTTP and restart |
| ambient-demo | Offline multi-source attention, isolated context and unconfirmed candidates |
| runtime-init | Explicit new development fixture store only; never reset an existing store |
| serve | Foreground loopback runtime, explicit config/current credentials |
| doctor | Read-only storage/authority/model/worker diagnosis |
| credentials issue/revoke/curl-config | Trusted OS credential administration, new private files |
| permissions grant/revoke | Current owner client file; host grants/revokes an exact permission |
| docs-check | Read-only documentation/reference consistency check, repository cwd |
| chat | Development fixture text CLI; explicit fake or configured local model |
| initiatives | Legacy fixture inbox/policy interface; no HTTP role selection |
| notifications | Legacy fixture inbox/preference/policy interface |
| db-init | Explicit schema migration, no identity/grant bootstrap |
| db-inspect | Read-only schema/table inspection |
| db-backup | Online SQLite backup to a new destination |
| db-verify | Read-only SQLite integrity/foreign-key/schema validation |

Runtime examples are in [quickstart](../getting-started/quickstart.md), credentials
and permissions in the [subsystem manual](../guides/subsystems.md), and database
commands in [backup/restore](../operations/backup-restore.md).

`initiatives`: list/show/approve/deny/cancel/execute/process/recover/configure/policies/pause.
`notifications`: list/show/read/unread/acknowledge/dismiss/expire/approve/deny/bridge/
process/recover/preferences/set-preferences/policies/configure. Use `--database`
explicitly; legacy `--identity` requires trusted host fixture code. Configuration
and inbox changes require exact expected revisions where supported.

Exit status 0 means the operation finished; it does not make every domain result
successful. Inspect structured result/state. Runtime startup/config/authority
failure exits 1. Forced or timed-out shutdown exits 2 with persisted attempts
recoverable under normal ambiguity rules. Missing local model is explicit
DEGRADED; cognitive failures have typed error codes. argparse misuse exits 2.
Raw secrets/provider exceptions are excluded from operator error output.

## Root help

```text
"""
        + root
        + "\n```\n\n## Runtime operator help\n\n```text\n"
        + operator
        + "\n```\n\n## Voice client and audio controls\n\n```text\n"
        + cli_help("voice")
        + "\n```\n\n```text\n"
        + cli_help("audio")
        + "\n```\n"
    )
    return {
        "docs/reference/configuration-reference.md": configuration,
        "docs/reference/cli-reference.md": cli,
        "docs/reference/openapi.json": json.dumps(openapi_reference(), indent=2, sort_keys=True)
        + "\n",
    }


def check_documentation(root):
    root = root.resolve()
    if not (root / "AGENTS.md").is_file():
        raise ValueError("documentation_repository_required")
    expected, failures, link_count = references(), [], 0
    for path, content in expected.items():
        file = root / path
        if not file.is_file() or file.read_text() != content:
            failures.append("reference_outdated:" + path)
    files = [*root.glob("*.md"), *(root / "docs").rglob("*.md")]
    for file in files:
        content = re.sub(r"```[^\n]*\n.*?```", "", file.read_text(), flags=re.S)
        for match in re.finditer(r"\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", content):
            target = match.group(1).strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            link_count += 1
            path = (file.parent / unquote(parsed.path)).resolve()
            if not path.exists():
                failures.append(f"link_missing:{file.relative_to(root)}:{target}")
    required = [
        "docs/README.md",
        "docs/getting-started/quickstart.md",
        "docs/development/architecture.md",
        "docs/development/extensions.md",
        "docs/development/repository-structure.md",
        "docs/development/testing.md",
        "docs/reference/api-reference.md",
        "docs/releases/v0.9.md",
        "docs/PHASE10.md",
    ]
    failures.extend("document_missing:" + p for p in required if not (root / p).is_file())
    if failures:
        raise ValueError("documentation_checks_failed:" + ";".join(failures))
    return {
        "status": "ok",
        "markdown_files": len(files),
        "internal_links": link_count,
        "generated_references": len(expected),
        "openapi_paths": len(openapi_reference()["paths"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    root = Path.cwd()
    if args.write:
        for path, content in references().items():
            file = root / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(content)
    else:
        print(json.dumps(check_documentation(root)))


if __name__ == "__main__":
    main()
