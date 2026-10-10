# JARVIS Architectural Decisions

This file is an ADR index and initial decision record set.

Decisions marked **Accepted** are binding until superseded.

---

## ADR-001 — Self-Hosted Core

**Status:** Accepted  
**Date:** 2026-10-08

### Context
JARVIS is intended to remain useful and controllable without depending on a third-party AI service.

### Decision
The JARVIS core will be self-hosted.

Cloud services may augment the system, but core identity, authorization, persistent state, and local capabilities must not require cloud AI to function.

### Consequences
- Local deployment is the baseline.
- Infrastructure and model adapters must tolerate cloud unavailability.
- Operational complexity is accepted in exchange for control and privacy.

---

## ADR-002 — Optional Cloud AI

**Status:** Accepted  
**Date:** 2026-10-08

### Decision
Cloud AI is optional and accessed through replaceable adapters.

### Consequences
- Model/provider interfaces are required.
- Provider choice must not become the authorization mechanism.
- Protected data may reach cloud AI only through explicit policy exceptions.

---

## ADR-003 — Permission-Governed Autonomy

**Status:** Accepted  
**Date:** 2026-10-08

### Decision
JARVIS may act autonomously, but autonomy is bounded by the same identity, permission, and policy model that governs requested actions.

### Consequences
- Initiative can create proposals/tasks.
- Initiative cannot directly bypass policy.
- High privilege does not belong to the model.

---

## ADR-004 — Local-First Data Processing

**Status:** Accepted  
**Date:** 2026-10-08

### Decision
Processing and storage are local-first. Cloud processing is an explicit exception.

### Consequences
- Local paths are the default.
- Egress policy must be enforceable outside prompts.
- Cloud-bound context should be minimized and auditable.

---

## ADR-005 — Local Development, Homelab Scale-Out

**Status:** Accepted  
**Date:** 2026-10-08

### Decision
Build and validate the initial system locally, then scale deployment into homelab infrastructure.

### Consequences
- Early implementation may be a modular monolith.
- Interfaces should support later service/process separation.
- Avoid premature distributed-system complexity.

---

## ADR-006 — Multi-User JARVIS

**Status:** Accepted  
**Date:** 2026-10-08

### Context
JARVIS should be usable by people other than the owner, with different levels of authority.

### Decision
JARVIS is a multi-user system. Guests may request permitted functions. The owner controls per-user/relationship permissions.

### Consequences
- Every sensitive operation needs principal/session context.
- Memory isolation is required.
- Relationship context may inform behavior but cannot itself grant privileges.

---

## ADR-007 — Authorization Separate from Cognition

**Status:** Accepted as architectural invariant  
**Date:** 2026-10-08

### Decision
Authorization is enforced independently of language/reasoning models.

### Consequences
- LLM output is a proposal, not permission.
- Prompt injection cannot legitimately grant access.
- Capability execution requires a trusted policy decision.

---

## ADR-008 — Replaceable Models

**Status:** Accepted as architectural invariant  
**Date:** 2026-10-08

### Decision
AI models are implementation components behind interfaces, not the system architecture itself.

### Consequences
- Local and cloud models can be swapped.
- Test fakes should be possible.
- Persistent data should not depend on one provider's opaque conversation state.

---

## ADR-009 — Recoverable Task Model

**Status:** Accepted as architectural invariant  
**Date:** 2026-10-08

### Decision
Long-running/multi-step work must use explicit persistent task state.

### Consequences
- Restarts should not erase task intent.
- Side effects need retry/idempotency thinking.
- Hidden chain-of-thought is not task state.

---

## ADR-010 — Scoped, Provenance-Aware Memory

**Status:** Accepted as architectural invariant  
**Date:** 2026-10-08

### Decision
Memory records must be scoped and preserve provenance plus policy/consent information where relevant.

### Consequences
- One global conversation history is insufficient.
- Retrieval must filter by principal/policy.
- Derived memories should remain distinguishable from source evidence.

---

## ADR-011 — Uncertainty-Aware World Model

**Status:** Accepted as architectural invariant  
**Date:** 2026-10-08

### Decision
JARVIS must distinguish observation from inference and represent uncertainty/staleness in world state.

### Consequences
- Sensor/model output is not automatically fact.
- Decisions may need freshness/confidence thresholds.
- Provenance links observations to beliefs.

---

## ADR-012 — Observable Failures

**Status:** Accepted as architectural invariant  
**Date:** 2026-10-08

### Decision
Core failures and denied actions must be visible through structured state/audit/telemetry.

### Consequences
- Silent failure is unacceptable for core actions.
- Correlation IDs should connect request/task/action/result.
- Recovery and debugging are first-class concerns.

---

# Proposed / Unresolved ADRs

## ADR-P01 — Person Baseline Creation

**Status:** Proposed / unresolved

### Proposal
When JARVIS encounters a new person, create a baseline person record that can accumulate authorized context.

### Open questions
- What confidence establishes identity?
- Is a record created before consent?
- What data may be retained?
- How are mistaken identities merged/split/deleted?
- What is visible to that person vs owner?

No implementation should assume automatic unrestricted profiling.

