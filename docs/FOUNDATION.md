# JARVIS foundation architectural review and implementation status

Phase 0/1 contracts remain the baseline. Phase 2 now implements the offline
identity, owner grant/revoke, policy, capability execution, and audit boundary.
Phase 3 adds durable local events/tasks/audits, checkpointed sequential execution,
and safe actual-process recovery. See [PHASE2.md](PHASE2.md) and
[PHASE3.md](PHASE3.md) for the trust model, usage, operations and limitations.

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
  recipient IDs. These labels do not enforce access. There is no memory backend
  or unscoped retrieval operation in this milestone.
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
long-running service. Identity/session/grant state remains in memory; recovery
requires trusted fresh authentication/current grants, not saved task approvals.

## Requirement coverage

| Requirement | Implemented and tested scope | Remaining acceptance criteria |
|---|---|---|
| ARCH-004 | Primary v1 contracts plus explicit task v2; missing/invalid outer/nested versions and corrupt records rejected | Future version migrations |
| DEV-001 | Locked local install, lint, format, pytest, packaged smoke command | Running the eventual kernel |
| CAP-001 | Metadata/provider registration/discovery with schemas and declared permissions; real fake-provider policy/executor integration | Real integration adapters |
| MEM-002 | Source category/reference, derived evidence references, optional model reference | Storage and retrieval integration |
| MEM-006 | Record time separate from validity interval; invalid intervals rejected | Temporal retrieval behavior |
| IAM-001 | Distinct server-owned principals, known/unknown sessions and effective explicit grants | Persistent identity and production authentication |
| IAM-002/003/004/007, PER-001 | Owner grant/revoke, guest allow/deny, forged-identity rejection, fresh policy enforcement and effective revocation | Durable authority and production authentication; see PHASE2.md |
| PER-002/003 | Declared permissions checked before invocation; linked allow/deny/outcome audits | Production policy design and tamper resistance |
| CAP-002/003, REL-001 | Actual fake-provider input/output validation, correlated structured failures and JSON logs | Real deadlines/provider cancellation and production failure policy |
| SEC-003/004 | Private/shared capability-result isolation, audited privilege decisions and grant/revoke | Full scoped memory retrieval, audit tamper resistance and future cloud egress |
| MEM-003/004/005, SOC-001 | Explicit private/shared labels, subjects, consent, retention, tombstone fields | Authorized storage/retrieval and revocation |
| WORLD-001/002/003 | Source/time, evidence-vs-inference labels, derived provenance, confidence validation | World-state beliefs and staleness policy |
| DEV-003, ARCH-003 | Fake capability/model/observation adapter paths used in tests | Production adapters and provider provenance integration |
| EVT-001, TASK-001/002, REL-002 | Durable ordered events, atomic task/history/audit checkpoints, explicit states/cancellation/CAS, safe idempotent retry, real subprocess recovery and ambiguous review | Distributed scheduling, real provider guarantees, manual review resolution and retention |

Tests demonstrate contracts, the Phase 2 security boundary, local durable
events/tasks/audits and actual-process recovery. They do not prove production
authentication, persistent grants, full memory isolation, distributed recovery,
continuous initiative or a complete v0.1 kernel. Phase 3 preserves current-policy
enforcement and tests revocation and owner isolation, including denial without
returning another principal's task payload.

## Decisions still open

No owner decision blocks this authorized local milestone. ADR-T02 records the
development identity/grant fixture boundary; ADR-T03 records owner-authorized
provisional SQLite, local task claims and conservative recovery. Final schemas,
authentication stack, persistent authority, policy syntax, storage engine, API
transport, event bus, distributed scheduler, model providers, data classification,
retention periods, profiling rules, initiative thresholds and SLOs remain open.

## Next smallest milestone

Phase 4 scoped local memory storage/retrieval with provenance, consent, retention/
deletion and multi-user isolation tests. Reuse the current policy boundary and
durable journal. Keep models/voice/devices and production identity separate.

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
