# JARVIS Requirements Specification v0.1

## 1. How to Read This Document

Requirement IDs are stable implementation references for Codex, tests, issues, and commits.

Status:
- **REQUIRED** — binding for v0.1 baseline.
- **PROPOSED** — discussed but not yet binding.
- **OPEN** — unresolved design choice.
- **LEGACY** — prior implementation behavior, not a v0.1 requirement.

Acceptance criteria are intentionally implementation-neutral where technology has not been selected.

---

# 2. Architecture and Core

### ARCH-001 — Self-hosted core
**Status:** REQUIRED

The JARVIS core services must be deployable on infrastructure controlled by the owner without requiring a cloud AI provider.

**Acceptance criteria**
- Core process(es) can start and perform non-cloud-dependent functions with cloud AI disabled.
- Configuration supports a "no cloud AI" deployment.
- Failure or unavailability of a cloud model does not prevent local authorization, memory access, task state, event storage, or local capabilities from operating.

### ARCH-002 — Optional cloud AI
**Status:** REQUIRED

Cloud AI must be an optional adapter/augmentation rather than a hard dependency.

**Acceptance criteria**
- At least one model/provider interface supports interchangeable implementations.
- Provider-specific logic is isolated behind that interface.
- A provider can be disabled without modifying authorization rules.

### ARCH-003 — Replaceable models
**Status:** REQUIRED

Reasoning, language, perception, or other AI models should be replaceable behind stable interfaces.

**Acceptance criteria**
- Model calls do not directly own persistent authorization state.
- At least one test double/fake model can be substituted for integration testing.
- Model identity/provider is recorded with relevant outputs where provenance matters.

### ARCH-004 — Versioned cross-component contracts
**Status:** REQUIRED

Persistent and cross-component event/data contracts must be explicitly versionable.

**Acceptance criteria**
- Persisted event records include a schema/event version.
- Consumers reject or safely handle unsupported versions.
- Contract changes can be tested without relying on free-form prompt compatibility.

---

# 3. Cognition / Coordination

### COG-001 — Cognitive coordinator
**Status:** REQUIRED

JARVIS must have a coordination layer that can transform an interaction, event, or initiative trigger into proposed actions/tasks without itself being the authority that grants permission.

**Acceptance criteria**
- Coordinator outputs are represented as explicit proposals/tasks rather than direct privileged execution.
- Authorization can deny a coordinator proposal.
- Tests can run the coordinator with a fake model.

### COG-002 — Direct-response path
**Status:** PROPOSED / LEGACY-INFLUENCED

Earlier JARVIS behavior preferred answering directly when available knowledge was sufficient, using special intents/functions only when needed.

**Acceptance criteria if adopted**
- Simple conversational responses need not invoke a capability.
- Capability selection is explicit and inspectable.

---

# 4. Identity and Access Management

### IAM-001 — Multi-user principals
**Status:** REQUIRED

JARVIS must represent more than one user/principal.

**Acceptance criteria**
- Two distinct principals can exist simultaneously in persistent state.
- Sessions reference a principal identity or an explicit unauthenticated/unknown state.
- Principal-specific access rules can differ.

### IAM-002 — Owner authority
**Status:** REQUIRED

The owner must be able to administer user permissions.

**Acceptance criteria**
- Owner can grant a permission to another principal.
- Owner can revoke a permission.
- Grant/revoke actions are audited.

### IAM-003 — Guest access
**Status:** REQUIRED

Guests may use JARVIS only within configured guest permissions.

**Acceptance criteria**
- A guest can perform at least one explicitly allowed non-privileged action.
- The same guest is denied an action not granted to the guest role/principal.
- Denial does not depend on model compliance.

### IAM-004 — No privilege escalation through conversation
**Status:** REQUIRED

A user must not gain capability privileges merely by prompting or persuading the model.

**Acceptance criteria**
- A model-generated claim such as "the user is authorized" does not alter policy state.
- Privileged execution requires policy-layer approval based on trusted session/principal context.

### IAM-005 — Authentication mechanism
**Status:** OPEN

