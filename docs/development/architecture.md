# Integrated architecture and trust boundaries

The v0.9 runtime composes existing Phase 2–8 services; it does not introduce a
second executor, authorization engine, memory store or recovery system.
`application.build_application` is the shared composition root. Legacy
`cognitive_runtime.development_runtime` delegates to it; runtime/API tests and
the server use it with a credential-bound development identity repository.
Domain services depend on protocols and typed contracts, not FastAPI.

## Components

```mermaid
flowchart TD
  CLI[Trusted host CLI] --> Root[Application composition root]
  HTTP[Loopback HTTP] --> API[Transport validation]
  API --> Root
  Root --> Cognition[CognitiveCoordinator]
  Root --> World[WorldModel]
  Root --> Initiatives[InitiativeEngine]
  Root --> Notifications[NotificationService]
  Root --> Scheduler[Finite serial scheduler]
  Cognition --> Memory[MemoryService]
  Cognition --> Models[ModelRouter and replaceable providers]
  Cognition --> Tasks[TaskManager]
  Initiatives --> Tasks
  Notifications --> Tasks
  Tasks --> Executor[CapabilityExecutor]
  Executor --> Policy[Current independent policy]
  Executor --> Adapters[Local simulated adapters]
  Root --> SQL[SQLite ports: authority, journal, tasks, memory, world, conversation]
```

The same identities, policy, registry, task guards, audit writer and database
instances are shared. A runtime claim permits one server per database on a
single POSIX host; task/domain claims protect individual operations. SQL
transactions remain short. Provider invocation occurs outside SQL transactions
but under the established current-authority boundary. Never hold an outer
identity lock before acquiring a domain claim; domain services own lock order.

## Authenticated request

```mermaid
sequenceDiagram
  participant Client
  participant Boundary as Local HTTP boundary
  participant Identity as Current persisted authority
  participant Domain as Core service
  Client->>Boundary: Bearer token, explicit v1 JSON
  Boundary->>Boundary: Loopback/Host/Origin/body/admission checks
  Boundary->>Identity: Verify hash, client kind, active principal/session/expiry
  Identity-->>Boundary: Trusted session
  Boundary->>Domain: Owned bounded operation with original credential
  Domain->>Identity: Revalidate current scope/grants before use
  Domain-->>Client: Versioned observed result or sanitized error
```

No HTTP endpoint creates authority or accepts a role, principal or owner session
claim. Host provisioning is an explicit development operation. It stores only
credential hashes in SQLite and writes tokens to new private files. UUIDs are
resource references, never authentication. Browser-origin requests are denied.

## Cognition and action

```mermaid
flowchart LR
  Request[Authenticated request] --> Context[Bounded authorized current context]
  Context --> Provider[Fake or loopback local model]
  Provider --> Decision[Strict CognitiveDecision validation]
  Decision --> Reply[Direct or verified grounded reply]
  Decision --> Proposal[Untrusted ActionProposal]
  Proposal --> Task[Durable TaskManager submission]
  Task --> Guard[Current policy and immutable task guards]
  Guard --> Handler[Validated capability provider]
  Handler --> Result[Validated ExecutionResult and audit]
  Result --> Reply
```

One bounded decision/action is allowed per request. Model text never invokes a
handler or changes permissions. A proposal is not proof of successful execution;
responses use actual task/result states. Current source references are rechecked
before persisted replies/history enter another model context.

## Observation to notification

```mermaid
flowchart TD
  Source[Trusted enrolled simulated source] --> Ingest[Authenticated ObservationInput]
  Ingest --> Commit[Atomic observation, world projection, event, audit]
  Commit --> Rule[Finite scoped initiative pass]
  Rule --> Intent[Durable intent and receipt/cursor]
  Intent --> Approval[Exact approval or explicitly granted narrow auto policy]
  Approval --> Tasks[Existing guarded tasks]
  Intent --> Bridge[Owned history notification bridge]
  Tasks --> Bridge
  Bridge --> Inbox[Atomic authoritative local inbox]
  Inbox --> Delivery[Guarded rate/quiet-aware simulated delivery]
  Inbox --> Interaction[Read, acknowledge or dismiss]
  Interaction -. separate explicit operation .-> Approval
```

Acknowledgement never approves an initiative. Original evidence/intent remains
distinct from current state. Quiet hours and current source/recipient authority
apply to delivery and recovery. Ordinary task recovery skips domain-bound tasks;
initiative/notification recovery owns their prerequisite checks/finalization.

## Recovery

```mermaid
flowchart TD
  Start[Open existing store and validate authority/schema] --> Scan[Bounded owned unfinished work]
  Scan --> Known{Recorded provider outcome?}
  Known -->|yes| Finalize[Finalize without repeating completed effect]
  Known -->|no| Safe{Trusted retry/idempotency semantics?}
  Safe -->|yes| Current[Recheck current authority and guards]
  Current --> Retry[Safe existing task recovery]
  Safe -->|no| Review[NEEDS_REVIEW; no blind resend]
  Start --> Ready[READY or explicit DEGRADED]
```

SIGINT/SIGTERM immediately stops admission and signals the scheduler. Active
request/scheduler threads remain tracked despite caller cancellation. Drain uses
one deadline; timeout/second interruption exits the process with status 2 and
preserves ambiguous RUNNING attempts. No external exactly-once guarantee exists.

## Privacy

```mermaid
flowchart LR
  Owner[Owner credential] --> Scoped[Service-level current authorization]
  Guest[Guest credential] --> Scoped
  Scoped --> Own[Exact owned conversation/task/inbox]
  Scoped --> Shared[Explicit memory/world resource scope]
  Private[Other user's private data] --> Deny[Deny before decoding/returning/context]
  Scoped --> Deny
  Own --> Bounded[Bounded context with provenance]
  Shared --> Bounded
```

Owner administration does not confer a bypass to another user's private data.
Foreign/missing resources have the same unavailable response. Public health has
booleans/lifecycle only; detailed operational metrics require the pinned owner.
Audit and raw storage are trusted operator internals, not public data endpoints.

## Model egress

```mermaid
flowchart LR
  Context[Authorized local context] --> Router[Provider selection]
  Router --> Local[Loopback adapter with timeout and bounded response]
  Router --> Fake[Explicit deterministic development provider]
  Router --> Gate[Cloud disabled by default]
  Gate --> Egress[Explicit trusted destination and all data-class grants]
  Egress --> Future[Future cloud adapter boundary]
  Gate --> Denied[No transmission or automatic fallback]
```

Local endpoint validation and no-proxy/no-redirect HTTP prevent ordinary model
selection becoming remote transmission authorization. Memory/world/initiative/
notification-derived history retains its data-class requirements. Retrieved
content is data, never system instructions or authority.

Migration 8 adds revocable credential bindings and preserves all prior security
and domain state. SQLite, POSIX claims, one serial scheduler, local development
authentication and HTTP v1 remain **provisional** (ADR-T10). Production auth,
distributed claims, exact public API, calibrated risk, retention/reconciliation
and a homelab deployment remain OPEN. See [decisions](../../DECISIONS.md).

For a future homelab deployment, keep the core authoritative on a private host,
replace development credential provisioning with a selected trusted authenticator,
enroll narrow adapters and decide collection/retention first. Move storage through
its ports with versioned migration/backup, not by changing model ownership or
bypassing policy. Distributed claims, remote inference, TLS/remote client access
and real notification destinations require explicit decisions and acceptance;
loopback bind restrictions are intentional in this release.
