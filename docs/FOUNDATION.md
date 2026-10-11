# JARVIS foundation architectural review and implementation status

Phase 0/1 contracts remain the baseline. Phase 2 now implements the offline
identity, owner grant/revoke, policy, capability execution, and audit boundary.
Phase 3 adds durable local events/tasks/audits, checkpointed sequential execution,
and safe actual-process recovery. See [PHASE2.md](PHASE2.md) and
[PHASE3.md](PHASE3.md) for the trust model, usage and operations. The local
verification/hardening findings are in [PHASE3_VERIFICATION.md](PHASE3_VERIFICATION.md).

## Authoritative baseline

ADR-001 through ADR-012 establish self-hosting, optional cloud AI, local-first
processing, permission-governed autonomy, multiple users, model-independent
authorization, replaceable models, recoverable tasks, scoped/provenanced memory,
uncertainty-aware world state, and observable failures. These constraints agree
across the documents. No blocking contradiction was found for a contracts-only
foundation. The specifications are broader than this implementation; their
acceptance criteria still govern the eventual kernel.

Legacy Porcupine, transcript-history, Ollama, and Spotify experiments impose no
technology requirement. Public person enrichment, biometric identity, wearable
hardware, urgency routing details, and performance SLOs remain proposed/open.

## Dependency order

Versioned contracts and validated configuration precede identity/session storage
and policy. Policy plus capability argument validation must precede execution;
execution must produce correlated result and audit data. Events and task storage
then enable durability, recovery, and retries. Scoped memory and observation
services require identity and policy before data is exposed. Cognitive and
initiative adapters produce proposals through the same policy/execution boundary.
Real models, devices, and homelab decomposition come after these boundaries work.

## Reversible choices used here

These are implementation choices for this milestone, not final architecture:

- Python 3.12+, a `src` package, uv lockfile, pytest, Ruff, and Pydantic v2.
- Provisional v1 wire formats use `contract_type`, integer `schema_version`, UUIDs,
  timezone-aware times, explicit failure envelopes, and bounded confidence values.
- Python construction requires typed inputs. JSON decoding converts JSON UUID/time
  strings through Pydantic. `decode_contract` requires an explicit supported
  version and known type. Unknown fields are rejected; future changes require
  a new supported version/migration rather than accidental coercion.
- Frozen contract fields are not deep immutability: nested JSON dictionaries and
  lists can change. Registry metadata is deep-copied on write and read. Contract
  data, valid UUIDs, role labels, authentication references, and policy decisions
  are not security credentials. Trusted adapters must establish their provenance.
- Private scope defaults to a required owner ID; sharing requires explicit
  recipient IDs. Phase 4 MemoryService now enforces these labels through current
  authority, with explicit household policy/membership and no public unscoped backend API.
- The legacy Task v1 remains readable. TaskRecord v2 explicitly extends its
  envelope with eight states, ordered steps, revision-checked checkpoints, results
  and attempt/replay history. Final contract/scheduler choices remain open.
- Provisional SQLite implements event/task/audit protocols, explicit migrations,
  WAL/FULL commits, append-only journals and atomic task/history/event/audit
  transitions. Short transactions and local POSIX claims protect concurrent work.
  Completed steps are preserved, safe retries use stable provider keys, and
  ambiguous non-idempotent operations require review. See ADR-T03.
- Capability schemas are checked as JSON Schema Draft 2020-12 on registration.
  Discovery exposes metadata only. Phase 2 binds trusted providers separately,
  validates inputs/outputs, rejects remote references/base relocation, and obtains
  current deterministic policy decisions before invocation.
- Synchronous Python protocols define only currently useful adapter boundaries.
  They do not establish a network transport or concurrency architecture.
- Logging has an allowlist of trace identifiers, no payloads, and exception types
  without exception messages. Callers remain responsible for keeping messages and
  codes free of sensitive data. AuditSink has both in-memory and local SQLite
  implementations; neither claims production tamper resistance.

