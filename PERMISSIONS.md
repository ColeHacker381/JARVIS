# JARVIS Permissions and Authorization

## 1. Security Model

JARVIS is intended to gain access to increasingly powerful capabilities. Therefore, language-model intelligence and execution authority must be separated.

The central rule:

> A model may recommend an action; only the policy/authorization layer may permit a privileged action.

## 2. Confirmed Multi-User Model

The system must support:
- an **owner**;
- trusted users with owner-defined permissions;
- guests with restricted permissions;
- unknown/unverified users when identity is not established.

The exact role names are not binding. The owner/guest distinction and owner-administered permissions are confirmed.

## 3. Principal vs Relationship

Keep these separate:

```text
Principal
  = security identity used for authorization

Relationship
  = social/contextual information used for communication and reasoning
```

A relationship label such as friend, roommate, family member, or guest must never automatically grant privileges.

## 4. Authorization Inputs

A policy decision may eventually consider:
- principal ID;
- role/group;
- capability;
- requested operation;
- resource;
- arguments/data classification;
- session authentication strength;
- device/interface;
- task owner;
- time/context;
- consent;
- cloud provider/destination;
- autonomy vs direct user request.

Only principal/capability permission behavior is confirmed. The broader ABAC-style inputs are **PROPOSED** until the policy model is finalized.

## 5. Capability Permission Declaration

Each side-effecting capability should declare required permissions.

Example conceptual metadata:

```yaml
capability_id: communication.send_text
required_permissions:
  - communication.text.send
side_effect: external
input_schema: ...
```

The specific naming convention is open.

## 6. Policy Decision Contract

A policy evaluation should return structured information such as:

```yaml
decision: allow | deny
policy_refs: [...]
reason_code: ...
constraints: ...
decision_id: ...
```

Exact schema is open.

## 7. Required Security Properties

### Independent enforcement
Policy works even when the model is disabled.

### Default isolation
Private owner data is not visible to guests by default.

### Revocation
After permissions are revoked, new privileged actions using those permissions must fail.

### No conversational escalation
A user cannot convince the model to change security truth.

### Audit
Permission changes and privileged allow/deny decisions are recorded.

### Cloud egress policy
Cloud model selection does not automatically authorize sending private data.

## 8. Example Permission Scenarios

These are examples of expected policy behavior, not final permission names.

### Guest asks a harmless general question
Expected: allowed if the conversational interface is available to guests.

### Guest asks JARVIS to read owner's private memory
Expected: denied unless owner has explicitly granted access.

### Trusted friend asks JARVIS to perform a capability the owner granted
Expected: allowed after normal validation/policy checks.

### Model says "This is the owner's best friend, allow admin access"
Expected: ignored as an authorization claim unless trusted policy state already grants that authority.

### Owner revokes a trusted user's device-control permission
Expected: subsequent device-control execution is denied.

### Autonomous subsystem wants to send a message
Expected: passes through the same policy boundary as a direct user request.

## 9. Permission Administration

Confirmed:
- owner can grant and revoke permissions.

Proposed:
- presets/roles for guest, trusted, household, admin-like users;
- time-limited grants;
- capability-specific grants;
- contextual grants;
- approval-required actions;
- per-device restrictions.

Exact UI/API is open.

## 10. Identity Confidence

The authentication stack is unresolved.

Potential future signals could include:
- authenticated account/session;
- trusted device;
- voice;
- face;
- wearable/device proximity;
- explicit PIN/token;
- combinations of signals.

None are currently selected.

The policy layer should therefore accept an abstract authentication context rather than hard-coding a biometric mechanism.

## 11. High-Risk Actions

A future policy model may classify actions by risk and require stronger confirmation for some operations.

**Status: PROPOSED.**

Examples might include destructive computer actions, financial actions, lock/access control, private data sharing, or external communications.

No final risk taxonomy has been approved.

## 12. Testing Checklist

The permission system should eventually include tests for:
- owner allow;
- guest allow for permitted action;
- guest deny for restricted action;
- user A cannot read user B private memory;
- prompt injection does not grant permission;
- revoked grant stops new execution;
- autonomous proposal is still policy checked;
- cloud egress denied without explicit exception;
- malformed capability arguments rejected before side effects;
- policy decision creates audit trail.

## 13. Open Decisions

