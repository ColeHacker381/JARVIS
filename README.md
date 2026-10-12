# JARVIS v0.9 — integrated local development runtime

A self-hosted permission-governed JARVIS kernel with durable tasks, private memory,
text cognition, evidence-based world state, scoped initiative and simulated
notifications. Phase 10 adds explicit local push-to-talk, offline speech adapters and bounded
ambient simulation. Phase 9 integrates these services into one always-on development
runtime with authenticated loopback HTTP, bounded scheduling and restart recovery.
Production authentication, real integrations and public deployment remain OPEN.

Start with the [documentation index](docs/README.md) and
[quickstart](docs/getting-started/quickstart.md). Read the
[v0.9 release report](docs/releases/v0.9.md) for acceptance results and limits.

```sh
uv sync --locked --python 3.12
# Explicit NEW private development store only; preserves existing work by refusing reuse.
uv run jarvis runtime-init --data-dir .jarvis/dev --model-provider fake
uv run jarvis doctor --config .jarvis/dev/runtime.json
uv run jarvis serve --config .jarvis/dev/runtime.json
```

The CLI writes separate private client/worker credential files and prints paths,
never tokens. HTTP accepts no owner/friend/guest selector. See the quickstart for
authenticated curl without token arguments. `fake` is deterministic simulation;
configure an available [local model](docs/getting-started/local-models.md) for AI.
Cloud is disabled with no silent fallback. `uv run jarvis runtime-demo` executes
the full two-process offline workflow through real core services.

## Setup

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/). From the existing checkout:

```sh
cd /workspace/JARVIS # or your local clone
uv sync --locked
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync jarvis smoke
uv run --no-sync jarvis demo
uv run --no-sync jarvis recovery-demo
uv run --no-sync jarvis memory-demo
uv run --no-sync jarvis cognition-demo
uv run --no-sync jarvis world-demo
uv run --no-sync jarvis initiative-demo
uv run --no-sync jarvis notification-demo
uv run --no-sync jarvis runtime-demo
uv run --no-sync jarvis docs-check
```

The smoke command builds local principal/session/proposal contracts, registers a
fake capability's metadata, and verifies a JSON round trip. It prints a JSON
result on stdout and a structured log on stderr. It does not execute a capability,
authenticate a user, start a server, or call an AI provider. No credentials,
hardware, database, or external runtime services are needed.

The `demo` command exercises the real Phase 2 policy and executor with explicit
test-fixture identities. It shows owner lighting access, allowed guest echo,
denied guest lighting, a friend's granted lighting access, and denial after
revocation. Only authorized invocations mutate simulated lighting. It prints
structured results and sanitized audit records; no real device is accessed.

Session tokens are server-issued bearer credentials, separate from session IDs
and proposals. All `*_for_testing` identity APIs are trusted local fixture code;
they must never be exposed as authentication endpoints. Capabilities require
explicit grants even for the owner. See [docs/PHASE2.md](docs/PHASE2.md) for the
trust boundary, code example, and limitations.

The `recovery-demo` command uses seven separate processes and real file-backed
SQLite storage. It demonstrates completed-step preservation, idempotent replay
after a provider-effect crash, `NEEDS_REVIEW` for ambiguous non-idempotent work,
and revoked permissions blocking resumed work. Every invocation uses the existing
Phase 2 executor. It creates a fresh ignored data directory, preserves the journal
for inspection, and prints JSON states, call/effect counts and sanitized audits.
See [docs/PHASE3.md](docs/PHASE3.md) for contracts, operations and crash semantics.

The `memory-demo` command uses two separate processes and the real scoped memory
service/SQLite backend. It verifies private/shared access, cross-user/guest denial,
provenance, correction to the current Slipstream fact, authorized historical
retrieval and tombstones before and after restart. Each run uses a fresh ignored
fixture directory; it preserves the ordinary database and never prints tokens.
See [docs/PHASE4.md](docs/PHASE4.md) for grants, API, migration and limits.

The `cognition-demo` command runs the real coordinator, authorized memory retrieval,
policy/executor, durable tasks and conversation store with a deterministic fake
model. It answers an owner-private Slipstream question, executes simulated lighting,
denies a hostile guest proposal and private-memory request, then reopens in another
process and replays the completed request without another action.

For interactive development: `uv run jarvis chat --model-provider fake`. Fake mode
is explicitly **not AI reasoning**. Chat defaults to a real local model:

```sh
# With Ollama already running, select an existing local model; no new download:
ollama list
printf 'Exact installed model tag: '; IFS= read -r JARVIS_LOCAL_MODEL_NAME
export JARVIS_LOCAL_MODEL_NAME
export JARVIS_LOCAL_MODEL_ENDPOINT=http://127.0.0.1:11434/v1
uv run jarvis chat
```

