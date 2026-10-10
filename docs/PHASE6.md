# Phase 6: observations and a persistent evidence-based world model

This local milestone extends the merged Phase 5 kernel. The baseline was **477
passing tests on Python 3.12.14**. Accepted ADR-001–012 remain binding; ADR-T07
records reversible implementation choices. WORLD-004 stays PROPOSED and WORLD-005,
production device authentication, collection/retention policy and initiative
thresholds remain OPEN. Genuine local inference was not verified in Phase 5's
cloud environment; this milestone does not claim that existing gap is resolved.

## Run the real offline workflow

```sh
uv sync --locked
uv run jarvis world-demo
uv run --no-sync pytest tests/test_world.py
```

`world-demo` runs two independent processes using the actual SQLite journal,
identity/grants, deterministic policy, world service, conversation store and
cognitive coordinator. It registers a simulated camera, two occupancy sources
and a device-status source. No hardware, cloud, biometric recognition or autonomous
initiative runs. Model output comes from an explicitly deterministic fake.

The structured JSON contains full evidence/state/provenance/audit references and
numeric telemetry. The stages actually execute and assert these transitions:

```text
camera alone:       occupancy=true, inferred, confidence strength=0.80
independent sensor: occupancy=true, observed, confidence strength=0.80
camera disconnect: occupancy=true, observed, supported by the other sensor
opposing sensor:   occupancy=unknown, conflicting, confidence strength=0.00
clock +61 seconds: occupancy=unknown, stale, confidence strength=0.00
guest query:       no restricted state
new process:       same stale projection revision, retained historical evidence
replayed input:    duplicate=true, no duplicate state-change event
continued chat:    world_grounded reply explicitly reports stale/unknown state
```

The run creates a new ignored `.jarvis/world-demo/run-*/journal.sqlite3` and prints
its path. Existing stores are preserved. Use that path for interactive development:

```sh
uv run jarvis chat --model-provider fake --database <printed-database-path>
```

For example, `Is anyone on the porch?` produces a verified reply containing
`porch occupancy: unknown (stale evidence; current state unknown; ...)`.
`Is garage-camera online?` similarly qualifies the stale simulated report.
The CLI clock is real time; the demo clock/evidence are explicitly synthetic.
An ordinary new chat store contains no sensors and returns unknown/unavailable
for environmental questions. It never claims a sensor connection exists.

To use an installed local model, keep the existing Phase 5 configuration:

```sh
ollama serve                     # separate terminal, Ollama already installed
ollama pull qwen2.5:3b
export JARVIS_LOCAL_MODEL_ENDPOINT=http://127.0.0.1:11434/v1
export JARVIS_LOCAL_MODEL_NAME=qwen2.5:3b
uv run jarvis chat --database <development-database-path>
```

Only pinned loopback OpenAI-compatible endpoints are accepted. Unavailable models
return structured errors; there is no cloud fallback. Development identity
selection is trusted host fixture code, never authentication for public clients.
See [PHASE5.md](PHASE5.md) for model/CLI configuration and genuine-inference limits.

## Contracts and trusted source registration

Legacy `Observation` v1 remains readable. `ObservationRecord` v2 extends it with
source principal/device registration, observation type, logical entity/location,
optional subject label, originating event/correlation IDs, received (`recorded_at`)
and observed times, availability, trusted expiry and unverified raw-evidence refs.
`SensorPayload` v1 accepts only boolean or explicit null values. No raw images,
audio, strings of instructions or automatically recognized person identities are
accepted. The v0 types are person detection, occupancy, motion and availability.
A new payload/type requires an explicit contract/adapter extension.

`ObservationInput` v1 is untrusted data, not authority. It cannot supply owner,
received time, approval, access scope or authenticated principal. An opaque session
token is supplied separately to `WorldModel.ingest`. The existing identity service
resolves the current source principal; a pinned `SourceRegistration` v1 must match
that principal, type, permitted entity and location. Subjects remain separate from
sensor ownership and are never authentication evidence. Raw references are not
fetched or treated as verified provenance beyond the registered source's report.

