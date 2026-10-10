# JARVIS Architecture v0.1

## 1. Architectural Goal

Build a self-hosted, permission-governed AI kernel whose intelligence can evolve without giving any reasoning model implicit authority over identity, private memory, or side effects.

## 2. Confirmed Architectural Invariants

1. **Self-hosted core**
2. **Optional cloud AI**
3. **Local-first processing/storage**
4. **Explicit cloud exceptions**
5. **Independent authorization boundary**
6. **Replaceable models**
7. **Versioned contracts**
8. **Recoverable tasks**
9. **Scoped/provenanced memory**
10. **Uncertainty-aware world state**
11. **Bounded autonomy**
12. **Observable failures**
13. **Multi-user isolation**
14. **Local development → homelab deployment**

## 3. Logical Component Model

```text
                        ┌─────────────────────────────┐
                        │        Interfaces           │
                        │ voice / text / GUI / APIs   │
                        └──────────────┬──────────────┘
                                       │
                                       v
┌──────────────────┐       ┌─────────────────────────────┐
│ Identity /       │──────>│     Cognitive Coordinator   │
│ Session Context  │       │ intent / reasoning / plan   │
└────────┬─────────┘       └──────────────┬──────────────┘
         │                                │ proposals
         │                                v
         │                    ┌──────────────────────────┐
         └───────────────────>│ Policy / Authorization   │
                              └──────────────┬───────────┘
                                             │ allow/deny
                                             v
                                  ┌───────────────────────┐
                                  │ Capability Executor   │
                                  └───────────┬───────────┘
                                              │
                          ┌───────────────────┼──────────────────┐
                          v                   v                  v
                    local services      external apps      device actions

      ┌─────────────────┐   ┌────────────────┐   ┌─────────────────┐
      │ Event Store     │   │ Task Engine    │   │ Audit / Telemetry│
      └─────────────────┘   └────────────────┘   └─────────────────┘

      ┌─────────────────┐   ┌────────────────┐   ┌─────────────────┐
      │ Memory Service  │   │ Observation    │   │ World Model     │
      │ scoped/proven.  │   │ Ingestion      │   │ uncertainty     │
      └─────────────────┘   └────────────────┘   └─────────────────┘

                           ┌──────────────────┐
                           │ Initiative Engine│
                           └────────┬─────────┘
                                    │ proposals/tasks
                                    └─────────────> policy boundary
```

This is a logical architecture, not a mandate for separate processes in the first implementation.

## 4. Core Domain Contracts

The previous architecture discussion identified the following contracts as the kernel vocabulary.

### Principal
Represents an authenticated or known actor.

Expected concepts:
- principal ID;
- principal type/role;
- status;
- relationship metadata reference;
- policy references.

**Exact schema: OPEN.**

### Session
Represents a bounded interaction/security context.

Expected concepts:
- session ID;
- principal ID or unknown identity;
- authentication context;
- device/interface;
- start/end/expiry information.

**Exact schema: OPEN.**

### Event
Represents a versioned occurrence or state transition.

Expected concepts:
- event ID;
- type;
- schema version;
- timestamp;
- causation/correlation;
- actor/source;
- payload.

### Capability
Describes an executable function.

Expected concepts:
- capability ID/name;
- input contract;
- output/result contract;
- required permissions;
- side-effect classification;
- adapter/handler reference.

### Action Proposal
Represents something a model, user request, rule, or initiative subsystem wants to do.

Expected concepts:
- proposal ID;
- originating principal/session/task;
- proposed capability/action;
- validated arguments;
- rationale/provenance;
- required permissions;
- risk/side-effect metadata where applicable.

### Task
Represents recoverable multi-step work.

Expected concepts:
- task ID;
- state;
- owner/requester;
- current step;
- input/output references;
- retry/idempotency information;
- correlation IDs;
- timestamps.

### Memory
See `MEMORY_SYSTEM.md`.

### Observation
Represents evidence from an input source.

Expected concepts:
- observation ID;
- source;
- time observed;
- time recorded;
- payload/fact;
- confidence;
- privacy scope;
- provenance.