No code requires a cloud provider. Phase 5 optionally supplies authorized private
context to a pinned loopback model; a future cloud adapter requires trusted egress
policy. No production cloud backend or automatic fallback is installed.
Smoke and demo are finite Phase 0/1 and Phase 2 readiness checks; recovery-demo
uses seven separate processes and private file-backed storage. None starts a
long-running service. SQLite identity/session/grant adapters now preserve current
authority across restart; the Phase 2 demo still uses in-memory fixtures. Default
durable identity wiring denies without trusted authentication, and saved task
approvals cannot re-establish privileges. Production authentication remains OPEN.

## Requirement coverage

| Requirement | Implemented and tested scope | Remaining acceptance criteria |
|---|---|---|
| ARCH-004 | Primary v1 contracts plus explicit task v2; missing/invalid outer/nested versions and corrupt records rejected | Future version migrations |
| DEV-001 | Locked local install, lint, format, pytest, packaged smoke command | Running the eventual kernel |
| CAP-001 | Metadata/provider registration/discovery with schemas and declared permissions; real fake-provider policy/executor integration | Real integration adapters |
| MEM-001/002 | Durable MemoryRecord v2, source/evidence/creator/time/confidence, flexible types and real-process scoped retrieval | Source authenticity and verified evidence resolution |
| MEM-006 | Distinct record/effective times, explicit supersession/current/history/date retrieval and preserved evidence | Automated contradiction/invalidation policy |
| IAM-001 | Durable local principals/session references and exact grants, explicit fixture namespace | Production authentication/provisioning |
| IAM-002/003/004/007, PER-001 | Owner grant/revoke, guest allow/deny, forged-identity rejection, fresh policy enforcement and effective revocation | Production authentication/administration; see PHASE3_VERIFICATION.md |
| PER-002/003 | Declared permissions checked before invocation; linked allow/deny/outcome audits | Production policy design and tamper resistance |
| CAP-002/003, REL-001 | Actual fake-provider input/output validation, correlated structured failures and JSON logs | Real deadlines/provider cancellation and production failure policy |
| SEC-001/003/004 | Local private/shared/household memory isolation, current grants, bounded search, atomic content-free mutation audits | Production auth, tamper resistance, future cloud egress |
| MEM-003/004/005, SOC-001 | Enforced scopes/subjects, consent metadata, current revocation, configurable expiry, tombstones and hidden inactive history | Verified consent/collection policies, retention durations and physical purge |
| WORLD-001/002/003 | Durable scoped evidence/projections, direct/inferred/unknown/conflict/stale labels, TTL/disconnection, source strength and real-process replay | Real hardware, final fusion/retention policy; see PHASE6.md |
| DEV-003, ARCH-003 | Fake capability/model/observation adapter paths used in tests | Production adapters and provider provenance integration |
| COG-001, ARCH-002/003/004 | Bounded coordinator, fake/local models, versioned decisions/provenance, private conversations and task submission | Genuine model validation here; final models/production auth |
| COG-002 (PROPOSED) | Provisional direct/grounded reply path without invoking tools | Binding adoption remains proposed |
| SEC-002 | Separate trusted default-deny cloud egress, destination/data-class grants and adapter-call negative tests | Production cloud backend and final classifications |
| EVT-001, TASK-001/002, REL-002 | Durable ordered events, atomic task/history/audit checkpoints, explicit states/cancellation/CAS, safe idempotent retry, real subprocess recovery and ambiguous review | Distributed scheduling, real provider guarantees, manual review resolution and retention |

Tests demonstrate contracts, the Phase 2 security boundary, local durable
events/tasks/audits and actual-process recovery. They do not prove production
authentication, all future context/egress isolation, distributed recovery,
continuous initiative or a complete v0.1 kernel. Phase 3 preserves current-policy
enforcement and tests revocation and owner isolation, including denial without
returning another principal's task payload.

## Decisions still open

No owner decision blocks this authorized local milestone. ADR-T02 records the
development identity/grant fixture boundary; ADR-T03 records owner-authorized
provisional SQLite, local task claims and conservative recovery. Final schemas,
authentication stack and owner provisioning, policy syntax, storage engine, API
transport, event bus, distributed scheduler, model providers, data classification,
retention periods, profiling rules, initiative thresholds and SLOs remain open.