The specific technologies used to authenticate voice, device, face, account, token, or other identity signals are not yet finalized.

### IAM-006 — Contextual/time-limited permissions
**Status:** PROPOSED

Permissions may eventually support time, context, device, location, or task-specific scope.

### IAM-007 — Revocation effectiveness
**Status:** REQUIRED

Revoked authority must not continue to authorize new privileged execution.

**Acceptance criteria**
- After revocation is committed, a subsequent privileged execution attempt is denied.
- Cached model/context state cannot override the current policy state.

---

# 5. Permission and Policy Engine

### PER-001 — Authorization independent of models
**Status:** REQUIRED

Authorization must be enforced by a deterministic/non-LLM policy boundary.

**Acceptance criteria**
- Privileged capability execution cannot occur without a policy decision.
- Policy evaluation can run with all language models disabled.
- Denial is final unless a new valid authorization context is supplied.

### PER-002 — Explicit capability permissions
**Status:** REQUIRED

Capabilities that can create side effects must declare the permissions they require.

**Acceptance criteria**
- Capability metadata identifies required permission(s).
- Executor checks required permission(s) before invocation.
- Missing permissions produce a structured denial.

### PER-003 — Auditable policy decisions
**Status:** REQUIRED

Privileged allow/deny decisions must be traceable.

**Acceptance criteria**
- Audit record identifies principal/session, requested action, policy decision, and timestamp.
- Sensitive arguments may be redacted, but the existence and result of the decision remain visible.

### PER-004 — Permission model syntax
**Status:** OPEN

RBAC, ABAC, capability tokens, policy-as-code, or a hybrid remain unresolved.

---

# 6. Capability System

### CAP-001 — Extensible capability registry
**Status:** REQUIRED

JARVIS must support adding functional capabilities without rewriting the core coordinator.

**Acceptance criteria**
- Capability can be registered with name/identifier, input schema, output schema or declared result contract, and required permissions.
- A new test capability can be added without changing core policy logic.

### CAP-002 — Validated execution
**Status:** REQUIRED

Capability inputs must be validated before execution.

**Acceptance criteria**
- Invalid arguments are rejected before side effects.
- Generated free-form model text is not passed directly to a privileged executor.

### CAP-003 — Execution result envelope
**Status:** REQUIRED

Executions must return structured success/failure information.

**Acceptance criteria**
- Result distinguishes success, denied, validation failure, execution failure, and timeout/cancellation where applicable.
- Result can be correlated to the originating task/action proposal.

### CAP-004 — External capability adapters
**Status:** PROPOSED

Future integrations may include messaging, calling, media services, smart-home functions, computer control, or other tools.

---

# 7. Events and Tasks

### EVT-001 — Durable important events
**Status:** REQUIRED

Important state transitions/events must be persistable across process crashes.

**Acceptance criteria**
- A persisted event written before a controlled process crash remains available after restart.
- Event has stable ID, timestamp, type, and version.

### TASK-001 — Recoverable task state
**Status:** REQUIRED

Multi-step or long-running work must be represented as recoverable task state.

**Acceptance criteria**
- Task has persistent identity and state.
- Restarting the core does not silently discard a persisted in-progress task.
- Recovery logic can resume, retry, fail, or require review according to explicit state.

### TASK-002 — Idempotent/retry-aware execution
**Status:** REQUIRED

Task/capability execution should not duplicate side effects merely because the process retries after failure.

**Acceptance criteria**
- Execution correlation/idempotency data exists for side-effecting operations where feasible.
- Retry behavior is explicit.

### TASK-003 — Runtime scheduler
**Status:** OPEN

The concrete task engine/scheduler technology and concurrency model are not finalized.

### TASK-004 — Failure semantics
**Status:** OPEN

Exact retry counts, escalation states, dead-letter handling, compensation, and human-review behavior remain to be specified.

---

# 8. Memory

### MEM-001 — Persistent memory
**Status:** REQUIRED

JARVIS must support persistent memory across sessions.

**Acceptance criteria**
- Stored memory survives process restart.
- Memory can be retrieved through a scoped query mechanism rather than only by loading an entire transcript.