Compatible loopback OpenAI chat endpoints also work with `--local-endpoint` and
`--local-model`. Unavailable models return a structured error; no cloud fallback
is enabled. Conversations use `.jarvis/chat/journal.sqlite3` by default. Resume
the printed ID with `--conversation <uuid>`; `--identity owner|friend|guest` is
**development-only trusted host selection**, never production authentication.
See [docs/PHASE5.md](docs/PHASE5.md) for contracts, setup, context/security rules,
examples and validation limits. Phase 5 cloud acceptance used fake and local HTTP
protocol backends because weight downloads were blocked. Follow-on Phase 10
acceptance exercises genuine local Qwen through the authenticated text API;
see [the cognition hardening report](docs/PHASE10_COGNITION_HARDENING.md) for measured
results, setup and limitations. Physical MacBook voice acceptance remains pending.

The `world-demo` command runs real ingestion/policy/SQLite world projections in two
processes. It shows camera evidence versus inferred occupancy, corroboration,
disconnection, conflict, stale evidence, guest isolation, replay and continued
world-grounded conversation. Numeric telemetry contains no payloads. Use its printed
development database path with `jarvis chat --database <path>` to query the simulated
world through the existing fake/local model interface. See [docs/PHASE6.md](docs/PHASE6.md)
for trusted source registration, TTL/conflict rules, scope isolation and migration 5.

`uv.lock` pins the dependency graph; `uv sync --locked` refuses an outdated lock.
Use `uv lock` deliberately when changing dependencies, and commit both the manifest
and lockfile. Formatting changes use `uv run --no-sync ruff format .`.
Build a distributable with `uv build`; CI also checks this packaging path.

## Configuration and logs

Defaults keep cloud AI disabled and use `.jarvis/journal.sqlite3` for persistent
local development state. Initialize explicitly with `uv run jarvis db-init`; inspect
with `uv run jarvis db-inspect`. Smoke and the Phase 2 demo remain in memory. Optional process variables are listed in
[.env.example](.env.example). The CLI does not load dotenv files automatically:
export `JARVIS_DATA_DIR`, `JARVIS_DATABASE_PATH`, or `JARVIS_LOG_LEVEL` if needed. Never commit
credentials or private data; `.env`, `.jarvis`, and virtual environments are ignored.

JSON logs include event codes, severity, timestamps, and optional correlation,
proposal, task, and reason identifiers. Never put private payloads in log messages
or identifier fields. Arbitrary extras and exception messages are omitted.

## Structure

| Path | Responsibility / current implementation |
|---|---|
| `src/jarvis/core/` | Strict v1 contracts, TaskRecord/MemoryRecord v2, query/search contracts and decoding |
| `identity.py` | Server-owned development principals, sessions, and token lookup |
| `permissions.py` | Owner-only explicit grant/revoke store and audit references |
| `authorization.py` | Deterministic default-deny policy and private/shared scope checks |
| `capabilities.py` | Trusted metadata/provider registration and discovery |
| `execution.py` | Input validation, fresh authorization, invocation, output validation, auditing |
| `development.py` | Fake echo, simulated lighting, and end-to-end offline demo |
| `events.py`, `tasks.py` | Durable store/checkpoint/claim protocols |
| `storage/` | SQLite journal/tasks/audits, durable security/scoped memory adapters, migrations and backup |
| `task_manager.py` | Sequential execution, current authority, cancellation and safe recovery |
| `recovery_demo.py` | Actual subprocess crash/restart harness and simulated idempotent provider |
| `memory.py` | Scoped memory domain service, current policy, supersession, retention, deletion |
| `memory_demo.py` | Real two-process SQLite memory/privacy demonstration |
| `world_state.py`, `storage/world.py` | Trusted source ingestion, scoped evidence and deterministic persistent world projections |
| `sensors.py`, `world_demo.py` | Three simulated sensor types and real two-process world/cognition demonstration |
| `cognition.py`, `context.py` | Bounded coordinator and authorized context building |
| `models.py`, `egress.py` | Fake/local model adapters, routing and separate trusted egress policy |
| `conversations.py`, `storage/conversations.py` | Authenticated private history and durable turn/task links |
| `cognitive_runtime.py`, `chat.py`, `cognition_demo.py` | Explicit development CLI composition and two-process cognition demo |
| `initiative.py`, `storage/initiatives.py` | Permission-governed proposals, exact approvals and guarded durable execution |
| `notifications.py`, `notification_channels.py`, `storage/notifications.py` | Scoped durable inbox, preferences, simulated delivery and safe recovery |
| `observability.py` | JSON logging, audit interface, and in-memory audit sink (SQLite adapter in storage/) |
| `config.py`, `__main__.py` | Local configuration, smoke/demo/recovery/memory and database operation commands |
| `tests/` | Contracts, policy/execution, durability, recovery, concurrency, corruption and CLI tests |
| `.github/workflows/ci.yml` | Frozen install, lint, formatting, tests, docs, build and all nine demos |
| `application.py`, `runtime_service.py`, `runtime_cli.py` | Shared composition, finite scheduler/lifecycle and trusted host operations |
| `local_api.py`, `local_credentials.py`, `api_contracts.py` | Loopback HTTP v1, current persisted credentials and typed transport contracts |