## Next smallest milestone

Phase 9 one explicitly authorized real sensor adapter after enrollment, collection
and retention decisions. Complete genuine local-model validation against
available weights/endpoint; Phase 5 offline/protocol checks cannot establish that.

## Validation performed

The original Phase 0/1 suite had 116 tests. On Python 3.12.14 the Phase 2 suite
now executes **180 tests, all passing**, including the original 116 unchanged.
Ruff lint and formatting checks passed. `uv sync --locked` succeeded and was
repeated without dependency changes. `uv build` produced both a source distribution
and a wheel. The installed CLI smoke passed from the editable checkout and from
the wheel in a separate virtual environment with `/tmp` as the working directory.
The Phase 2 CLI demo runs real policy/execution and checks lighting calls `[25, 50]`.
Both smoke and the Phase 2 demo passed from a separate installed wheel in `/tmp`;
the documented Phase 2 code example also ran successfully.
`git diff --check` passed. CI is configured for Python 3.12 and 3.13; hosted CI and
the Python 3.13 matrix were not executed in this local validation.

Phase 3 started from the passing 180-test baseline. On Python 3.12.14 the extended
suite executes **256 tests, all passing** (76 added), including actual separate
process event append/retrieval, task history inspection, lock contention, and the
seven-process crash/recovery harness. Ruff lint/format and frozen installation passed. `uv build` produced source and
wheel distributions. Smoke, Phase 2 demo and seven-process recovery passed from
the checkout and a separately installed wheel in `/tmp` (confirmed site-packages
import). Wheel database init/inspect/backup passed, including repeat initialization.
Whitespace checks passed. CI runs Python 3.12 and 3.13, including recovery-demo;
local validation uses Python 3.12.14. No production/power-loss acceptance is claimed.

The Phase 3 verification baseline passed 256 tests. Hardening adds 35 cases for
**291 tests, all passing**, including 17 independent-process security/recovery cases
and 18 storage/security cases. Schema migration 2 preserves old journal/task/audit
records without reconstructing authority. Default identity denial, durable grants/
revocations/session references, atomic permission/audit changes, host-wide security
serialization, current-policy task recovery and actual killed-worker claim release
are covered. See PHASE3_VERIFICATION.md for demonstrated limits and ADR-T04 for
provisional choices. Frozen installation, Ruff lint/format, source/wheel build,
smoke, demo, recovery-demo and whitespace checks passed on Python 3.12.14.
The updated wheel was separately installed and imported from site-packages outside
the checkout; all three CLI demos, repeated schema 2 initialization, inspection,
and backup/backup inspection passed. Hosted CI results are reported with the PR.

Phase 4 started from **291 passing tests** and adds 66 cases: 51 domain/storage/
security, 3 migration/legacy/version and 12 real-process memory tests. The demo
uses two independent processes and current persistent grants, proving private/
shared denial, correction/current-history, provenance and tombstones after restart.
See PHASE4.md and provisional ADR-T05 for the complete file/requirement mapping,
local guarantees and production limitations. The full suite passed **357 tests**
on Python 3.12.14; frozen install, Ruff lint/format, source/wheel build, whitespace,
smoke, demo, recovery-demo and memory-demo checks passed. A separately installed
wheel imported from site-packages outside the checkout passed all four demos plus
repeat schema 3 initialization, inspection and backup. Hosted Python 3.12/3.13 CI
results are attached to the PR; no Phase 5 feature or production auth is claimed.

Phase 5 extends the kernel with bounded authenticated cognition, fake/local async
models, authorized context/discovery, structured one-action decisions, durable
private conversations and trusted egress policy. Baseline 357 tests passed before
coding. New security tests check actual handler/adapter calls, current memory/
history isolation, revocation, malformed output and forged authority. Actual process
tests cover crashes before task linking, after task completion and after an ambiguous
provider effect. The installed CLI/protocol tests and two-process cognition-demo
exercise real services; docs/PHASE5.md maps files, requirements, examples and limits.
Provisional ADR-T06 preserves COG-002 PROPOSED and all OPEN production decisions.
Genuine inference remains unverified here because model-weight downloads are blocked;
protocol test servers and deterministic fakes are not evidence of AI reasoning.