### MEM-002 — Provenance
**Status:** REQUIRED

Memory must record where information came from.

**Acceptance criteria**
- Memory item can identify source category and source reference/creator where available.
- Derived/inferred memory is distinguishable from directly supplied information.

### MEM-003 — Scope and access policy
**Status:** REQUIRED

Memory must have access scope and must not be globally visible by default.

**Acceptance criteria**
- Memory can be restricted to owner-only, principal-specific, shared, or another explicit access scope.
- Unauthorized sessions cannot retrieve restricted memory.

### MEM-004 — Consent/permission metadata
**Status:** REQUIRED

Memory must preserve consent or policy-relevant metadata where the information's collection/use depends on permission.

**Acceptance criteria**
- Memory schema can reference consent/policy basis.
- Revoked access is enforced at retrieval/use time even if the underlying record remains retained.

### MEM-005 — Retention/deletion representation
**Status:** REQUIRED

Memory records must support retention/deletion policy metadata.

**Acceptance criteria**
- Schema can represent retention policy or deletion state.
- Deleted/revoked memory is not returned to unauthorized normal retrieval.

### MEM-006 — Temporal validity
**Status:** REQUIRED

Memory/world facts must be able to represent when they were believed valid, not just when they were stored.

**Acceptance criteria**
- Schema can separately represent record time and fact validity/effective time where applicable.

### MEM-007 — Raw transcript startup injection
**Status:** NOT REQUIRED / LEGACY

The old strategy of injecting a large raw conversation history into the model at startup is not a required architecture and should not be used as the primary memory system.

---

# 9. World Model / Observations

### WORLD-001 — Observation vs inference
**Status:** REQUIRED

JARVIS must distinguish direct observations/evidence from inferred beliefs.

**Acceptance criteria**
- Observation records include source and observation time.
- Derived world state identifies supporting evidence/inference.
- Callers can inspect whether a value is observed, inferred, or unknown.

### WORLD-002 — Uncertainty
**Status:** REQUIRED

World state must not represent uncertain inference as unquestioned fact.

**Acceptance criteria**
- Beliefs/observations can store confidence or another uncertainty representation.
- Low-confidence state can be surfaced to decision logic.

### WORLD-003 — Staleness
**Status:** REQUIRED

Time-sensitive world state must be able to become stale.

**Acceptance criteria**
- Observations carry timestamps.
- Consumers can detect stale data according to domain-specific policy.

### WORLD-004 — Sensor adapters
**Status:** PROPOSED

Authorized camera feeds and EDITH-style wearable glasses are planned observation sources.

### WORLD-005 — Sensor/hardware selection
**Status:** OPEN

Specific camera, wearable, streaming, CV, and identity technologies are not finalized.

---

# 10. Autonomy and Initiative

### AUTO-001 — Permission-governed autonomy
**Status:** REQUIRED

JARVIS may act proactively only within applicable permissions and policy.

**Acceptance criteria**
- An autonomous trigger produces an action proposal/task.
- Privileged actions are policy checked exactly as user-requested actions are.
- Initiative does not create a bypass around authorization.

### AUTO-002 — Continuous helpful scanning
**Status:** REQUIRED AS PRODUCT BEHAVIOR; IMPLEMENTATION PHASED

JARVIS is intended to continually evaluate authorized context for events or useful assistance.

**Acceptance criteria for the eventual feature**
- Initiative evaluator consumes only authorized observations/context.
- Suggestions can be produced without executing privileged actions.
- Policy can disable or restrict initiative.

### AUTO-003 — Bounded autonomy
**Status:** REQUIRED

Autonomy must have explicit boundaries and observable state.

**Acceptance criteria**
- Tasks/actions have owner/principal/policy context.
- Autonomous actions can be denied, cancelled, or disabled through policy/configuration.

---

# 11. Social Intelligence

### SOC-001 — Per-person context
**Status:** REQUIRED AT DATA-MODEL LEVEL

JARVIS must be able to associate allowed contextual information with specific people/principals.

**Acceptance criteria**
- Person/principal context has explicit scope.
- One person's private context is not exposed to another without authorization.

