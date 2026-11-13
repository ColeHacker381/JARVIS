# JARVIS Implementation Roadmap

This roadmap sequences implementation without pretending unresolved choices are settled.

Phase 9 now integrates the local always-on development runtime, authenticated
loopback API, bounded scheduler, diagnostics and developer/operator documentation.
See [documentation index](docs/README.md), [v0.9 release report](docs/releases/v0.9.md)
and provisional ADR-T10. Production authentication and real integrations remain OPEN.

Current progress: Phase 0/1 tooling and validated contracts plus the Phase 2
in-memory identity, owner grant/revoke, deterministic policy, validated execution,
and audit pipeline are implemented. `jarvis demo` exercises simulated capabilities.
Production authentication/provisioning and production security are not complete.
The verification pass adds durable local authority and tests independent processes;
see `docs/PHASE3_VERIFICATION.md` and provisional ADR-T04. See `docs/FOUNDATION.md` and `docs/PHASE2.md` for coverage and limits.
Phase 3 now adds a provisional SQLite event/task/audit adapter, checkpointed
sequential execution and real subprocess recovery (`jarvis recovery-demo`).
See `docs/PHASE3.md` and ADR-T03. Phase 4 now implements persistent permission-scoped
memory, explicit supersession/history, bounded scoped search, retention/deletion
and actual separate-process verification (`jarvis memory-demo`). See docs/PHASE4.md
and ADR-T05. Phase 5 implements bounded cognition/context building, fake/local
model adapters, isolated persistent conversations and text CLI (docs/PHASE5.md,
provisional ADR-T06). Offline and protocol checks are implemented; genuine model
inference remains unverified; earlier weight-download attempts were blocked.
Phase 6 now adds a persistent evidence-based world model, simulated sensors,
scoped cognitive world context and a two-process world-demo (docs/PHASE6.md, ADR-T07).
Production exposure still requires trusted authentication/provisioning and collection policies.

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

**Implementation status:** Complete for the scoped local milestone. The real SQLite
service supports ownership/current grants, private/shared/explicit household scope,
source/consent/retention metadata, current/history/date retrieval, supersession,
revocation/tombstones and deterministic scoped keyset search. Unit, migration and
real-process tests cover privacy, restart, audit rollback and competing revisions.
The replaceable backend is exercised with real SQLite and injected failure fakes;
no separate in-memory memory store is added. Production authentication/collection
policy, physical purge and final retention durations remain OPEN.

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

**Implementation status:** Offline workflow and local OpenAI-compatible adapter
implemented. Persistent conversations, current scoped memory context, validated
one-action decisions, established task/executor integration, provenance, bounded
history, failures and trusted default-deny cloud egress are tested. `jarvis
cognition-demo` uses two real processes; `jarvis chat` offers explicit fake mode
and configured local inference. Genuine-model connectivity remains an acceptance
gap in this environment until weights/endpoint are available; protocol test-server
success is not genuine AI. COG-002 stays PROPOSED, implemented provisionally.
Production authentication/public transport and final model/policy choices stay OPEN.

**Goal:** add intelligence without weakening the kernel.

### Implement
- model-provider interface;
- local OpenAI-compatible adapter plus deterministic development fake;
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

**Implementation status:** Complete for the scoped simulated/local milestone.
Trusted ingestion, explicit v2 evidence, durable events/projections, deterministic
confidence/conflict/TTL, private/shared isolation, current-policy retrieval and
bounded verified cognitive world context are implemented. `jarvis world-demo`
uses two real processes and real SQLite/policy services. Numeric metrics report
only measured or provider-reported values. See docs/PHASE6.md and provisional
ADR-T07. Physical adapters, production device auth, retention policy and initiative
remain deferred; WORLD-004 stays PROPOSED. Genuine-model acceptance remains an
existing Phase 5 gap in this environment.

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

**Implementation status:** Local simulated milestone implemented. Finite scoped
event passes, default observe-only, exact durable approvals, separately granted
LOW simulated automatic actions, restart-safe suppression/rate limits, task guards,
evidence/revocation checks, isolated inbox and bounded cognitive explanations are
covered by docs/PHASE7.md and provisional ADR-T08. Continuous scheduling,
production authentication, final risk/threshold/retention choices and actual
external delivery remain OPEN. No unrestricted model autonomy is installed.

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

