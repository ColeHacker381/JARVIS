# JARVIS Implementation Roadmap

This roadmap sequences implementation without pretending unresolved choices are settled.

Current progress: Phase 0/1 tooling and validated contracts plus the Phase 2
in-memory identity, owner grant/revoke, deterministic policy, validated execution,
and audit pipeline are implemented. `jarvis demo` exercises simulated capabilities.
Production authentication/provisioning and production security are not complete.
The verification pass adds durable local authority and tests independent processes;
see `docs/PHASE3_VERIFICATION.md` and provisional ADR-T04. See `docs/FOUNDATION.md` and `docs/PHASE2.md` for coverage and limits.
Phase 3 now adds a provisional SQLite event/task/audit adapter, checkpointed
sequential execution and real subprocess recovery (`jarvis recovery-demo`).
See `docs/PHASE3.md` and ADR-T03. Local Phase 4 scoped-memory work can begin
using current policy and explicit development fixtures; production exposure still
requires a trusted authentication/provisioning decision and implementation.

## Phase 0 — Repository and Specification Baseline

**Goal:** make the repository safe for iterative Codex development.

### Deliverables
- documentation in this handoff package committed;
- initial repository structure;
- formatter/linter/test runner;
- configuration strategy;
- environment example file with no secrets;
- CI for unit tests;
- issue/ADR workflow.

### Exit criteria
- Codex can identify requirement IDs for work.
- Tests can run locally.
- No runtime architecture choice contradicts the accepted ADRs.

---

## Phase 1 — Kernel Contracts

**Goal:** establish the minimum stable domain model before model-heavy features.

### Implement
- `Principal`
- `Session`
- `Event`
- `Capability`
- `ActionProposal`
- `Task`
- `Memory`
- `Observation`
- `PolicyDecision`
- `AuditRecord`
- result/error envelopes
- schema versioning

### Notes
Exact schemas remain open. Implement minimal versions that satisfy confirmed requirements and are easy to migrate.

### Exit criteria
- contracts serialize/deserialize;
- schema validation tests exist;
- versions are explicit;
- no LLM is required.

---

## Phase 2 — Identity, Permissions, and Capability Execution

**Implementation status:** Complete for the scoped offline/in-memory milestone.
The required owner/guest/revocation/impersonation/private-result denial checks run
against the real policy/executor with fake providers. Storage, authentication,
transport, and final policy syntax remain open; no production readiness is claimed.

**Goal:** create the security boundary before autonomous behavior.

### Implement
- principal/session service;
- owner bootstrap;
- grant/revoke storage;
- policy interface;
- capability registry;
- argument validation;
- capability executor;
- audit records;
- fake capabilities.

### Required tests
- owner allowed;
- guest restricted;
- cross-user denial;
- prompt/model output cannot self-authorize;
- revocation blocks later privileged execution;
- denied action never reaches capability handler.

### Exit criteria
A fake model or direct API can propose actions, but only authorized capabilities execute.

---

## Phase 3 — Durable Events and Recoverable Tasks

**Goal:** remove important work from ephemeral call stacks.

### Implement
- persistent event storage;
- task state machine;
- correlation/causation IDs;
- retry-aware execution;
- restart recovery;
- structured failure states;
- operator inspection endpoint/CLI.

### Open decision likely required
The owner authorized provisional SQLite and a small sequential local runner for
this milestone (ADR-T03). Final storage and scheduler choices remain open.

### Implemented local milestone
The listed deliverables now work through repository protocols and the Phase 2
executor. Seven separate demo processes show persisted checkpoint preservation,
provider idempotent replay, ambiguous-outcome review and effective revocation.
Cancellation, immutable history, revision conflicts and storage/corruption failures
are tested. POSIX local locking is not a distributed worker system; production
authentication/provisioning, manual review resolution and real providers remain
future work. Local SQLite authority now persists across restart; synchronized
process tests prove exclusive task ownership, effective revocation and safe
crash handling. No OPEN authentication or scheduler decision is marked accepted.

### Exit criteria
- controlled crash does not lose persisted task/event state;
- restart detects unfinished tasks;
- failed work is visible;
- tests cover duplicate/retry hazards.

---

## Phase 4 — Memory v0

**Goal:** replace raw transcript-style memory with scoped structured memory.

### Implement
- memory service interface;
- local persistence;
- provenance;
- principal/access scope;
- validity timestamps;
- retention/deletion metadata;
- consent/policy reference;
- scoped retrieval;
- test fake/in-memory backend.

### Required tests
- persistence after restart;
- owner-only memory hidden from guest;
- user A private memory hidden from user B;
- derived memory marked as derived;
- revoked access affects retrieval.