### SOC-002 — Relationship-aware communication
**Status:** PROPOSED

JARVIS may adapt tone, assumptions, and initiative based on relationship context, without allowing relationship inference to grant permissions.

### SOC-003 — Relationship model
**Status:** PROPOSED

JARVIS may represent relationships between people and learn from authorized interaction history.

### SOC-004 — New-person baseline/profile
**Status:** PROPOSED

When JARVIS encounters a new person, it may create a minimal baseline/person record.

The exact automatic behavior, consent flow, and identity confidence threshold are open.

### SOC-005 — Public-profile research
**Status:** PROPOSED / OPEN

JARVIS may optionally search public internet sources to enrich information about a person.

This is **not** a confirmed automatic default.

If implemented, it must preserve:
- provenance;
- uncertainty;
- consent/policy;
- sensitive-profile restrictions;
- privacy isolation;
- revocation behavior.

### SOC-006 — Sensitive profiling limits
**Status:** REQUIRED CONSTRAINT

The social-memory subsystem must support policy limits that prevent unrestricted collection/use of sensitive personal profiling data.

**Acceptance criteria**
- Policy can reject a proposed memory/profile write based on data class/source.
- Rejected profile data is not silently stored as ordinary memory.

---

# 12. Interaction and Communication

### INT-001 — Conversational interaction
**Status:** REQUIRED PRODUCT FUNCTION

JARVIS must expose at least one conversational user interface.

Exact modalities are open.

### INT-002 — Voice interaction
**Status:** PROPOSED / LEGACY-ESTABLISHED DIRECTION

Voice input/output has long been part of JARVIS prototypes and remains an intended interface, but the current v0.1 kernel should not depend on a specific STT/TTS stack.

### INT-003 — Wake word
**Status:** LEGACY

Porcupine/Picovoice wake-word detection for "Jarvis" was accepted in an earlier prototype. It is not yet confirmed as the final v0.1 wake-word implementation.

### COM-001 — Urgency-aware notifications
**Status:** REQUIRED AS PRODUCT BEHAVIOR; CAPABILITY DEPENDENT

JARVIS should support different notification channels based on urgency.

Established direction:
- urgent events may trigger a phone call;
- non-urgent events may trigger a text.

**Acceptance criteria for eventual implementation**
- Event/task can carry urgency/severity.
- Notification policy maps urgency to an allowed channel.
- Calling/texting still requires capability availability and permission.

---

# 13. Reliability and Observability

### REL-001 — Observable failures
**Status:** REQUIRED

Failures must be inspectable and correlated to tasks/actions.

**Acceptance criteria**
- Failed task/action produces structured status and error metadata.
- Failure is logged/audited with correlation ID.
- Silent exception swallowing is not accepted for core state transitions.

### REL-002 — Restart recovery
**Status:** REQUIRED

Core persistent state must support recovery after process restart.

**Acceptance criteria**
- Persisted tasks/events/memory remain readable after restart.
- Recovery does not depend on an LLM reconstructing hidden state from text.

### REL-003 — Performance targets
**Status:** PROPOSED

Previously proposed targets include:
- voice acknowledgement p95 under ~800 ms;
- local command response under ~1 s where feasible;
- recovery under ~30 s;
- durable events surviving at least one process crash.

These are not yet ratified as binding v0.1 SLOs.

---

# 14. Security

### SEC-001 — Local-first data handling
**Status:** REQUIRED

Private/user data should be processed and stored locally by default.

**Acceptance criteria**
- Cloud egress is not the default code path.
- Configuration/policy identifies allowed cloud exceptions.
- Cloud adapter cannot bypass policy simply because it is selected by a model.

### SEC-002 — Explicit cloud exceptions
**Status:** REQUIRED

Sending private/local data to cloud services requires an explicit allowed policy/configuration path.

**Acceptance criteria**
- Disallowed data/provider combinations are rejected before transmission.
- Audit metadata records permitted cloud use at an appropriate level.

### SEC-003 — User privacy isolation
**Status:** REQUIRED

