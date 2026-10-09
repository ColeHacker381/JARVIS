# Phase 2: trusted identity, policy, and execution

## Implemented behavior

An owner, users, guests, and unknown sessions can coexist in server-owned memory.
The explicitly named testing APIs bootstrap one owner, create development
principals, and issue unpredictable bearer tokens. Proposal session IDs, UUIDs,
role labels, relationship references, and model claims establish no authority.
The executor resolves a token through the server's identity service and binds
the resolved session to the proposal. Disabled principals, unknown tokens,
anonymous sessions, expired/closed sessions, and session mismatches are denied.

Only that singleton owner may administer grants. Grants are exact
`(principal_id, permission)` pairs with stable UUID references and structured
target/permission/grantor metadata. There are no wildcard permissions, role
inheritance, social relationship grants, or implicit owner capability privileges.
Both safe echo and simulated lighting require explicit permissions. Successful
and denied administration attempts are audited. Revocation removes the current
grant while retaining its referenced metadata for the in-memory audit history.

The registry binds providers through trusted application wiring, never dynamic
imports or model-supplied functions. Metadata-only registration remains available
for the Phase 1 smoke check. A callable registration requires explicit permission
declarations. The registry returns copies of metadata and has no invocation API.

The sole request-facing invocation path is:

```text
ActionProposal + separate session token
  -> server session lookup and binding
  -> registered capability and private/shared scope lookup
  -> JSON Schema input validation
  -> current-grant policy decision + policy audit
  -> permitted provider invocation
  -> JSON/object and output schema validation
  -> structured ExecutionResult + outcome audit
```

No cached decision or caller-supplied principal/authorization context is accepted
by `execute`. Policy uses only current server state; language models play no part.
An allow decision must bind to the actual principal/session, proposal, and
correlation ID. Policy/identity/registry adapter failures produce sanitized
failure results before a provider can run. Identity, policy, grant lookup, and
registry protocols keep those adapters replaceable for future persistence.
All declared permissions must be present. A registered `AccessScope` additionally
restricts a private result to its owner or explicit shared recipients; possessing
the action permission alone does not permit access to another user's context.
Tests exercise this through a fake private-context provider before its handler
can run. This does not implement a memory storage/retrieval subsystem.

JSON Schema Draft 2020-12 validation permits document-local references only.
Remote references, other dialect declarations, and `$id` base relocation are
rejected on registration. Reference/schema resolution failures fail input
validation before invocation. No external schema fetching is required. Inputs
and metadata are copied; a provider cannot mutate the caller's proposal payload.
Invalid outputs and exception messages never become result payloads or audit data.

## Offline usage

From the repository, run `uv run --no-sync jarvis demo`. Each run creates fresh
in-memory state and uses actual grants, policy decisions, and provider invocations.
The expected results are owner access and guest echo success, guest lighting and
pre-grant friend denial, friend success after a grant, then friend denial after
revocation. The provider's actual lighting call history must be `[25, 50]` or the
demo fails. Logs go to stderr; results and sanitized audits go to stdout.

For local tests/application wiring, the same sequence can be built explicitly:

```python
from uuid import uuid4
from jarvis.authorization import DeterministicPolicy
from jarvis.capabilities import InMemoryCapabilityRegistry
from jarvis.development import (
    BRIGHTNESS_PERMISSION,
    proposal_for,
    register_development_capabilities,
)
from jarvis.execution import CapabilityExecutor
from jarvis.identity import InMemoryIdentityService
from jarvis.observability import InMemoryAuditSink
from jarvis.permissions import InMemoryPermissionStore

audit = InMemoryAuditSink()
identities = InMemoryIdentityService()
owner = identities.bootstrap_owner_for_testing()
credentials = identities.establish_session_for_testing(owner.principal_id)
permissions = InMemoryPermissionStore(identities, audit)
registry = InMemoryCapabilityRegistry()
register_development_capabilities(registry)
policy = DeterministicPolicy(identities, permissions, audit)
executor = CapabilityExecutor(identities, registry, policy, audit)
permissions.grant(
    credentials.token, owner.principal_id, BRIGHTNESS_PERMISSION, correlation_id=uuid4()
)
proposal = proposal_for(credentials, "home.lighting.set_brightness", {"brightness": 30})
result = executor.execute(proposal, session_token=credentials.token)
assert result.status == "success"
```

