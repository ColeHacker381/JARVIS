# JARVIS kernel foundation

A local Python development foundation for the permission-governed JARVIS kernel.
This implements Phase 0 and the initial Phase 1 contracts. It is not yet the
continuously running assistant described in the product specification.

## Setup

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/). From the existing checkout:

```sh
cd /workspace/JARVIS # or your local clone
uv sync --locked
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync jarvis smoke
```

The smoke command builds local principal/session/proposal contracts, registers a
fake capability's metadata, and verifies a JSON round trip. It prints a JSON
result on stdout and a structured log on stderr. It does not execute a capability,
authenticate a user, start a server, or call an AI provider. No credentials,
hardware, database, or external runtime services are needed.

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
| `identity.py` | Principal/session repository protocol |
| `authorization.py` | Trusted policy evaluation protocol |
| `capabilities.py` | Metadata registry protocol and working in-memory discovery |
| `events.py`, `tasks.py` | Store protocols; no persistence or scheduler |
| `memory.py` | Authorized read/write protocol; no retrieval backend |
| `world_state.py` | Observation adapter protocol; no world model engine |
| `cognition.py`, `initiative.py` | Proposal-only adapter protocols |
| `observability.py` | JSON logging and audit sink protocol |
| `config.py`, `__main__.py` | Local configuration and offline smoke command |
| `tests/` | Contract, registry, logging, configuration, and smoke tests |
| `.github/workflows/ci.yml` | Frozen install, lint, formatting, tests, build, smoke |

Read [docs/FOUNDATION.md](docs/FOUNDATION.md) for the architectural review,
requirement coverage, temporary choices, and remaining decisions.

## Changes and architectural decisions

Follow [AGENTS.md](AGENTS.md). Link implementation work to requirement IDs and
accepted ADRs. Use the issue template for requirement-linked work and
[docs/ADR_TEMPLATE.md](docs/ADR_TEMPLATE.md) for decisions. Keep proposed and
provisional decisions distinct from accepted decisions. When a final decision is
accepted, update `DECISIONS.md`, affected requirements/architecture, migration
notes, and acceptance tests together.

The next milestone is a small Phase 2 security slice: trusted session resolution,
owner grant/revoke, deterministic policy, fake capability execution, and audit
records, tested for guest denial, revocation, and cross-user access. Durable
storage and real model integrations remain later work.