Source registration requires current `world.sources.manage` and exact ownership
of the registered AccessScope. Recipients/source principals must be known active
principals. Only current owner-administered `world.ingest` permits submission.
Registration supports CAS enable/disable; changing stored ownership, recipients,
reliability, TTL or routes through that update API is rejected. A disabled source
cannot ingest and its current support becomes unavailable on reevaluation.
Production credentials/device enrollment are deliberately unresolved; fake adapters
use the existing explicit development identity service, never device-supplied IDs.

## Evidence, confidence and deterministic projection

Immutable observations are retained separately from `WorldState` v1 projections.
Room/device entities, state/evidence links and revisions are durable structured
records, not text memory. Source/type schema and provenance are validated on read.

Projection rules are intentionally conservative and replaceable:

1. Select the latest observed timestamp per source/entity/attribute. Keep equal-time
   ties so contrary reports from even one source produce explicit conflict.
2. Older out-of-order arrivals remain historical evidence and do not overwrite
   newer support. Expired latest reports never resurrect older source claims.
3. Apply the source's trusted TTL (1–86400 seconds); input expiry may shorten it,
   never extend it. Future observations and naive timestamps are rejected.
4. Exclude disabled/unavailable/disconnected sources and expired evidence from
   current support. Disconnection is an explicit availability report on the device
   entity. It never means a room is empty. A disconnected source stays unavailable
   until an explicit later connection report; missing heartbeats are not negation.
5. Camera person detection implies **possible occupancy**, labeled `inferred`
   with `camera-presence-implies-possible-occupancy-v1`. No person detected is
   unknown occupancy. A direct occupancy report may explicitly be false.
6. Any opposing fresh nonzero-strength support produces `conflicting`, null value,
   confidence zero. No arbitrary source/arrival/UUID tie-break asserts truth.
7. Agreement uses the maximum supporting `observation confidence × registered
   source reliability`; corroboration does not increase it. This is an evidence
   strength, **not a calibrated probability**. Low strength stays low.
8. No usable support yields `unknown`, `unavailable` or `stale`, each with null
   current value and zero confidence. Current beliefs cite governing observations,
   including availability dependencies; history preserves original values.

`last_observed_at` is evidence time, `updated_at` is the projection change time,
`evaluated_at` is query time, and `expires_at` is the first support expiry that can
change the projection. They are distinct from persisted revision/checkpoint state.
Freshness uses an injectable clock. Queries and ingestion reevaluate selected
projections, including after restart; **no background scheduler** is installed.
Expiration events are emitted once for expired latest candidate observations as
projections are evaluated. Superseded historical rows remain available without a
claim that every expired historical row generates a timer event.

## Authorization, privacy and cloud egress

| Operation | Current authority and scope |
|---|---|
| Register/enable/disable a source | `world.sources.manage`, exact scope owner; enable/disable also needs expected revision |
| Ingest | `world.ingest`, authenticated enabled registered source and pinned routes |
| Current query/get/context | `world.read`, exact private owner or explicit shared recipient |
| Historical observations/scoped world events | `world.history` in addition to read; same current scope |

The existing DeterministicPolicy supplies independent permission decisions and
content-free audits. WorldService uses existing AccessScope private/shared labels
and the same explicit-owner/recipient semantics as memory. The system owner has
no bypass into another principal's private data. Subject labels grant no access.
SQL filters scope before decoding, relevance, order and limit; missing/inaccessible
state IDs both return `world_unavailable`. No global counts/ranks are returned.
Current grant revocation takes effect on subsequent queries, including history.

Evidence from different owner/recipient/policy partitions is **never fused**.
A shared projection cannot depend on a private source. A caller authorized for
multiple partitions can see multiple separately labeled records, including
contradictory values; no cross-partition absolute truth is invented. Partition
membership is immutable in this v0 API. Revoke current read/history grants for
future access; per-resource recipient removal and physical purge remain deferred.