---

## ADR-P02 — Public Internet Enrichment of Person Profiles

**Status:** Proposed / unresolved

### Proposal
JARVIS may use public internet research to learn more about a newly encountered or known person.

### Constraints already established
- provenance;
- uncertainty;
- permission/consent;
- sensitive-profile limits;
- privacy isolation;
- revocation.

### Open questions
- owner opt-in vs subject consent;
- which source types are permitted;
- which data classes are prohibited;
- retention;
- when research may trigger automatically.

---

## ADR-P03 — Environmental Perception via Cameras / EDITH Glasses

**Status:** Proposed architecture direction

Authorized cameras and EDITH-style wearable glasses are intended future observation sources.

Hardware, protocols, CV stack, and identity-processing rules remain open.

---

## ADR-P04 — Urgency-Based Phone/Text Escalation

**Status:** Proposed product behavior with established preference

Urgent events should be able to trigger calls; non-urgent events should be able to trigger texts.

Provider integrations and exact severity rules remain open.

---

## ADR-P05 — Performance SLOs

**Status:** Proposed

Previously discussed targets:
- voice acknowledgement p95 < ~800 ms;
- local commands < ~1 s where feasible;
- recovery < ~30 s;
- durable events survive a process crash.

These should not be treated as binding until explicitly ratified.

---

# Legacy Decisions / Context

## LEGACY-001 — "Jarvis" Wake Word with Porcupine
Accepted in a 2024 prototype. Not yet ratified for the new v0.1 architecture.

## LEGACY-002 — Text-File Conversation History
Used/proposed in an early prototype. Superseded in direction by structured scoped memory.

## LEGACY-003 — Ollama / Local Model Direction
Local Ollama-based experiments and custom model/fine-tuning were explored in 2025. Local models remain compatible with v0.1, but no specific model is currently selected.

---

## ADR-T01 — Phase 0/1 Python Contract Foundation

**Status:** Provisional implementation choice; not a final architectural decision
**Date:** 2026-10-09

### Context and choice
The initial implementation request selects Python for the kernel foundation.
Python 3.12+, Pydantic v2, uv, pytest, and Ruff provide a small local package with
strict, serializable v1 contracts and reproducible development checks. A local
JSON Schema Draft 2020-12 metadata registry supports simulated discovery.

### Scope and consequences
Schemas, UUID identifiers, synchronous protocol signatures, scope labels, and
minimal result/task states are reversible development choices. They do not select
the final policy language, authentication mechanism, scheduler, database, model,
API transport, or deployment topology. No privileged executor is supplied yet.

### Migration and validation
The repository previously had no implementation or persisted application data.
Future wire changes require explicit schema/version handling and migration tests.
See `docs/FOUNDATION.md` for review, limits, requirement coverage, and pytest checks.

---

## ADR-T02 — Offline Phase 2 Trust and Execution Boundary

**Status:** Provisional implementation choice; not final production security architecture
**Date:** 2026-10-09

### Context and choice
IAM-001/002/003/004/007, PER-001/002/003, CAP-001/002/003, and SEC-003/004 require
a model-independent policy boundary before real integrations. For this local
milestone, server-owned identity fixtures issue opaque session tokens; proposals
carry IDs but no credentials or policy authority. One bootstrapped owner manages
exact principal/permission grants, including its own capability grants.

Trusted registry wiring binds schemas, providers, and optional existing
AccessScope labels. The executor alone validates, obtains a current deterministic
decision, invokes, validates output, and audits the outcome. Policy audits must
succeed before invocation. No cached/caller-supplied decision is accepted.

### Scope and consequences
All state and audit records are in memory. A process-local lock serializes
identity/grant mutation with synchronous invocation; already-started work is not
cancelled by a waiting revocation. Auditing failure before a grant/change or
invocation prevents it. An outcome-audit failure after invocation is reported as
failure without claiming rollback or retry safety. There is no production
authentication, role inheritance, durable authorization, remote schema fetching,
real provider integration, or sandbox for untrusted Python plugins.

### Migration and validation
Existing v1 wire contracts are reused unchanged. The Python PolicyEvaluator now
takes a separate session token and optional trusted scope rather than accepting
an arbitrary Session as authority. There are no external protocol consumers or
persisted grants to migrate. Future authentication/storage/concurrency decisions
must replace the named test fixture methods and add durability/recovery tests.
`tests/test_phase2.py` and `jarvis demo` cover the actual enforcement boundary;
`docs/PHASE2.md` maps exact tested behavior and outstanding acceptance criteria.

---

## ADR-T03 — Provisional Local Durability and Safe Sequential Recovery

**Status:** Provisional implementation choice; owner authorized SQLite for Phase 3,
not a final database, scheduler or production identity decision
**Date:** 2026-10-09

### Context and choice
EVT-001, TASK-001/002, REL-001/002 and ARCH-004 require actual durable events and
recoverable work while PER-001/IAM-007/CAP-002/003 retain current model-independent
authority. Use Python stdlib SQLite behind repository interfaces: explicit numbered
migrations, foreign keys, WAL, synchronous FULL, short immediate write transactions
and a five-second busy timeout. Event append is idempotent for identical IDs/content
and rejects conflicting duplicates. Task/history/event/audit checkpoints commit
atomically with revision checks; no SQL transaction spans provider execution.

