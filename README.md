# JARVIS kernel foundation

A local Python development foundation for the permission-governed JARVIS kernel.
This implements Phase 0/1 contracts and the Phase 2 in-memory identity,
authorization, and capability execution pipeline. It is not yet the continuously
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

`uv.lock` pins the dependency graph; `uv sync --locked` refuses an outdated lock.
Use `uv lock` deliberately when changing dependencies, and commit both the manifest
and lockfile. Formatting changes use `uv run --no-sync ruff format .`.
Build a distributable with `uv build`; CI also checks this packaging path.

## Configuration and logs

Defaults keep cloud AI disabled and use `.jarvis` as a future local data path.
No storage is created by the foundation. Optional process variables are listed in
[.env.example](.env.example). The CLI does not load dotenv files automatically:
export `JARVIS_DATA_DIR` or `JARVIS_LOG_LEVEL` in your shell if needed. Never commit
credentials or private data; `.env`, `.jarvis`, and virtual environments are ignored.

JSON logs include event codes, severity, timestamps, and optional correlation,
proposal, task, and reason identifiers. Never put private payloads in log messages
or identifier fields. Arbitrary extras and exception messages are omitted.

## Structure

| Path | Responsibility / current implementation |
|---|---|
| `src/jarvis/core/` | Strict v1 domain contracts, decoding, timestamps |
| `identity.py` | Server-owned development principals, sessions, and token lookup |
| `permissions.py` | Owner-only explicit grant/revoke store and audit references |
| `authorization.py` | Deterministic default-deny policy and private/shared scope checks |
| `capabilities.py` | Trusted metadata/provider registration and discovery |
| `execution.py` | Input validation, fresh authorization, invocation, output validation, auditing |
| `development.py` | Fake echo, simulated lighting, and end-to-end offline demo |
| `events.py`, `tasks.py` | Store protocols; no persistence or scheduler |
| `memory.py` | Authorized read/write protocol; no retrieval backend |
| `world_state.py` | Observation adapter protocol; no world model engine |
| `cognition.py`, `initiative.py` | Proposal-only adapter protocols |
| `observability.py` | JSON logging, audit interface, and in-memory audit sink |
| `config.py`, `__main__.py` | Local configuration, smoke, and Phase 2 demo commands |
| `tests/` | Contracts, security/policy/execution, logging, configuration, and CLI tests |
| `.github/workflows/ci.yml` | Frozen install, lint, formatting, tests, build, smoke, demo |

Read [docs/FOUNDATION.md](docs/FOUNDATION.md) for the architectural review,
requirement coverage, temporary choices, and remaining decisions.

## Changes and architectural decisions

Follow [AGENTS.md](AGENTS.md). Link implementation work to requirement IDs and
accepted ADRs. Use the issue template for requirement-linked work and
[docs/ADR_TEMPLATE.md](docs/ADR_TEMPLATE.md) for decisions. Keep proposed and
provisional decisions distinct from accepted decisions. When a final decision is
accepted, update `DECISIONS.md`, affected requirements/architecture, migration
notes, and acceptance tests together.

The next milestone is Phase 3: choose a provisional local persistence backend,
make important events and task state durable, define execution ambiguity and
idempotency, and test crash/restart recovery. Production authentication, durable
permissions/audits, memory retrieval, and real integrations remain unfinished.