`WorldModel.events` filters the existing journal by currently visible evidence
before returning bounded identifiers. Raw SQLiteEventStore/audit methods remain
trusted operator internals and must not become user/initiative endpoints.
Trusted Python infrastructure and database file owners are outside the API's
security boundary; this is not encryption, an untrusted-plugin sandbox or
anti-rollback/tamper-resistant storage.

Cloud AI remains disabled by default. Future cloud invocation containing current
world state **or world-derived conversation history** additionally requires the
separate current `model.egress:<provider>:world` grant, trusted destination and
cloud enablement. Ordinary model selection/read permission is insufficient.
Negative tests verify zero cloud-adapter calls when world egress is prohibited.
No cloud perception adapter or networked sensor broker is introduced.

## Durability, replay and migration

SQLite migration **5** preserves schema 1–4 authority, memory, tasks, events,
audits and conversations. It adds sources/entities, immutable observations,
current projection/evidence links, scoped reader indexes and expiration markers.
The existing WAL/FULL, short immediate transactions and host-wide security claim
are reused. Database/source/state versions and denormalized indexes are validated;
corrupt/unsupported authorized records produce stable `WorldError.code` failures.

Observation acceptance, its existing Event v1 with versioned reference payload,
all resulting projection changes/events and success audit commit **atomically in
one transaction**. No new broker/outbox is needed. Identical observation/event IDs
and input are acknowledged as duplicates; conflicting reuse is rejected. Duplicate
acceptance does not add events or projection revisions. Projection refresh and its
state/expiration events are also atomic. Query policy/outcome audits are separately
durable; an unavailable audit prevents returning protected results. A committed
refresh is not rolled back by a later query-audit failure.

Concurrent source updates use expected revisions. State writes compare previous
revisions and have a SQL CAS trigger; short SQLite transactions plus existing
security claims serialize local processes. Real subprocess tests cover competing
updates and process death after writes/events but before commit, followed by safe
replay. This is one local POSIX host, not distributed locking or a final PostgreSQL
choice. `WorldStore` private methods can be implemented by another backend.

Back up before migration using `jarvis db-backup --destination <new-path>` with the
appropriate `JARVIS_DATABASE_PATH`, then run `jarvis db-init`. Initialization is
repeatable, does not grant world permissions to existing users, restore revoked
authority or import legacy observations by guessing trusted devices. Existing
Phase 5 stores work without world grants; missing grants simply omit world context.
Never remove live WAL/SHM/lock files or reset user state to complete setup.

## Cognitive integration and telemetry

ContextBuilder optionally queries the world service using trusted credentials.
WorldContext contains projection/evidence fields without owner/recipient ACL metadata.
It matches bounded entity/location keywords and structured attribute terms, filters
access first, includes up to three states by default (configurable maximum five),
and applies the existing total serialized context budget. Memory and world state
remain distinct contracts and data sources. Sensor context is untrusted data,
never a system/developer instruction. The local and fake models share this request.

Validated world decisions cite supplied state IDs. For environmental replies with
state, the coordinator deterministically renders actual value, evidence/inference
label, strength, timestamp and evidence references, ignoring fabricated names or
sensor claims. For recognized environmental questions without authorized evidence,
it reports unknown/unavailable instead of accepting fabricated sensor text.
The small keyword intent rule is provisional, not general natural-language intent
understanding or a universal semantic verifier. Action proposals still go only
through the original TaskManager/CapabilityExecutor/current policy.

Turns persist bounded world ID/revision dependencies (at most 105; history admission
is additionally capped by configured history/state limits). These are rechecked before
model-output use, completed reply replay and history reuse. Expired/updated/revoked
state cannot re-enter context through an old assistant response. A completed old
reply whose evidence changed returns `context_authorization_changed`; submit a
new conversational request for current state. Prior authorized conversation
snapshots remain stored locally; revocation does not physically erase transcripts.

The provider-neutral numeric metrics sink allowlists ingestion/projection/query,
context/model durations, token counts, load/prompt/generation durations and token
rate. No content, free-form labels, sensor payloads or session tokens are recorded.
Local OpenAI endpoints supply optional usage and llama.cpp timing fields; absent
metrics are `None`, never estimated from characters or inferred as zero. Elapsed
service/model durations are measured with monotonic time. Metrics are at most 1024 recent development
samples, not a monitoring service or ratified performance SLO (REL-003 stays PROPOSED).