**Implementation status:** Offline local/simulated milestone implemented: durable
recipient-scoped inbox, trusted routing, separate interactions/approval links,
replaceable channels, task-backed attempts/guards, conservative unknown-outcome
recovery, retained deduplication, preferences/quiet hours and bounded cognition.
See docs/PHASE8.md and provisional ADR-T09. Real phone/text/push, provider choice,
production authentication, final severity/retention and scheduling remain OPEN.

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

## Phase 9 — Integrated Runtime, Local API and Documentation

**Implemented local development slice:** shared composition root; explicit
credential-bound current sessions; loopback HTTP v1; finite non-overlapping
initiative/notification/task recovery; readiness/owner metrics; bounded graceful
shutdown; same-store restart; online backup/restore and coherent checked docs.
Migration 8 preserves all authority/state and adds revocable credential bindings.
No model-driven privileges or automatic policy activation are introduced.

**Limits:** fake/local-only models, no verified genuine inference in this cloud
workspace, no production authentication/public exposure/distributed scheduler or
real external effects. All final runtime/auth/API decisions remain OPEN.

## Deferred proposal — Real Sensors and EDITH-Style Wearables

The earlier Phase 9 proposal is retained here as deferred potential work. The
explicit Phase 9 request selected integration instead; no hardware is authorized
by this roadmap entry.

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

## Phase 10 — Local Intelligence and Runtime Stabilization

User-authorized local voice milestone: explicit push-to-talk HTTP client,
whisper.cpp and system offline TTS adapters, bounded PCM/VAD/multi-source simulation,
ephemeral room contexts, attention and unconfirmed project-memory candidates.
No source/label authenticates a person; existing policy/tasks/memory remain authoritative.
See [PHASE10.md](docs/PHASE10.md) for implemented scope and unverified hardware/inference.
INT-002 remains provisional; no production ambient intelligence is claimed.

Follow-on real speech validation verifies Linux whisper.cpp tiny.en/base.en and
eSpeak NG plus authenticated audio-file interaction. MacBook microphone, Gemma and
speaker acceptance remains the next operator gate; use
[the acceptance checklist](docs/VOICE_ACCEPTANCE.md). Do not advance to automatic
ambient capture/profiling or claim real diarization from the heuristic simulations.

The authorized Phase 10 cognition-hardening follow-on adds real local Qwen acceptance,
grounded engineering reasoning, scoped project continuity, fixture migration,
schema guidance and normal interrupted playback. See
[the hardening report](docs/PHASE10_COGNITION_HARDENING.md); this is not Phase 11.

The [mixed-request follow-on](docs/PHASE10_MIXED_INTENT.md) defers recognized combined
question/lighting commands, suppresses unsubmitted execution claims, and verifies
both configured local structured-output modes. Generalized intent planning remains
deferred; MacBook and physical voice acceptance are still operator gates.

The user-authorized [bounded multi-intent follow-on](docs/PHASE10_MULTI_INTENT.md)
preserves questions alongside independently authorized actions, partial outcomes and
restart-safe child checkpoints. General conditional planning and autonomous loops
remain deferred under provisional ADR-T12; this remains Phase 10.

## Future direction — PROPOSED, not started

The following phases supersede the earlier *planned sequencing*, not historical
implementation records or confirmed requirements. Dependencies are gates, not a
commitment to ship these designs. Final APIs, policy language, storage, model,
scheduling and production identity remain OPEN. Security prerequisites must be in
place before any externally accessible communication. Reuse existing components;
do not create parallel task, initiative, notification, voice or capability systems.

### Phase 11 — Autonomous Capability Intelligence (PROPOSED)

- Dependencies: Phase 10 stability, owner decisions on standing authority and goals.
- Functionality: persistent goals, accurate capability awareness, bounded standing
  delegations and goal-driven plans. Reuse scoped memory, registry, policy and tasks.
