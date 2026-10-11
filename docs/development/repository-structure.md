# Repository structure

| Path | Responsibility |
| --- | --- |
| `src/jarvis/application.py` | Shared composition root and explicit development fixture bootstrap |
| `runtime_config.py`, `runtime_service.py`, `runtime_cli.py` | Validated settings, lifecycle/scheduling and trusted host operations |
| `local_api.py`, `api_contracts.py`, `local_credentials.py` | Loopback transport, typed HTTP contracts, persisted credential resolution |
| `identity.py`, `authorization.py`, `execution.py`, `capabilities.py` | Current identity/policy and sole capability execution path |
| `task_manager.py`, `tasks.py`, `events.py` | Durable task orchestration/ports and journal |
| `memory.py`, `conversations.py`, `cognition.py`, `context.py` | Scoped memory/history, bounded cognitive coordination/context |
| `models.py`, `egress.py` | Replaceable model adapters/router and explicit cloud boundary |
| `world_state.py`, `sensors.py` | Trusted observation ingestion, evidence/beliefs and simulated sensors |
| `initiative.py`, `notifications.py`, `notification_channels.py` | Scoped finite initiative, inbox/delivery and simulated transports |
| `core/` | Explicit versioned domain contracts and decoders |
| `storage/` | SQLite implementations, migrations, transaction/claim/backup primitives |
| `observability.py` | Sanitized structured audit/logs and numeric bounded telemetry |
| `runtime_demo.py`, `runtime_fixtures.py`, other `*_demo.py` | Explicit offline real-service demonstrations |
| `documentation.py` | Generated interface references and read-only link/drift verification |
| `tests/`, `tests/helpers/` | Unit, enforcement, concurrency and actual-process fault tests |
| `docs/`, root specification files | User/operator/developer guides and architectural authority |
| `deploy/` | Optional provisional Linux service template |
| `.github/workflows/ci.yml`, `pyproject.toml`, `uv.lock` | Frozen tooling, CI and packaging |

Names in the module rows are relative to `src/jarvis`. The composition root binds
protocols to adapters; individual services do not import HTTP framework classes.
Runtime data under `.jarvis`, credentials, WAL/SHM/claim files, caches and build
outputs are ignored. Never commit them. Historical Phase documents remain useful
for contract/adapter details; the v0.9 guides describe current integration.

## Audio extension

`core/audio_records.py` and `audio_ports.py` define provisional v1 contracts/ports.
`audio_frontend.py`, `ambient.py`, `speech.py` and `voice.py` implement bounded local
processing around shared services. `storage/audio.py` persists only source/session
metadata. `voice_cli.py` is the explicit authenticated API client; `voice_demo.py`
executes offline scenarios. [Phase 10](../PHASE10.md) maps requirements and limits.
