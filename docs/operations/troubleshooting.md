# Troubleshooting

Start with `uv run jarvis doctor --config <explicit-file>`, the non-sensitive
`/health/ready` JSON, authorized `/api/v1/status`, and private structured logs.
Use real paths/UUIDs; do not include token values, private prompts or raw sensor
payloads in an issue. [CLI](../reference/cli-reference.md) and
[configuration](../reference/configuration-reference.md) list supported commands.

| Symptom | Likely cause | Diagnostic | Safe resolution | Avoid |
|---|---|---|---|---|
| `uv sync` fails | Network/package availability, wrong checkout/lock | `uv --version`; `uv sync --locked --python 3.12` | Verify supported tools, trusted package access and lockfile; retry after fixing prerequisite | TLS/checksum bypass or casually rewriting the lock |
| Python incompatible | Old system/global executable | `uv python list`; `uv run python --version` | Install/use 3.12 or 3.13 and the checkout environment | Running a stale global `jarvis` |
| CLI has only `smoke` | Earlier installed package | `uv run jarvis --help` from checkout | Update branch/dependencies, use `uv sync --locked` | Recreating source or overwriting the repo |
| Ollama missing | Optional backend not installed | `ollama --version`; `ollama list` | Follow official installation or explicitly select fake mode | Claiming fake mode is real inference |
| Model unavailable/DEGRADED | Server stopped or model absent | `curl http://127.0.0.1:11434/v1/models`; `doctor` | Start the existing server, pull/select exact model name | Unapproved cloud fallback |
| Wrong local endpoint | Missing `/v1`, wrong port, remote host | Inspect explicit settings; curl `/models` | Use credential-free loopback OpenAI-compatible endpoint | Weakening SSRF restrictions or adding credentials to URLs |
| Invalid structured model output | Backend cannot follow required decision schema | Inspect response's error code/provenance; run protocol tests | Choose compatible model, review existing structured-output instructions; retry with a fresh request UUID | Treating text commands as executable authority |
| Slow inference | Model size/load/hardware contention | Owner `/status` numeric model/context timings | Use a smaller compatible model, tune `model_timeout_seconds` within bounds | Raising limits without bounds or exposing private prompts |
| SQLite lock/contention/busy | Another live runtime/worker/provider or writer | `db-inspect`; process/service status; worker duration/busy code | Wait for legitimate work; stop only the process you own, then recover | Removing live lock/WAL files, forced lease takeover |
| Missing runtime DB/authority | Wrong path or initialization not done | `doctor --database <path>`; explicit `db-inspect` | Choose intended existing store; initialize only a new development directory | Automatic owner fallback/bootstrap in an old store |
| Schema/migration failure | Old/future schema, unavailable storage, corrupt records | `db-verify`; installed release; saved error code | Backup, stop writers, migrate separately with correct release | Deleting/resetting DB/security as default repair |
| API 401 | Missing, expired, revoked, wrong-kind credential | Check private file metadata/credential ID; `doctor` for workers | Explicit host issue/rotate/revoke and use private client curl config | Sending owner/session UUID or using a worker token as a client |
| API 403 browser/Host denial | Origin/fetch-site/rebinding metadata | Stable error code | Use authenticated local non-browser client with loopback URL | Broad CORS, trusting forwarded headers, public tunneling |
| API permission denied | Current grant/scope/source denied | Compare requested operation with declared exact permission and scope | Explicit owner administration/appropriate sharing; retry under actual authority | Natural-language privilege escalation or role headers |
| Task WAITING/NEEDS_REVIEW | Safe retry pending or ambiguous non-idempotent effect | GET owned task summary; linked actual result/audit references | Let eligible recovery run; review ambiguous provider evidence manually | Blind resend, SQL state edits, assuming rollback |
| Initiative not processing | Disabled/paused policy, absent worker authority, stale/conflicting evidence, cooldown | `initiatives policies`; owner status/jobs; scoped world state | Correct current source/policy/grants; explicitly enable only intended policy | Enabling autonomy automatically at startup |
| Notification deferred/stuck | Quiet hours/rates/opt-out/source revocation, failed/unknown transport | GET owned notification/deliveries; host preferences; jobs | Respect due time/preferences; recover eligible known-safe work; review ambiguity | Equating DELIVERED with read/approval or repeating unknown sends |
| Quiet hours unexpected | IANA zone or midnight/DST interpretation | `notifications preferences --json`; delivery due UTC | Correct paired local start/end minutes and valid timezone with revision | Local wall-clock arithmetic that ignores gaps/folds |
| World stale/unknown/conflicting | TTL elapsed, source disabled/disconnected, conflicting reports | Scoped `/world` evidence/status/time | Supply fresh authorized simulated observation or accept uncertainty | Inventing a recognized identity/fact or reusing expired evidence |
| Runtime ready is false | STARTING/STOPPING/FAILED, authority/scheduler failure | Health state; doctor; logs and last pass | Fix exact prerequisite; stop/restart after safe backup if needed | Treating liveness/open port as readiness |
| Interrupted startup/shutdown | Timeout or uninterruptible synchronous provider | Structured lifecycle code; process status; durable task summaries | Allow actual exit, then recover under current credentials/profile; inspect unknown outcomes | Declaring a timed-out effect absent or restarting over live claims |

Runtime configuration errors identify the failed operation through a stable code;
Pydantic validation can be reproduced locally with `RuntimeSettings.load` without
printing secret values. Startup never resets data. If `runtime-init` partially
fails, its new store may remain: inspect it and explicitly provision missing
credentials/configuration, or preserve/archive it and choose a new directory.
Do not rerun fixture bootstrap or overwrite an existing store to hide the failure.

## Voice and process-test diagnostics

`audio_dependency_or_portaudio_missing`: install optional voice extra and local PortAudio.
`microphone_unavailable_or_permission_denied`: check selected device and OS microphone permission.
`local_stt_model_or_binary_missing`: install/pin whisper.cpp and verify local weights.
`local_tts_binary_missing`: use an installed offline macOS voice or Linux espeak-ng.
`audio_processing_disabled`: check explicit source consent/activation/availability and session.
`audio_permission_denied`: use current owner administration to grant the exact permission.
`trusted_confirmation_required`: a speaker/source is unverified; do not substitute owner authority.
`audio_backpressure`: processing or stream limit reached; reduce sources and drain/expire buffers.
No provider error permits cloud fallback. [Phase 10](../PHASE10.md) has manual diagnostics.

The three former real-process runtime failures on occupied port 8765 were caused by
public readiness reaching another server and foreign credentials returning an error
without `data`. Tests/demo now inherit continuously reserved ephemeral listeners and
require an owned startup acknowledgement. HTTP assertions expose actual status/error
codes; cleanup preserves the primary failure and touches only its owned subprocess.