- Acceptance: goals survive restart; revoked delegations stop subsequent work;
  discovery distinguishes simulation, unavailable and approval-required operations.
- Security: model descriptions confer no authority; delegations are scoped,
  revocable, auditable and independently enforced. OPEN: delegation semantics,
  goal ownership, expiration and review; proposed ADR for delegated authority.

### Phase 12 — Background Autonomy (PROPOSED)

- Dependencies: Phase 11 authority and recoverable goals.
- Functionality: bounded independent initiative, schedules and event-driven work;
  reuse initiative policies, scheduler, task checkpoints and verification.
- Acceptance: restart recovery, duplicate suppression, limits and actual outcome
  reporting work under failures and revocation.
- Security: budgets, current permission checks and audit per effect; OPEN: scheduler,
  interruption and escalation policies; proposed ADR for background execution limits.

### Phase 13 — Unified Communication Gateway (PROPOSED)

- Dependencies: Phase 12 and Phase 14 security prerequisites before exposure.
- Functionality: inbound/outbound routing, continuity and notifications; reuse
  sessions, conversations, notifications, existing voice and authorization.
- Acceptance: scoped messages route with durable outcomes and no cross-user leakage.
- Security: authenticated channels, consent, replay protection and least privilege;
  OPEN: gateway trust boundaries, identity mapping and retention; proposed gateway ADR.

### Phase 14 — Secure Always-On Deployment (PROPOSED)

- Dependencies: production identity and threat model precede external access;
  core hosting hardening may proceed before external gateway work.
- Functionality: Fedora hosting, production identity, remote access, backups and
  monitoring; reuse runtime, durable stores and existing backup/health interfaces.
- Acceptance: tested recovery, credential rotation, monitoring and hardened remote
  access; verified GPU/model compatibility and resource budgets on target host.
- Security: encrypted transport and backups, least privilege, patching and audit;
  OPEN: production authentication, secret management, deployment topology and SLOs;
  proposed deployment and identity ADRs. RTX 4060 driver, VRAM, power and model
  latency must be measured on the actual Fedora host.

### Phase 15 — Real Communication Integrations (PROPOSED)

- Dependencies: secure Phase 14 deployment and Phase 13 gateway.
- Functionality: iPhone client, push, SMS, two-way voice and calls; reuse gateway,
  notifications, voice adapters, tasks and policy.
- Acceptance: end-to-end delivery, explicit failure/retry handling and revocation
  verified with real devices/providers; simulations remain labeled.
- Security: channel verification, consent, destination restrictions and provider
  privacy controls; OPEN: provider selection, call consent and credential handling.

### Phase 16 — Environmental Awareness (PROPOSED)

- Dependencies: Phase 14 security and explicit collection/retention decisions.
- Functionality: smart home, microphones, sensors, optional camera processing;
  reuse observations, world state, voice and capability registry.
- Acceptance: provenance, uncertainty, access filtering and device failure tests.
- Security: explicit enrollment, consent, local processing and retention limits;
  OPEN: hardware, identity confidence, camera policy and data lifecycle.

### Phase 17 — Advanced Social Intelligence (PROPOSED)

- Dependencies: scoped memory, explicit consent and governance decisions.
- Functionality: relationship context, consent-aware learning and communication
  adaptation; reuse memory provenance, identity and scoped access controls.
- Acceptance: correction, revocation and user-isolation demonstrated.
- Security: no silent sensitive profiling; OPEN: consent, sensitive attributes,
  relationship inference, retention and subject rights; proposed social-data ADR.

### Phase 18+ — Wearables, Optional Enrichment and Homelab (PROPOSED)

- Dependencies: secure deployment and the relevant communication/sensor phases.
- Functionality: optional wearables, public-profile enrichment and homelab expansion;
  reuse gateways, evidence, scoped memory, tasks and existing adapters.
- Acceptance: explicit enrollment, provenance, isolation and recovery demonstrated.
- Security: public research is never automatic by default; OPEN: consent, source
  allowlists, deletion, topology and provider selection. Earlier Phase 11 public
  enrichment and Phase 12 homelab proposals are retained here as deferred scope.

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