### Policy
Represents authorization/privacy/egress rules.

**Policy language/model: OPEN.**

### World State
Represents current beliefs derived from observations and other trusted data.

Expected concepts:
- entity/fact key;
- value;
- observed vs inferred;
- confidence;
- evidence references;
- valid/effective time;
- staleness.

### Audit Record
Represents security- and execution-relevant trace information.

Expected concepts:
- actor/principal;
- action;
- decision/result;
- correlation;
- timestamp;
- policy basis;
- redacted metadata.

## 5. Request / Action Flow

A privileged user request should follow this shape:

```text
Interface
  -> Session/Identity
  -> Cognitive Coordinator
  -> Action Proposal
  -> Input Validation
  -> Policy Decision
  -> Capability Execution
  -> Structured Result
  -> Event + Audit + Task update
  -> Response
```

The model may propose an action but does not authorize itself.

## 6. Autonomous / Initiative Flow

```text
Authorized Observation/Event
  -> Observation Store
  -> World-State Update
  -> Initiative Evaluation
  -> Suggestion OR Action Proposal
  -> Policy Decision
  -> Task/Capability Execution
  -> Audit + Notification
```

Autonomous work must use the same authorization boundary as interactive work.

## 7. Memory Architecture

Memory should be queried by relevance and scope rather than appended wholesale to every prompt.

Logical layers:

```text
raw event / interaction / observation
          │
          v
  extraction / classification
          │
          v
  scoped memory record
          │
          ├── provenance
          ├── consent/policy basis
          ├── temporal validity
          ├── confidence
          ├── access scope
          └── retention/deletion metadata
          │
          v
 context retrieval filtered by
 principal + task + policy + relevance
```

See `MEMORY_SYSTEM.md`.

## 8. Model Architecture

The system should expose model adapters rather than a single hard-coded model.

Potential model roles:
- dialogue/reasoning;
- planning;
- summarization;
- memory extraction;
- perception/CV;
- embeddings/retrieval;
- speech recognition/synthesis.

No particular model/provider is confirmed for v0.1.

Cloud adapters must pass through egress/privacy policy before receiving protected context.

## 9. Deployment Evolution

### Phase A — Local development
A single-machine implementation is acceptable if component boundaries remain explicit.

### Phase B — Local service decomposition
Selected components may move into separate services/processes.

### Phase C — Homelab deployment
Stateful services, model hosts, sensor bridges, or workers may run on multiple self-hosted machines.

The architecture should avoid assumptions that make Phase C require a rewrite.

## 10. Observability

Core operations should use stable correlation IDs across:
- request/session;
- task;
- action proposal;
- capability execution;
- event;
- audit record.

At minimum, operators should be able to answer:
- Who requested or triggered this?
- What did JARVIS believe?
- Which capability was proposed?
- Which policy allowed/denied it?
- What executed?
- What failed?
- What memory/world state was used?
- Was any data sent to a cloud provider?

## 11. Reliability

Important work should not exist only in model context or Python call stacks.

Persist before or around critical transitions so restart recovery can identify:
- unfinished tasks;
- ambiguous executions;
- failed capabilities;
- pending review;
- pending notifications.

Exact transactional/event-sourcing design remains open.

## 12. Architecture Questions Still Open

- relational DB vs document DB vs multiple stores;
- event sourcing vs conventional state + event log;
- queue/broker technology;
- synchronous vs async service framework;
- authorization policy engine;
- task scheduler;
- schema library and serialization format;
- API transport;
- observability stack;
- identity/authentication stack;
- model routing;
- sensor streaming architecture.

## 13. Current Foundation Implementation

The Phase 0/1 implementation uses the `src/jarvis` Python package. Core v1
contracts and decoding live in `core/`; identity, policy, events, tasks, memory,
observations, cognition, initiative, and audit expose narrow adapter protocols.
Capability metadata registration/discovery and structured logging are working
local implementations. `jarvis smoke` exercises this finite offline foundation.

