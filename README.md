# JARVIS kernel foundation

A local Python development foundation for the permission-governed JARVIS kernel.
This implements Phase 0/1 contracts, the Phase 2 identity/authorization/execution
boundary, Phase 3 durable authority/events/tasks/audits, and Phase 4 persistent
permission-scoped memory. It is not yet the continuously
running assistant described in the product specification.

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
| `world_state.py` | Observation adapter protocol; no world model engine |
| `cognition.py`, `initiative.py` | Proposal-only adapter protocols |
| `observability.py` | JSON logging, audit interface, and in-memory audit sink (SQLite adapter in storage/) |
| `config.py`, `__main__.py` | Local configuration, smoke/demo/recovery/memory and database operation commands |
| `tests/` | Contracts, policy/execution, durability, recovery, concurrency, corruption and CLI tests |
| `.github/workflows/ci.yml` | Frozen install, lint, formatting, tests, build, smoke, demo, recovery-demo, memory-demo |

Read [docs/FOUNDATION.md](docs/FOUNDATION.md) for the architectural review,
requirement coverage, temporary choices, and remaining decisions.

## Changes and architectural decisions

Follow [AGENTS.md](AGENTS.md). Link implementation work to requirement IDs and
accepted ADRs. Use the issue template for requirement-linked work and
[docs/ADR_TEMPLATE.md](docs/ADR_TEMPLATE.md) for decisions. Keep proposed and
provisional decisions distinct from accepted decisions. When a final decision is
accepted, update `DECISIONS.md`, affected requirements/architecture, migration
notes, and acceptance tests together.

The next milestone is Phase 5 fake-first cognitive coordination and a bounded
context builder using authorized memory and validated action proposals. Production authentication,
production owner provisioning, tamper-resistant auditing, real integrations and the continuous
assistant remain unfinished. SQLite is a provisional reversible local choice, not
a final storage or distributed scheduling decision.

The Phase 3 verification pass adds durable current grants/revocations/principals/
sessions, atomic permission/audit changes and cross-process security exclusion.
The default durable identity repository denies without a trusted authenticator;
fixture credentials remain explicitly isolated from production. Run `uv run jarvis db-init` explicitly to migrate existing schema 1/2 development databases to schema
3; this never provisions identity or restores old task approvals. See
[docs/PHASE3_VERIFICATION.md](docs/PHASE3_VERIFICATION.md) for findings, real-process
race/crash tests, supported local guarantees and Phase 4 prerequisites.