One principal must not receive another principal's restricted memory/context without authorization.

**Acceptance criteria**
- Negative integration test demonstrates isolation.
- Retrieval layer applies principal/session scope before returning private records.

### SEC-004 — Auditability
**Status:** REQUIRED

Security-sensitive operations must be auditable.

**Acceptance criteria**
- Permission changes, privileged actions, privileged denials, and policy-relevant cloud egress generate audit records.

---

# 15. Development and Deployment

### DEV-001 — Local-first development
**Status:** REQUIRED

Initial implementation must be runnable on a local development machine.

### DEV-002 — Homelab scale-out
**Status:** REQUIRED DIRECTION

The architecture must permit later deployment across self-hosted homelab infrastructure.

**Acceptance criteria**
- Core interfaces do not assume all components must permanently remain in one process.
- Configuration can externalize addresses/storage/providers without code rewrites.

### DEV-003 — Simulation/fakes
**Status:** REQUIRED

Important external integrations should have test/simulation paths.

**Acceptance criteria**
- At least capability, model, and observation interfaces can use fake implementations.
- Core authorization/task tests do not require physical cameras, phones, or cloud accounts.

---

# 16. Unresolved Requirements Needing Owner Decisions

The following must remain open until explicitly decided:

1. Final database/storage engine(s).
2. Final public API transport and protocol.
3. Final event bus/queue technology.
4. Final task scheduler/runtime.
5. Final authorization/policy model.
6. Final authentication/identity signals.
7. Final local model(s) and optional cloud provider(s).
8. Final STT/TTS/wake-word stack.
9. Final camera and EDITH hardware/integration stack.
10. Exact policy for automatic person baseline creation.
11. Exact policy for public internet research about people.
12. Exact sensitive-data classifications and retention periods.
13. Exact initiative thresholds and human-confirmation rules.
14. Exact notification integrations.
15. Binding performance/SLO targets.

## 17. Foundation Implementation Coverage

Phase 0 and the initial Phase 1 contracts are implemented in `src/jarvis`, with
pytest checks for validation, serialization, explicit version rejection, proposal
and policy separation, metadata registration, structured failures, and logging.
`docs/FOUNDATION.md` maps each affected requirement to implemented/tested scope
and outstanding acceptance criteria. Contract fields alone do not satisfy durable
storage, authorization enforcement, private retrieval, or recovery requirements.
The REQUIRED / PROPOSED / OPEN statuses above remain unchanged.

Phase 2 now exercises in-memory identity, owner-only grant/revoke, fresh policy
decisions, input/output-validated fake capability execution, and linked audits.
`docs/PHASE2.md` maps IAM-001/002/003/004/007, PER-001/002/003, CAP-001/002/003,
and SEC-003/004 to the tested local behaviors and production/persistence gaps.
Private capability-result isolation is tested; full scoped memory retrieval is
still unimplemented. Authentication, persistent authorization, and production
security are not marked complete, and requirement statuses are unchanged.

Phase 3 implements local event/task/audit durability, atomic checkpoints, explicit
state/attempt history, current-authority execution and actual subprocess recovery.
`docs/PHASE3.md` maps EVT-001, TASK-001/002, REL-001/002, ARCH-004, PER-001, IAM-007
and CAP-002/003 to the demonstrated slice and its limits. SQLite and a sequential
runner are authorized provisional choices (ADR-T03); final storage/scheduler,
production authority persistence, arbitrary external exactly-once semantics and
a complete kernel remain unclaimed. Requirement statuses above are unchanged.

The Phase 3 verification pass adds durable local principals/sessions/grants and
revocations, atomic permission/audit commits, authoritative current reads, default
authentication denial and isolated development fixtures. It verifies independent
process task races, actual killed-worker claims, ambiguous outcomes and revoked
resumed work. IAM-001/002/004/007, PER-001/003, SEC-003/004, REL-001/002 and
TASK-001/002 coverage is documented in `docs/PHASE3_VERIFICATION.md`. This is a
reversible local adapter choice (ADR-T04), not production authentication or an
accepted resolution of IAM-005, PER-004 or TASK-003/004.