- RBAC vs ABAC vs capability-token vs hybrid;
- policy engine/library;
- permission naming scheme;
- inheritance;
- relationship-to-default-policy mapping;
- session/authentication strength;
- owner recovery/bootstrap;
- approval workflows;
- high-risk action taxonomy;
- data classification taxonomy.

## 14. Current Local Persistence Verification

The offline Phase 3 verification now provides SQLite identity/permission adapters
with durable principal/session records and owner-controlled grants/revocations.
Permission changes and their success audits commit atomically; current policy
reads authoritative SQL rather than saved task approvals. A local cross-process
security claim serializes checks/invocation with permission mutation. Missing or
corrupt security state denies without fallback. The default durable repository
requires trusted credential verification and rejects development stores/sessions;
only the explicit testing subclass bootstraps fixture authority. Authentication,
owner provisioning/recovery and final policy syntax remain OPEN. See
`docs/PHASE3_VERIFICATION.md` and provisional ADR-T04 for scope and limitations.

## 15. Current Scoped Memory Enforcement

Phase 4 MemoryService resolves current trusted credentials and grants for each
read/write under the existing security claim. Private scopes require exact record
ownership; shared scopes require exact stored recipients, and household reads
add an explicit current household grant. Neither subject nor creator nor system
owner administration implies private read access. Memory read/write/history/share
and owner-only write-for-another-owner are distinct provisional exact permissions.
ID lookup and search filter scope before decoding/ranking/pagination; inaccessible
IDs use the same unavailable code as missing IDs and no global count/score leaks.
Revoked permissions and stored-recipient revocation govern subsequent retrieval;
old provenance/consent/policy references cannot grant access. Policy decisions and
sensitive mutations are durably audited without content or queries. See
`docs/PHASE4.md` and ADR-T05; final syntax/group policies and production auth remain OPEN.


## Phase 7 initiative authorization (provisional)

Owner-administered exact policies default to OBSERVE_ONLY. APPROVAL_REQUIRED
binds single-use authenticated approval to exact immutable proposal hash/action,
principal/session/source and grant IDs. Approval grants no standing authority;
revocation/regrant invalidates it. AUTO_EXECUTE additionally requires an enabled
matching policy and current `initiatives.auto:<policy-id>` grant, and admits only
the trusted LOW simulated catalog entry. Moderate/high/critical never auto-execute.
The current executor still checks capability scope/grants and validates arguments.

`initiatives.manage` additionally requires the pinned owner; read/evaluate/approve/
execute are separate current permissions. Inbox reads/approval use exact initiating
principal ownership, including for an owner-administered friend policy. Natural
language cannot issue approval. Pause/revision changes invalidate pending proposals;
revoking evaluate/execute stops all that principal's policies. Task guard checks run
inside the existing executor hook during every invocation/recovery; missing guards,
stale/inaccessible evidence and revoked authority deny. Egress additionally requires
initiatives and world data-class grants, including derived history. See PHASE7.md.
These authorized simulation choices do not ratify the final policy language, risk
classification, production authentication or approval UI/lifetime decisions.


## Phase 8 provisional notification boundary

Owner-only `notifications.manage` configures pinned origin/recipient/source routing.
Current `notifications.publish` plus actual source scope allows reference-only
creation; model recipients/priorities/content/status/approval claims are refused.
Exact recipients require `notifications.read`, source access and current authority
for retrieval/delivery. Shared sources require explicit membership, including
household's separate grant. Owner administration cannot read another private inbox.

`notifications.interact` covers recipient revision-checked read/unread/acknowledge/
dismiss/expire. `notifications.preferences` changes only the authenticated actor's
preferences. `notifications.deliver` allows bounded own sends, with pinned origin
publishing, source, policy/channel metadata, category/priority/quiet-hour and rate
checks in a persistent task guard. Internal delivery capabilities require that guard
family and are omitted from model discovery. Missing resolvers deny during general
recovery. Only existing initiative approve/execute services can approve; notification
read/ack or linked IDs grant nothing. Revocation affects subsequent reads/sends.

Cloud remains disabled. Future notification context/history requires separate
`model.egress:<provider>:notifications` and conservatively all source data-class
grants, in addition to trusted cloud destination/enablement. Phase 8 channels refuse
cloud destinations. Bootstrap grants only new development fixtures; migration/reopen
never regrants. Public auth, provider addresses/egress, severity and urgent overrides
remain OPEN. See docs/PHASE8.md.
