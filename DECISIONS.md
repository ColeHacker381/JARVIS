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
