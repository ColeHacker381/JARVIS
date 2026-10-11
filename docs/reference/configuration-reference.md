# Configuration reference

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
| `data_dir` (string) | Private persistent runtime directory; example `".jarvis"` | `".jarvis"` | `JARVIS_DATA_DIR` | `--data-dir` | Yes | `{}` |
| `database_path` (string/null) | Explicit existing runtime SQLite store; no serve bootstrap; example `".jarvis/dev/runtime/journal.sqlite3"` | `null` | `JARVIS_DATABASE_PATH` | `--database` | Yes | `{}` |
| `log_level` (string) | Sanitized structured log severity; never enable raw access payloads; example `"INFO"` | `"INFO"` | `JARVIS_LOG_LEVEL` | `file/env` | Yes | `{"enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]}` |
| `cloud_ai_enabled` (boolean) | Cloud transmission disabled; true is rejected; example `false` | `false` | `JARVIS_CLOUD_AI_ENABLED` | `file/env` | Yes | `{"const": false}` |
| `model_provider` (string) | Explicit local AI or deterministic fake; no automatic fallback; example `"local"` | `"local"` | `JARVIS_MODEL_PROVIDER` | `--model-provider` | Yes | `{"enum": ["local", "fake"]}` |
| `local_model_endpoint` (string) | Pinned loopback compatible API base, no credentials/proxy/redirect; example `"http://127.0.0.1:11434/v1"` | `"http://127.0.0.1:11434/v1"` | `JARVIS_LOCAL_MODEL_ENDPOINT` | `--local-endpoint` | Yes | `{}` |
| `local_model_name` (string) | Exact advertised installed model ID; availability is not inference; example `"qwen2.5:3b"` | `"qwen2.5:3b"` | `JARVIS_LOCAL_MODEL_NAME` | `--local-model` | Yes | `{}` |
| `model_timeout_seconds` (number) | Bound model call/provider resource lifetime; example `60.0` | `60.0` | `JARVIS_MODEL_TIMEOUT_SECONDS` | `file/env` | Yes | `{"exclusiveMinimum": 0, "maximum": 300}` |
| `mode` (string) | Development only; production mode rejected; example `"development"` | `"development"` | `JARVIS_MODE` | `file/env` | Yes | `{"const": "development"}` |
| `runtime_host` (string) | Loopback listener; public addresses rejected; example `"127.0.0.1"` | `"127.0.0.1"` | `JARVIS_RUNTIME_HOST` | `--host` | Yes | `{"enum": ["127.0.0.1", "::1"]}` |
| `runtime_port` (integer) | Local TCP port; use unprivileged port above 1024; example `8765` | `8765` | `JARVIS_RUNTIME_PORT` | `--port` | Yes | `{"maximum": 65535, "minimum": 1}` |
| `worker_credentials_file` (string/null) | Private 0600 worker token file; never settings tokens; example `".jarvis/dev/credentials/workers.json"` | `null` | `JARVIS_WORKER_CREDENTIALS_FILE` | `--worker-credentials` | Yes | `{}` |
| `initiative_interval_seconds` (number) | Delay after finite initiative pass; no overlapping loop; example `2` | `2` | `JARVIS_INITIATIVE_INTERVAL_SECONDS` | `file/env` | Yes | `{"maximum": 3600, "minimum": 0.05}` |
| `notification_interval_seconds` (number) | Delay after finite inbox bridge/delivery pass; example `2` | `2` | `JARVIS_NOTIFICATION_INTERVAL_SECONDS` | `file/env` | Yes | `{"maximum": 3600, "minimum": 0.05}` |
| `recovery_interval_seconds` (number) | Delay after guarded recovery and advertised-model probe; example `5` | `5` | `JARVIS_RECOVERY_INTERVAL_SECONDS` | `file/env` | Yes | `{"maximum": 3600, "minimum": 0.05}` |
| `maintenance_interval_seconds` (number) | Delay after scoped lazy world/notification expiry pass; example `30` | `30` | `JARVIS_MAINTENANCE_INTERVAL_SECONDS` | `file/env` | Yes | `{"maximum": 3600, "minimum": 0.05}` |
| `worker_batch_size` (integer) | Bound records per owned page/policy batch; limits finite work; example `20` | `20` | `JARVIS_WORKER_BATCH_SIZE` | `file/env` | Yes | `{"maximum": 100, "minimum": 1}` |
| `max_workers` (integer) | Single serial scheduler; parallel workers are currently rejected; example `1` | `1` | `JARVIS_MAX_WORKERS` | `file/env` | Yes | `{"maximum": 1, "minimum": 1}` |
| `max_worker_principals` (integer) | Bound distinct authenticated worker principals; no implicit owner; example `4` | `4` | `JARVIS_MAX_WORKER_PRINCIPALS` | `file/env` | Yes | `{"maximum": 16, "minimum": 1}` |
| `max_requests` (integer) | Bound active HTTP/request threads; excess is rejected without queue; example `8` | `8` | `JARVIS_MAX_REQUESTS` | `file/env` | Yes | `{"maximum": 32, "minimum": 1}` |
| `startup_timeout_seconds` (number) | Bound startup/authority/recovery/probe; pending work retains claim; example `30` | `30` | `JARVIS_STARTUP_TIMEOUT_SECONDS` | `file/env` | Yes | `{"maximum": 300, "minimum": 1}` |
| `shutdown_timeout_seconds` (number) | Shared HTTP/worker drain deadline; ambiguous attempts preserved; example `15` | `15` | `JARVIS_SHUTDOWN_TIMEOUT_SECONDS` | `file/env` | Yes | `{"maximum": 300, "minimum": 1}` |
| `simulated_notification_channels` (boolean) | Enable simulations; false retains local inbox; example `true` | `true` | `JARVIS_SIMULATED_NOTIFICATION_CHANNELS` | `file/env` | Yes | `{}` |
| `request_body_limit_bytes` (integer) | Bound HTTP body; reject excess before domain use; example `131072` | `131072` | `JARVIS_REQUEST_BODY_LIMIT_BYTES` | `file/env` | Yes | `{"maximum": 131072, "minimum": 4096}` |
| `request_body_timeout_seconds` (number) | Bound total body receive time against slow local clients; example `10` | `10` | `JARVIS_REQUEST_BODY_TIMEOUT_SECONDS` | `file/env` | Yes | `{"maximum": 60, "minimum": 0.1}` |

CLI aliases: `--data-dir` → data_dir; `--database` → database_path;
`--host` → runtime_host; `--port` → runtime_port;
`--worker-credentials` → worker_credentials_file;
`--model-provider` → model_provider; `--local-endpoint` → local_model_endpoint;
`--local-model` → local_model_name. Other values use the explicit JSON file or
environment. `--config` selects that file. `--identity` is a trusted host
provisioning/legacy fixture selector, never runtime authentication or configuration.
