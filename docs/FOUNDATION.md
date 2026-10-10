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

No code relies on a cloud provider or sends private context to network services.
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
| WORLD-001/002/003 | Source/time, evidence-vs-inference labels, derived provenance, confidence validation | World-state beliefs and staleness policy |
| DEV-003, ARCH-003 | Fake capability/model/observation adapter paths used in tests | Production adapters and provider provenance integration |
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

Phase 5 fake-first cognitive coordination/context building using bounded authorized
memory and validated action proposals. Preserve evidence/uncertainty and add explicit
cloud-egress policy before any optional external model receives protected context.

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