These token-bearing credentials belong to trusted fixture code and must not be
printed, embedded in proposals, committed, or forwarded to a model.

## Audit and failure semantics

Policy audits bind actor/session, proposal/task/correlation, decision ID, reason,
and grant references. Outcome audits carry the same decision ID. Permission
changes reference structured `PermissionGrant` metadata, available through
owner-only inspection; audit records contain no raw input/output arguments or
exception messages. Audit IDs/operation codes are not proof of authority.

Policy auditing must succeed before invocation. Administration auditing must
succeed before mutating grants. An unavailable audit sink therefore prevents
privileged invocation or grant/revoke mutation. Audit write failures are logged
with a sanitized code; execution returns `audit_unavailable` rather than silently
continuing. If the *outcome* audit fails after invocation, the side effect may
already exist. The result reports execution failure with retries disabled; no
rollback, exactly-once execution, or durable audit guarantee is claimed.

Provider exceptions produce a correlated execution failure. Providers that raise
`TimeoutError` or `concurrent.futures.CancelledError` produce timeout/cancelled
envelopes. There is no timer, cancellation controller, background worker, or
scheduler here. Invalid proposal contracts are rejected before pipeline entry;
valid proposals with malformed capability arguments get `validation_failure`.

Identity changes, grant changes, and synchronous authorization/invocation share
a process-local reentrant lock. Thus a concurrent revocation cannot commit in
the gap between checking grants and invoking a provider. A revocation waiting
behind already-started execution does not cancel that execution; subsequent
invocations see the revoked grant. Slow providers block this development kernel.
Durable transactions, distributed workers, and production concurrency semantics
remain separate decisions for Phase 3 and beyond.

## Requirement coverage and limits

| Requirement | Tested Phase 2 scope | Still unfinished |
|---|---|---|
| IAM-001 | Distinct server-owned principals/sessions and permissions | Persistent identity, production authentication |
| IAM-002 | Singleton owner grant/revoke and audit | Persistent grants, production administration interface |
| IAM-003 | Explicitly permitted guest echo, denied lighting | Production guest authentication |
| IAM-004 | Forged IDs/sessions/proposals/model claims cannot authorize invocation | Authentication transport and token lifecycle hardening |
| IAM-007 | Revocation denies subsequent execution, including old proposals | Durable/distributed revocation |
| PER-001/002 | Fresh deterministic decisions and all declared permission checks | Final policy syntax and production enforcement topology |
| PER-003 | Linked allow/deny policy and outcome audits | Durable, tamper-resistant audit sink |
| CAP-001/002/003 | Trusted provider registration, validated input/output, correlated structured outcomes | Real providers, actual deadline/cancellation enforcement |
| SEC-003 | Private/shared capability-result isolation before provider invocation | Full scoped memory retrieval and storage |
| SEC-004 | Grant/revoke, privilege allow/deny and outcomes audited | Durability and future cloud-egress audit paths |

Authentication, persistent authorization, and production security are explicitly
not complete. Unknown identities receive no capability privileges. Bootstrap,
registry, providers, policy, stores, and audit adapters are trusted Python code;
this boundary protects untrusted requests/model output, not arbitrary Python
code running in the same process. No networking endpoint exposes those objects.
All state and session tokens disappear on restart. There is no real hardware,
model integration, cloud call, initiative engine, scheduler, or memory backend.
Wire contracts stay at v1; the Python policy protocol now accepts the token
separately rather than treating a caller-supplied `Session` as authority.

## Phase 3 extension

The Phase 2 demo remains an in-memory workflow. Phase 3 now supplies optional
SQLite event/task/audit adapters and a recoverable sequential task runner through
the same executor. Identity/session/grant state still requires trusted runtime
re-establishment; saved task approvals cannot restore permissions. See
[PHASE3.md](PHASE3.md) and ADR-T03 for the current implementation and limits.
The validation below records the original Phase 2 baseline.

## Validation performed

Python 3.12.14 executed all **180 pytest tests successfully**, including the
original 116 Phase 0/1 tests and 64 Phase 2 cases. Ruff lint/format checks, frozen
installation, source/wheel builds, and whitespace checks passed. Smoke and the
actual Phase 2 demo passed from a separate installed wheel outside the checkout;
the code example above also executed successfully. Hosted CI and the Python 3.13
matrix are configured, but were not verified locally.
