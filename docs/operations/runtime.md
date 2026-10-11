# Runtime operations and diagnostics

`jarvis serve --config <explicit-file>` runs one foreground process until stopped.
The [composition root](../development/architecture.md) owns configuration,
authoritative SQLite adapters, identities/policy, audit/journal, tasks/executor,
memory/world, cognition/models, initiatives and notifications. Core services know
nothing about FastAPI. The API and background jobs share these instances; existing
development CLI wrappers use the same root with explicitly trusted fixture identity.

Startup validates settings/logging, requires an existing compatible database,
claims one runtime per local database, validates security state, registers domain
guards and authenticates explicit worker credentials. It performs one bounded
initial task/initiative/notification recovery pass, probes the configured model,
starts serial scheduling, then exposes readiness. It does not create an owner,
migrate, restore grants or enable policies. Default startup timeout is 30 seconds.

States: STARTING, READY, DEGRADED, STOPPING, STOPPED, FAILED. Missing/future schema,
missing/corrupt authority, unsafe/invalid worker files, incompatible security mode,
an existing runtime claim or initial authoritative failure prevents startup.
Unavailable local model, absent worker configuration, optional adapter/job failure
cause explicit DEGRADED operation. Model availability is an endpoint/model-ID
probe, not proof of successful reasoning. Ordinary authorization denial never
creates fallback owner privileges.

The scheduler has exactly one worker. Jobs process enabled initiative events,
recover initiatives, bridge source history to notifications, process/recover
deliveries, recover eligible ordinary tasks, and run supported bounded maintenance.
Domain-guarded tasks remain owned by initiative/notification processing so generic
recovery cannot bypass quiet hours or domain admission. Each pass has a bounded
batch/current principal, at most the configured bounded worker principals, and no
same-job overlap. One failing job/principal does not grant privileges or disable
unrelated jobs. Authoritative storage/security failures stop unsafe admission.

Current policies, queues, rates, cooldowns and approval gates govern all work.
Intervals are minimum spacing after completion; slow providers make jobs late.
Ordinary task selection rotates a scoped cursor for fairness. World freshness and
memory validity are still primarily lazy/current-query checks; maintenance samples
up to 20 authorized world states and bounded inbox pages. There is no new physical
purge/retention policy or guarantee that every stored historical record is swept.

SIGINT/SIGTERM stops request/job admission immediately, drains current work and
closes owned executors/connections/listener. Completed operations remain committed;
pending work remains durable. Shutdown uses a 15-second default shared deadline.
A synchronous Python/provider thread cannot be safely interrupted. If it cannot
drain, runtime reports FAILED and the foreground host exits with code 2; actual
process death releases claims and recovery classifies outstanding effects. A second
interrupt forces that same conservative exit. Normal handled shutdown exits 0.
Never interpret timeout/process death as proof that an external effect did not occur.

Readiness and diagnostics:

```sh
curl --fail-with-body http://127.0.0.1:8765/health/live
curl --fail-with-body http://127.0.0.1:8765/health/ready
uv run jarvis doctor --config .jarvis/dev/runtime.json
curl --fail-with-body --config .jarvis/dev/credentials/owner.curl http://127.0.0.1:8765/api/v1/status
```

Liveness means the process responds. Core readiness includes valid authoritative
storage and a running scheduler; DEGRADED may be ready while optional cognition or
background credentials are unavailable. Failed authority returns readiness 503.
Unauthenticated health contains only generic state/availability/last pass metadata.
Detailed status is pinned-owner only; backlog samples are that principal's scoped
records, not global private counts. Samples can be truncated and are not total queue
sizes. Status includes job durations/errors/pass counts and process-local simulated
invocations, not confidential prompts, tokens, source payloads or network topology.

`doctor` reads configuration/storage/authority/worker verification and probes a
configured local model; it never migrates, issues sessions, changes grants,
consumes events or executes capabilities. Missing authority exits nonzero;
optional-model degradation is explicit in JSON and may exit 0. Read the result.

Allowlisted metrics report startup, HTTP request, model invocation/provider tokens
when supplied, memory/world retrieval/context assembly and notification/scheduler
pass latency. Missing provider measurements stay unavailable, not fabricated.
Samples are bounded, process-local and reset on restart. Debug logs emit stable
worker codes; access logs are disabled so URLs/queries cannot expose private data.
Logs and audit references are useful diagnostics, not tamper-resistant storage.
