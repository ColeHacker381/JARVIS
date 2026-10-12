# JARVIS v0.9 documentation

JARVIS v0.9 is an integrated **local development runtime**, not a production or
publicly deployable service. Credentials, bootstrap and simulated adapters are
development-only. Read [security](operations/security.md) before running it.

Start with [installation](getting-started/installation.md), then follow the
[quickstart](getting-started/quickstart.md). Configure [settings](getting-started/configuration.md)
and an optional [local model](getting-started/local-models.md).

Use the [subsystem manual](guides/subsystems.md) for conversations, memory, world
state, initiatives, notifications, tasks and permissions. The [operator manual](operations/runtime.md)
explains lifecycle and diagnostics; [deployment](operations/deployment.md),
[backup/restore](operations/backup-restore.md) and
[troubleshooting](operations/troubleshooting.md) cover operating the service.

Developers should read [architecture and diagrams](development/architecture.md),
[repository structure](development/repository-structure.md),
[extension guides](development/extensions.md), and [testing](development/testing.md).

References: [configuration](reference/configuration-reference.md),
[CLI](reference/cli-reference.md), [API](reference/api-reference.md),
[generated OpenAPI](reference/openapi.json), and [glossary](reference/glossary.md).
The [v0.9 release report](releases/v0.9.md) records measured acceptance and limits;
the [integration plan](PHASE9_PLAN.md) records the initial design.

Architectural authority remains [requirements](../REQUIREMENTS.md),
[decisions](../DECISIONS.md), [permissions](../PERMISSIONS.md), and
[memory rules](../MEMORY_SYSTEM.md). OPEN and PROPOSED decisions are not ratified
by this provisional runtime.

Earlier milestones remain accessible: [foundation](FOUNDATION.md),
[Phase 2](PHASE2.md), [Phase 3](PHASE3.md),
[Phase 3 verification](PHASE3_VERIFICATION.md), [Phase 4](PHASE4.md),
[Phase 5](PHASE5.md), [Phase 6](PHASE6.md), [Phase 7](PHASE7.md),
and [Phase 8](PHASE8.md). Their validation counts describe those milestones.

## Voice and ambient audio

[Phase 10 voice guide](PHASE10.md): explicit local push-to-talk, offline STT/TTS,
consent/attention/context rules, simulated ambient demos and runtime test isolation.

[Real speech and MacBook acceptance](VOICE_ACCEPTANCE.md): checksum-verified installs,
measured real STT/TTS, existing Gemma configuration, safe schema migration and
explicit hardware/operator checks. Physical microphone/speaker acceptance remains pending.

[Phase 10 cognition hardening](PHASE10_COGNITION_HARDENING.md): independent text-API
Qwen acceptance, structured-output diagnostics, authorized grounded reasoning,
fixture migration and playback cancellation.
