# Phase 0/1 architectural review and implementation status

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
- Minimal task/result states represent errors, cancellation, and timeouts, without
  fixing transition policy, retry counts, scheduler semantics, or idempotency
  enforcement. Task state is not yet durable.
- Capability schemas are checked as JSON Schema Draft 2020-12 on registration.
  Discovery uses metadata only, not plugin imports or execution. Future execution
  must validate arguments and outputs with an approved local schema policy,
  prohibit unapproved remote references, and authorize from current trusted state.
- Synchronous Python protocols define only currently useful adapter boundaries.
  They do not establish a network transport or concurrency architecture.
- Logging has an allowlist of trace identifiers, no payloads, and exception types
  without exception messages. Callers remain responsible for keeping messages and
  codes free of sensitive data. AuditSink is a protocol, not durable audit storage.

No code relies on a cloud provider or sends private context outside the process.
The smoke command is a finite offline readiness check, not a long-running service.

## Requirement coverage

| Requirement | Implemented and tested scope | Remaining acceptance criteria |
|---|---|---|
| ARCH-004 | All primary contracts serialize with explicit v1; unknown/missing versions and fields rejected at the decoder | Future version migrations |
| DEV-001 | Locked local install, lint, format, pytest, packaged smoke command | Running the eventual kernel |
| CAP-001 | Metadata registration/discovery with input/output schemas and declared permissions | Integration with policy/executor |
| MEM-002 | Source category/reference, derived evidence references, optional model reference | Storage and retrieval integration |
| MEM-006 | Record time separate from validity interval; invalid intervals rejected | Temporal retrieval behavior |
| IAM-001 | Distinct principals, known/unknown sessions and explicit role/status data | Persistent identity and effective permissions |
| IAM-004, PER-001 | Proposal schema cannot embed authorization; distinct policy protocol/contract | Trusted policy enforcement and execution denial |
| PER-002 | Side-effect metadata requires permission declarations | Permission checking at invocation |
| CAP-003, REL-001 | Correlated success/denied/validation/execution/timeout/cancelled envelopes, error validation, structured logging | Executor and persistent failure/audit handling |
| MEM-003/004/005, SOC-001 | Explicit private/shared labels, subjects, consent, retention, tombstone fields | Authorized storage/retrieval and revocation |
| WORLD-001/002/003 | Source/time, evidence-vs-inference labels, derived provenance, confidence validation | World-state beliefs and staleness policy |
| DEV-003, ARCH-003 | Fake capability/model/observation adapter paths used in tests | Production adapters and provider provenance integration |
| EVT-001, TASK-001/002, REL-002 | Event/task identity/state/correlation/idempotency metadata only | Durability, crash recovery, retry enforcement |

Only the scoped contract acceptance checks are claimed. These tests do not prove
permission enforcement, principal isolation during retrieval, crash recovery,
continuous initiative, or a complete v0.1 kernel. The two fully implemented
foundation features are validated contracts and capability metadata discovery.

## Decisions still open

No owner decision blocks this reversible foundation. Before Phase 2 execution,
choose a provisional trusted identity/bootstrap and grant/revoke model, record
its limits, and specify enforcement plus audit behavior. Before Phase 3 choose a
local persistence backend and define crash ambiguity, transitions, retries, and
idempotency semantics. Final schema fields, authentication stack, policy syntax,
API transport, event bus, task scheduler, model providers, data classification,
retention periods, sensors, profiling rules, notification services, initiative
thresholds, and SLOs remain unresolved.

## Next smallest milestone

Implement trusted session lookup, explicit owner-managed in-memory grants,
deterministic policy evaluation, a fake capability with validated inputs/outputs,
and an audit sink. Test owner allow, guest denial before handler invocation,
forged proposal/session claims, revocation before subsequent execution, and
cross-user access. Keep this local and model-free; persistence follows separately.

## Validation performed

On Python 3.12.14, the complete pytest suite executed **116 tests, all passing**.
Ruff lint and formatting checks passed. `uv sync --locked` succeeded and was
repeated without dependency changes. `uv build` produced both a source distribution
and a wheel. The installed CLI smoke passed from the editable checkout and from
the wheel in a separate virtual environment with `/tmp` as the working directory.
`git diff --check` passed. CI is configured for Python 3.12 and 3.13; hosted CI and
the Python 3.13 matrix were not executed in this local validation.