Read [docs/FOUNDATION.md](docs/FOUNDATION.md) for the architectural review,
requirement coverage, temporary choices, and remaining decisions.

## Changes and architectural decisions

Follow [AGENTS.md](AGENTS.md). Link implementation work to requirement IDs and
accepted ADRs. Use the issue template for requirement-linked work and
[docs/ADR_TEMPLATE.md](docs/ADR_TEMPLATE.md) for decisions. Keep proposed and
provisional decisions distinct from accepted decisions. When a final decision is
accepted, update `DECISIONS.md`, affected requirements/architecture, migration
notes, and acceptance tests together.

Phase 9 now integrates the local development runtime. Earlier Phase 10 recommendation (deployment work remains deferred):
production authentication/provisioning and genuine local-model acceptance before
privileged real integrations. Real sensors still need explicit enrollment,
collection and retention decisions. Production security, tamper-resistant auditing
and real integrations remain unfinished. SQLite, local auth and scheduling are
provisional reversible choices, not final production decisions.

The Phase 3 verification pass adds durable current grants/revocations/principals/
sessions, atomic permission/audit changes and cross-process security exclusion.
The default durable identity repository denies without a trusted authenticator;
fixture credentials remain explicitly isolated from production. Run `uv run jarvis db-init`
explicitly to migrate existing schema 1–8 development databases to schema 9;
this never provisions identity or restores old task approvals. Back up existing
state before migration. See
[docs/PHASE3_VERIFICATION.md](docs/PHASE3_VERIFICATION.md) for findings, real-process
race/crash tests, supported local guarantees and Phase 4 prerequisites.


## Phase 7 initiative inbox (offline, simulated)

`uv run jarvis initiative-demo` exercises observe-only (default), exact durable
approval, narrow LOW simulated automatic execution, cooldowns/revocation/stale
evidence, and three-process restart/ambiguous-outcome recovery. All invocation
uses the existing guarded TaskManager and CapabilityExecutor.

```bash
uv run jarvis initiatives list --database <demo-database>
uv run jarvis initiatives show <initiative-id> --database <demo-database>
uv run jarvis initiatives approve <initiative-id> --database <demo-database>
uv run jarvis initiatives deny <initiative-id> --database <demo-database>
uv run jarvis initiatives cancel <initiative-id> --database <demo-database>
uv run jarvis chat --database <demo-database> --model-provider fake
```

Development host identities are fixtures; observe-only and cloud default-deny
remain the legacy defaults. Current databases migrate explicitly to schema 9 without
new grants. See [docs/PHASE7.md](docs/PHASE7.md) for policy configuration, risk,
approval/execution guards, bounded processing, all files/requirements, verified
results and deferred production choices. Real devices/messaging are unavailable.


## Phase 8 notification inbox (offline, simulated)

`uv run jarvis notification-demo` runs three real processes through the core
services: authorized task/initiative notifications, separate read/acknowledgement
and approval, quiet-hour deferral, bounded retries, deduplication, guest isolation,
revocation and ambiguous-send recovery. Nothing sends a real external message.

```sh
uv run jarvis notifications list --database <printed-demo-database>
uv run jarvis notifications show <notification-id> --database <database> --json
uv run jarvis notifications read <notification-id> --expected-revision <revision> --database <database>
uv run jarvis notifications acknowledge <notification-id> --expected-revision <revision> --database <database>
uv run jarvis notifications process --database <database>
uv run jarvis notifications preferences --database <database> --json
uv run jarvis chat --model-provider fake --database <database>
```

Try `Do I have new notifications?` or `Why did you notify me about garage?`.
Transport delivery never means a human read, acknowledged or approved. Approval
links use only the existing Phase 7 trusted workflow. New development stores seed
basic rights once, with no routing policy; old stores never receive automatic grants.
Current schema is 7. See [docs/PHASE8.md](docs/PHASE8.md) for routing/configuration,
channels, retries, DST-aware preferences, migration, actual validation and limits.


## Voice and ambient development

See [Phase 10 setup and limitations](docs/PHASE10.md). Audio never starts automatically.
Run `uv run jarvis voice-demo` or `uv run jarvis ambient-demo` entirely offline.
For a real microphone use `uv sync --locked --extra voice`, explicitly enroll and
activate a source, configure whisper.cpp and an installed offline TTS voice, then
run `uv run jarvis voice --help`. [MacBook acceptance and real-model results](docs/VOICE_ACCEPTANCE.md)
cover verified Linux whisper.cpp/eSpeak NG inference and the still-unverified
Gemma/microphone/speaker workflow. Runtime integration tests select owned ephemeral ports,
so an existing development server on port 8765 can remain running.

[Phase 10 cognition hardening](docs/PHASE10_COGNITION_HARDENING.md) documents real
Qwen reproductions, optional local JSON Schema output, grounded brainstorming,
scoped project continuity, fixture migration and normal playback cancellation.
Voice presentation requests concise generation without truncating verified results.