Extend the existing Task envelope explicitly as TaskRecord v2, preserving v1
decoding without guessing runnable plans. A small sequential runner uses only
CapabilityExecutor; its trusted post-authorization/pre-invocation hook persists
an attempt. Task submission keys are owner-scoped; step keys are task/step scoped.
The trusted registry declares provider idempotency, and the executor passes stable
keys independently of untrusted proposal arguments. POSIX file claims release on
actual process exit and prevent concurrent execution on one local host/path.

### Consequences and scope
Completed checkpoints are immutable. Abandoned attempts have unknown outcomes;
pure/idempotent providers with unchanged replay metadata may retry, while unknown
non-idempotent effects enter terminal NEEDS_REVIEW. Timeouts/safe audit failures
can wait for an explicit retry. Cancellation is cooperative, not provider rollback.
Persisted authority references do not authenticate or grant privileges: every
resumed step rechecks current Phase 2 policy. Identity/grants remain in memory;
trusted fixtures establish current authority independently after restart. Durable
audits extend the original in-memory scope of ADR-T02; its trust boundary remains.

This does not supply arbitrary external exactly-once guarantees, distributed
leases, network-filesystem support, production auth, encryption/tamper resistance,
retention, automatic review resolution or later-phase integrations. The original
confirmed ADRs remain unchanged.

### Migration and validation
Storage schema v1 and task wire v2 are separate versions. Future/invalid versions
fail closed. There was no persisted v1 task database to migrate; any later v1 task
import must supply a trusted explicit plan/migration. A PostgreSQL adapter can
replace SQL migrations, transactions, uniqueness/CAS and claim implementation
through the existing protocols; the final engine/scheduler remains OPEN.

The actual seven-process recovery demo and tests cover acknowledged event restart
durability, completed-step preservation, provider-effect crash/deduplication,
ambiguous-outcome review, revocation, concurrent claims/revision conflicts,
cancellation, atomic rollback, storage failures and corrupted/unsupported records.
See `docs/PHASE3.md` for operational backup/reset procedures and precise limits.

---

## ADR-T04 — Local Durable Authority and Cross-Process Security Verification

**Status:** Provisional local hardening authorized for the verification milestone;
not an accepted production authentication, policy, scheduler or database decision
**Date:** 2026-10-10

### Context and choice
Review found that Phase 3 audits/tasks were durable but principals/sessions/grants
were ephemeral and the offline demo re-granted in each worker. IAM-001/002/007,
PER-001/003, SEC-004 and REL-002 require durable current authority and auditable
permission changes before local scoped-memory development. Add SQLite adapters
through existing identity/permission interfaces, reusing owner-only administration
checks and the unchanged deterministic policy/executor boundary.

Storage migration 2 adds versioned security authority, Principal/Session records,
versioned grants with retained revocations and exact active-grant uniqueness.
Permission mutation and its successful audit commit atomically. Missing or invalid
authoritative state denies; historical task/allow references cannot restore grants.
A reentrant POSIX security claim spans current checks/synchronous invocation and
permission mutation across local processes, without a long SQL transaction.
Existing task claims/revision checks remain; task claiming precedes waiting for
authority so competing recovery can return busy without private task data.
Hard-linked database aliases are rejected; canonical symlinks share claims.

The default SQLiteIdentityRepository requires a trusted SessionAuthenticator and
refuses development stores/sessions. Only an explicit development subclass issues
fixture credentials/bootstrap records. Random fixture tokens are verified against
SHA-256 digests; no raw bearer token is persisted. The demo bootstraps/grants once
and subsequently reopens current authoritative state. This extends ADR-T02/T03's
original in-memory security scope without changing their confirmed trust boundary.

### Limits and migration
Explicit initialization preserves schema 1 journals/tasks/audits and adds empty
security tables. It never imports lost ephemeral privileges or provisions an owner
from task IDs. Existing Event v1/TaskRecord v2 wire formats remain unchanged.
Storage/metadata/lock choices are reversible through repository protocols.

No TTL or lease-based takeover is introduced: a live or hung worker keeps its
claims, and process death releases them without proving provider effects absent.
Recovery still checks replay contracts and requires review for ambiguous effects.
This is one local POSIX host, not distributed orchestration, anti-rollback storage,
production authentication, public scoped storage APIs or tamper-resistant auditing.
IAM-005, PER-004 and TASK-003/004 remain OPEN; owner provisioning/recovery and
real authentication integration must be decided before production exposure.

### Validation
`tests/test_security_storage.py` and `tests/test_security_processes.py` verify
restart-persistent authority/audits, revocation, missing/corrupt state denial,
fixture isolation, atomic permission/audit rollback, deterministic process races,
actual SIGKILL claim release, safe provider deduplication, ambiguous review and
completed-checkpoint preservation. See `docs/PHASE3_VERIFICATION.md` for exact
findings, tested guarantees and prerequisites for Phase 4.