## Files, requirement coverage and validation

New modules: `core/world_records.py`, `storage/world.py`, `sensors.py`,
`world_demo.py`. Existing `world_state.py` now implements the domain service;
`context.py`, `cognition.py`, cognitive contracts/runtime, model/egress adapters,
observability, SQLite migration/decoder and CLI are extended. No duplicate
capability executor, memory architecture or messaging infrastructure is created.
`tests/test_world.py` and `tests/helpers/world_worker.py` cover contracts, scopes,
actual processes, recovery, cognition and numeric telemetry. CI adds world-demo.

Files created or modified in this milestone:

- `.github/workflows/ci.yml`
- `ARCHITECTURE.md`
- `DECISIONS.md`
- `README.md`
- `REQUIREMENTS.md`
- `ROADMAP.md`
- `docs/FOUNDATION.md`
- `docs/PHASE3.md`
- `docs/PHASE6.md`
- `src/jarvis/__main__.py`
- `src/jarvis/cognition.py`
- `src/jarvis/cognitive_runtime.py`
- `src/jarvis/context.py`
- `src/jarvis/core/cognitive_records.py`
- `src/jarvis/core/contracts.py`
- `src/jarvis/core/world_records.py`
- `src/jarvis/egress.py`
- `src/jarvis/models.py`
- `src/jarvis/observability.py`
- `src/jarvis/sensors.py`
- `src/jarvis/storage/sqlite.py`
- `src/jarvis/storage/world.py`
- `src/jarvis/world_demo.py`
- `src/jarvis/world_state.py`
- `tests/helpers/world_worker.py`
- `tests/test_cognitive_migration.py`
- `tests/test_world.py`

| Requirements | Demonstrated coverage |
|---|---|
| WORLD-001/002/003 | Direct vs inferred/unknown/conflicting/stale states, provenance, confidence, TTL/disconnection and deterministic replay |
| WORLD-004 (PROPOSED), DEV-003 | Three fake adapter types through the real trusted ingestion interface |
| EVT-001, ARCH-004, REL-001/002 | Explicit versions, atomic durable observations/events/projections/audits, migration, structured failure and process restart/crash |
| IAM-001, PER-001, MEM-002/003, SEC-001/003 | Existing current policy, exact source identity/ownership, scoped evidence/context/events, private/shared isolation and revocation |
| COG-001, SEC-002 | Bounded authorized world context, verified environmental responses, no sensor-authorized actions and separate default-deny world egress |

Final local validation on Python 3.12.14 passed **563 tests** (86 added; the
477-test Phase 0–5 baseline continues passing), Ruff lint/format, frozen install,
source/wheel packaging and `git diff --check`. Smoke, demo, recovery-demo,
memory-demo, cognition-demo and world-demo passed from the checkout and an
independently installed wheel outside the checkout with a verified site-packages
import. Persisted-world fake chat, repeated schema 5 initialization, inspection
and SQLite online backup also passed in both environments. Hosted CI tests Python
3.12/3.13; final hosted results are reported on the pull request. Real inference
remains unverified here; local protocol/fake tests do not establish model reasoning.

## Limits and recommended Phase 7

Only simulated boolean person/motion/occupancy/availability reports are supported.
No physical sensors, real CV, facial recognition, automatic person identification,
profiling, memory extraction, initiative loops, distributed brokers or cloud
perception are implemented. Confidence/conflict/freshness rules are a replaceable
v0 policy. Collection consent, retention/erasure periods, real device authentication,
source enrollment, hardware selection and genuine-model verification remain open.

Phase 7 should consume **authorized** scoped world events to create bounded
suggestions with durable correlation, explicit enable/pause controls and duplicate
suppression. Any later action must still use existing policy/tasks/executor, with
owner decisions on initiative thresholds and confirmation rules before activation.