Phase 2 adds server-owned development sessions, separate opaque bearer tokens,
owner-only explicit grants, deterministic default-deny policy, registered fake
provider execution, input/output validation, and linked in-memory audit records.
The executor resolves current trusted state; proposals cannot carry authorization.
Registered private/shared scopes restrict provider access independently of action
permissions. `jarvis demo` exercises the full offline pipeline.

Phase 3 adds SQLite implementations of event/task/audit protocols, explicit
TaskRecord v2 alongside legacy Task v1, atomic revision-checked checkpoints and
a sequential TaskManager. A trusted executor hook records an attempt after current
authorization and before provider invocation; the executor remains the only
provider invocation path. Results are durably checkpointed before advancing.
Recovery preserves completed steps, retries only pure/idempotent operations under
an unchanged trusted replay profile, and sends ambiguous non-idempotent work to
terminal NEEDS_REVIEW. Fresh credentials and current grants are required.

SQLite WAL/FULL transactions and POSIX crash-released task claims are local
provisional adapters, not distributed orchestration or final database selection.
Events/history/audits are append-only by application convention; provider effects
are outside the checkpoint transaction. Arbitrary external exactly-once semantics
are not claimed. The verification milestone adds durable principal/session/grant/revocation
adapters and a host-wide reentrant security claim around checks/invocation and
permission changes. Task claims precede authorization waiting and remain exclusive
while active. Default durable identity wiring denies without a trusted authenticator
and refuses development authority/session records. Only the explicit development
subclass bootstraps fixtures; startup never infers authority from saved task IDs. No production authentication, world-state inference, or continuous initiative is implemented.
ADR-T01/T02/T03/T04/T05 document provisional choices. See `docs/FOUNDATION.md`,
`docs/PHASE2.md`, `docs/PHASE3.md`, and `docs/PHASE3_VERIFICATION.md` for coverage,
operations and trust boundaries.

Phase 4 adds MemoryService domain logic behind trusted token/current-policy checks
and private MemoryStore internals, using the same SQLite journal/authority/audits.
Explicit MemoryRecord v2 extends readable Memory v1; migration 3 adds memory/readers/
subjects and indexed transactional persistence. Scoped selection precedes decoding
and search ordering/pagination. Corrections preserve immutable source/content in
linked historical records. Current/history/date reads enforce current grants,
expiry and deletion; sensitive writes/index mutations and outcome audits are atomic
with expected revisions. Existing capability invocation remains through the executor.
The two-process memory-demo verifies real persistence and privacy. See docs/PHASE4.md
for temporary permissions, trust boundaries and remaining production policies.

Phase 5 extends cognition with a bounded coordinator, ContextBuilder and replaceable
asynchronous model providers. ModelRequest/CognitiveDecision/Conversation/Turn/
Response v1 use explicit nested wire versions and existing ActionProposal and
ExecutionResult semantics. Context includes only bounded current authorized memory,
permitted recent history and capability metadata discovered through current policy.
Retrieved content is data, never instruction/authority. Memory dependencies are
rechecked before history reuse and before accepting model output.

One model call yields at most one proposed action. Validated intent is persisted
before TaskManager submission with a stable owner-scoped key; the existing executor
revalidates arguments/current authority, and observed task/results determine action
responses. Conversation claims and revision-checked SQL/event/audit commits preserve
accepted intent, isolated history and task links across restart. Completed request
replays skip invocation; ambiguous effects retain NEEDS_REVIEW. Migration 4 adds
conversation tables without changing existing authority or memory/task contracts.

The local adapter uses a pinned loopback OpenAI-compatible endpoint; the deterministic
fake keeps core tests/demo fully offline. Cloud boundaries require trusted enablement,
provider/destination and per-data-class current egress grants before adapter invocation.
No cloud implementation/fallback is installed. Development chat identities/bootstrap
are explicitly fixture-only; a conversation UUID never authenticates a caller.
COG-002 is provisional, and final auth/model/routing/data-classification decisions
remain OPEN. See docs/PHASE5.md and provisional ADR-T06 for validation limits.