Final local Phase 5 validation passed **477 tests** (120 added) on Python 3.12.14,
frozen install, Ruff lint/format, source/wheel build and whitespace checks. Smoke,
demo, recovery-demo, memory-demo and cognition-demo passed from both the checkout
and a separately installed wheel outside it, with verified site-packages import.
Wheel fake chat, repeated schema 4 initialization, inspection and online backup
passed. Hosted Python 3.12/3.13 CI results are reported with the PR.


Phase 6 started from the passing **477-test** merged Phase 5 baseline. It implements
trusted ingestion and ObservationRecord v2, scoped durable structured projections,
separate observed/inferred/unknown/conflict/stale states, injectable-clock TTL,
registered source confidence, three simulated adapter types, scoped world events,
bounded cognitive context with current dependency checks and verified environmental
replies. Migration 5 preserves existing records. Numeric telemetry contains no
private content. Tests and the two-process world-demo exercise actual security,
SQLite/policy, crash/concurrency/replay and cloud-egress non-invocation boundaries.
See docs/PHASE6.md for actual validation, files, rules and limitations. Provisional
ADR-T07 preserves WORLD-004 PROPOSED and all OPEN authentication/hardware/retention/
initiative/storage decisions. Genuine local-model acceptance remains unverified.


Final local Phase 6 validation passed **563 tests** (86 added) on Python 3.12.14,
Ruff lint/format, frozen install, source/wheel build and whitespace checks. All six
readiness demos, persisted-world fake chat, repeated schema 5 initialization,
inspection and online backup passed from the checkout and a separately installed
wheel outside it, with verified site-packages import. Hosted Python 3.12/3.13 CI
results are reported on the PR; genuine inference remains separately unverified.


Phase 7 adds bounded permission-governed initiative (AUTO-001/002/003), default
observe-only, exact durable approval, narrow LOW simulated auto grants, private
inbox and evidence-grounded cognitive explanations. It extends the existing
journal/task/policy/evidence services with migration 6 and immutable execution
guards, preserving current revocation and ambiguous-outcome review. Baseline:
563 passing tests. See PHASE7.md for full validation, files, requirement mapping,
three-process demo and deferred production choices; ADR-T08 is provisional.
AUTO-002 continuous product behavior remains phased with finite host-scheduled
passes. Phase 8 recommendation: simulated durable notifications and recipient/
channel policies before actual external delivery.


Final Phase 7 validation passed **642 tests** (79 added) on Python 3.12.14,
frozen installation, Ruff lint/format, source/wheel build and whitespace checks.
All seven demos, inbox list/show/approve, persisted initiative fake chat and
repeated schema 6 initialization/inspection/online backup passed in the checkout
and a separately installed wheel outside it. Hosted Python 3.12/3.13 results are
reported with the PR. Genuine local model inference remains separately unverified.


Phase 8 builds on the **642 passing test baseline** with locally persistent scoped
notifications, recipient preferences and simulated channels, using existing tasks/
executor guards and current source authority. Read/ack/dismiss and transport states
are separate; initiative approval links confer no authority. Bounded history
receipts/cursors/admission commit atomically; non-idempotent ambiguous sends require
review. Current notification/source dependencies and default-deny egress protect
cognition/history. Migration 7 preserves data/grants/revocations. See PHASE8.md and
provisional ADR-T09 for complete files/requirement coverage, validation and limits.
Production auth, real channels, final priority/retention/reconciliation and permanent
scheduling remain OPEN; genuine model inference is separately unverified.


Final Phase 8 local validation passed **723 tests (81 added)** in 149.30 seconds
on Python 3.12.14, frozen install, Ruff lint/format (102 files), source/wheel build,
whitespace checks, all eight demos and notification CLI/chat/database operations.
The wheel was independently installed outside the checkout and runtime source
matched the checkout. CI includes Python 3.12/3.13 and notification-demo.