### Explicitly defer
- automatic social profiling;
- internet enrichment of people;
- advanced vector retrieval unless needed;
- sophisticated consolidation/conflict resolution.

---

## Phase 5 — Cognitive Coordinator and Model Adapters

**Goal:** add intelligence without weakening the kernel.

### Implement
- model-provider interface;
- at least one local model adapter or development fake;
- optional cloud adapter interface;
- prompt/context builder;
- action-proposal parser;
- structured response validation;
- cloud egress check before protected context transmission.

### Exit criteria
- model can answer conversation;
- model can propose a capability action;
- invalid proposals are rejected;
- model cannot bypass permissions;
- provider can be swapped/faked.

---

## Phase 6 — Observation and World Model

**Goal:** represent changing reality with evidence and uncertainty.

### Implement
- observation ingestion interface;
- observation persistence;
- source/provenance;
- confidence;
- timestamps/staleness;
- derived world-state representation;
- fake sensor adapter.

### Exit criteria
- system can show why it believes a world fact;
- stale/low-confidence values are distinguishable;
- direct observation vs inference is queryable.

### Explicitly defer
Physical camera/EDITH integration until kernel behavior is proven.

---

## Phase 7 — Initiative Engine

**Goal:** allow JARVIS to proactively find useful work while preserving control.

### Implement
- initiative triggers from events/world state;
- suggestion generation;
- autonomous action proposals;
- policy enforcement;
- disable/pause controls;
- task creation;
- audit.

### Exit criteria
- JARVIS can proactively suggest something;
- privileged autonomous action still requires policy approval;
- initiative can be globally or selectively disabled.

---

## Phase 8 — Communication and Notification

**Goal:** route important events to the owner.

### Implement
- notification abstraction;
- urgency/severity field;
- policy-based channel routing;
- text adapter;
- call adapter;
- failure/retry handling.

### Established behavior
- urgent → potentially phone call;
- non-urgent → potentially text.

Provider choice remains open.

---

## Phase 9 — Real Sensors and EDITH-Style Wearables

**Goal:** connect authorized environmental awareness.

### Potential work
- camera adapter;
- wearable/glasses stream adapter;
- perception/CV model adapter;
- identity evidence integration;
- privacy/retention rules;
- bandwidth/storage controls.

### Must be decided first
- hardware;
- protocols;
- recording policy;
- identity confidence model;
- retention;
- cloud-use rules.

---

## Phase 10 — Social Intelligence

**Goal:** improve JARVIS's understanding of people and relationships without weakening privacy.

### Potential work
- person profiles;
- relationship graph;
- communication adaptation;
- interaction-derived social context;
- confidence/provenance;
- correction/merge flows.

### Still unresolved
- automatic new-person baseline behavior;
- consent model;
- sensitive profiling rules.

---

## Phase 11 — Optional Public Profile Enrichment

**Goal:** only if explicitly approved, enrich person context from public internet sources.

### Prerequisites
- explicit owner policy decision;
- sensitive-data classification;
- source allowlist/blocklist policy;
- provenance;
- revocation/deletion;
- audit;
- subject/owner consent rule.

### Status
Do not implement as an automatic default until these decisions are resolved.

---

## Phase 12 — Homelab Scale-Out

**Goal:** migrate from local development deployment to resilient self-hosted infrastructure.

### Potential work
- separate model host;
- persistent database service;
- task workers;
- event broker if justified;
- sensor gateways;
- observability stack;
- backups;
- remote access;
- secrets management.

Technology selection remains open.

---

# Near-Term Codex Backlog

Recommended first implementation sequence:

1. Create repository package/module structure.
2. Add domain contract schemas.
3. Add in-memory repositories and test fakes.
4. Implement principal/session model.
5. Implement minimal grant/revoke policy service.
6. Implement capability registry and validation.
7. Implement executor + audit.
8. Add tests for guest denial, revocation, and cross-user isolation.
9. Add persistent event/task backend behind repository interfaces.
10. Add restart-recovery integration tests.
11. Add memory interface and scoped retrieval.
12. Only then integrate a real model.

# Milestone Definition of "JARVIS Kernel v0.1"

The kernel milestone is complete when:

- runs locally;
- no cloud AI is required;
- multiple principals exist;
- owner can grant/revoke privileges;
- capabilities declare permission requirements;
- actions are policy checked;
- event/task state persists;
- interrupted work is recoverable/inspectable;
- memory is scoped and provenance-aware;
- observations/world state can represent uncertainty;
- audit records explain privileged actions/denials;
- a fake or real model can propose actions but cannot authorize itself;
- test suite demonstrates the security boundaries.

This milestone intentionally does **not** require full vision, wearable integration, phone/text providers, automatic social profiling, or public-internet person research.
