# User and operator subsystem manual

API examples use the private curl configuration created by the
[quickstart](../getting-started/quickstart.md). Replace `<UUID>` and `<revision>`
with values from an actual response; angle-bracket placeholders are not commands
to copy unchanged. See [API reference](../reference/api-reference.md) for exact
routes and schemas. Credentials resolve current principals; role labels, source
IDs, natural-language authorization claims and conversation IDs are never credentials.

## Identity and permissions

`runtime-init` explicitly creates development owner, friend and guest fixtures.
The owner is pinned in authoritative security state and administers exact grants;
it has no implicit capability/read bypass. Friend lighting is initially permitted;
guest lighting is not. Memory/world/inbox reads still require current scope and
source authority in addition to the relevant permission.

Trusted host administration uses an actual owner client credential:

```sh
uv run jarvis permissions revoke --config .jarvis/dev/runtime.json --credential-file .jarvis/dev/credentials/owner.json --principal e3c4b46a-f7fb-43e6-a1be-000000000005 --permission home.lighting.set_brightness
uv run jarvis permissions grant --config .jarvis/dev/runtime.json --credential-file .jarvis/dev/credentials/owner.json --principal e3c4b46a-f7fb-43e6-a1be-000000000005 --permission home.lighting.set_brightness
```

That UUID is the explicitly bootstrapped development friend, not a production
identity enrollment rule. Other principals can be provisioned only by trusted host
code until production enrollment is designed. Both grant/revoke and denials are
audited; current authority is rechecked on invocation and recovery. An already
started synchronous invocation finishes before a waiting revocation commits.
Do not infer a cancelled physical effect from revocation or process termination.

Provision/rotate a client credential into a **new file**, then export a new curl
configuration. The old one remains valid until explicitly revoked or expired:

```sh
uv run jarvis credentials issue --config .jarvis/dev/runtime.json --identity friend --output .jarvis/dev/credentials/friend-next.json
uv run jarvis credentials revoke --config .jarvis/dev/runtime.json --credential-id <credential-UUID>
```

There is no HTTP credential issuer or owner-selection header. Legacy
`jarvis chat/initiatives/notifications --identity ...` commands are explicitly
trusted development-host fixtures, not HTTP authentication. Access to the OS
user/data directory is outside this development API boundary.

## Conversations and cognition

POST `/api/v1/conversations` with `{"schema_version":1}` creates an owned persistent
conversation. POST its `/messages` route with text and a fresh UUID `request_id`.
One bounded model call produces a direct/grounded reply, clarification, unsupported
operation, validated action or failure. Models cannot change trusted identity,
grant permissions or supply an approval. Fake mode is deterministic, local mode
uses the configured OpenAI-compatible adapter; both report provenance.

The message request UUID is durable, owner/conversation scoped idempotency. Exact
replay uses the original outcome without executing again; changed input conflicts.
For an action inspect `task_id`, `task_state`, `execution.status` and `error`.
`pending` or a task reference means work is accepted/started, not completed.
NEEDS_REVIEW means the outcome is unknown, not success. A structured cognitive
failure can appear in an HTTP-200 response: HTTP receipt is not tool success.

GET `/api/v1/conversations/<UUID>/history?limit=20` returns recent owned permitted
turns. Current memory/world/initiative/notification dependencies are revalidated;
revoked, expired or changed source context cannot return through an old answer.
Known credential strings and recognized secret patterns are rejected before
conversation storage. Detection is not a universal secret classifier: do not paste
secrets. The service does not persist unrestricted internal reasoning traces.

For the legacy local trusted-host text interaction:

```sh
uv run jarvis chat --model-provider fake
uv run jarvis chat --model-provider local --local-endpoint http://127.0.0.1:11434/v1 --local-model qwen2.5:3b
```

These default to the separate `.jarvis/chat/journal.sqlite3` fixture store;
specify `--database` explicitly to inspect the runtime store. They are not API
clients and use development fixture issuance. Use API credentials for client tests.

## Memory

POST `/api/v1/memory/query` with v1 text/project/kind/limit/cursor filters retrieves
current authorized records; GET `/api/v1/memory/<UUID>` retrieves one permitted
record. Private, explicitly shared and household scopes are distinct. Owning a
conversation, being system owner, or knowing a memory UUID grants no extra read
authority. Household access needs explicit membership and its separate grant.

Memory records retain source, creator, evidence/consent references, effective time,
confidence and retention metadata. Corrections use `MemoryService.supersede` with
an expected revision and link immutable historical facts. Tombstones/expiry hide
ordinary retrieval; historical SDK reads additionally require current history
authority. Tombstones do not physically erase retained database/backup bytes.
No automatic extraction, consolidation, embeddings or social profiling is added.

The API currently provides query/get, not write/share/delete/history endpoints.
Trusted host programs use actual `MemoryService.create/supersede/delete/revoke_access/
set_retention` methods; [extensions](../development/extensions.md) demonstrates
composition and credentials. Do not write protected memory tables from a client.

## World model

GET `/api/v1/world?entity=garage&limit=10` and `/world/<state-UUID>` return scoped
current projections and authorized evidence references. A value has observed,
inferred, unknown, conflicting, stale or unavailable status with confidence and
TTL. Camera presence may support inferred occupancy; it is not recognized identity
or proof that a room is empty. Conflicting evidence remains explicit.

POST `/api/v1/world/observations` wraps an explicit v1 `ObservationInput`; nested
payload versions are required. A current enrolled source pins its principal,
route/type/entity/location/scope, and ingestion requires `world.ingest`. The API
does not enroll a source or accept model/device identity claims as authority.
Immutable observation/event IDs make identical ingestion replay safe; conflicting
duplicates are rejected. All adapters are simulated in this release.

Queries and bounded maintenance reevaluate freshness. No global physical purge
or broad sensor collection is performed. A stale state can block an initiative
even though its original observation remains in durable storage.

## Initiatives

GET `/api/v1/initiatives` and `/initiatives/<UUID>` show the caller's permitted
proposals. Policies default OBSERVE_ONLY. APPROVAL_REQUIRED waits for explicit
trusted approval; AUTO_EXECUTE additionally needs narrow current automatic grants,
the trusted LOW-risk simulated allowlist and exact rule/argument limits.
The runtime never enables policies or restores revoked grants on restart.

POST `/initiatives/<UUID>/approve` or `/deny` with v1 and the actual
`expected_revision`. Approval binds the immutable proposal, evidence, exact grant
IDs and principal/session; approval invokes only through existing task guards and
the executor. A revoked/regranted authority cannot resurrect old approval.
The approval response reports the actual state; it can fail/expire/be denied.

Host policy inspection and pause retain the existing CLI:

```sh
uv run jarvis initiatives policies --database .jarvis/dev/runtime/journal.sqlite3 --identity owner
uv run jarvis initiatives pause runtime.garage --expected-revision <policy-revision> --database .jarvis/dev/runtime/journal.sqlite3 --identity owner
```

Configuration uses `initiatives configure --policy-file <serialized-policy.json>`
and an explicit database. Do not turn uncertainty or model urgency into authority.

## Notifications

GET `/api/v1/notifications?limit=20&after=0` returns authorized records and a
recipient-local cursor. Current source access is checked again; hidden records can
yield short pages, so retain the cursor rather than assuming a full page/count.
GET `/notifications/<UUID>` or its `/deliveries` route inspects current source-linked
state. POST `/read`, `/acknowledge` or `/dismiss` with an expected revision.
Acknowledgement **never** approves an initiative: use its separate approval route.

Local inbox admission is the authoritative durable receipt. Simulated DELIVERED
means a synthetic provider result, not human receipt/read. RETRY_WAIT may mean a
known transient failure, current rate limit or quiet-hours deferral; inspect due
time/reason. FAILED and NEEDS_REVIEW are different: the latter prohibits blind
non-idempotent resend. Completed effects remain completed after restart.

Host CLI supports preferences, policy configuration and additional explicit
operations; the runtime schedules finite bridge/process/recover passes:

```sh
uv run jarvis notifications preferences --database .jarvis/dev/runtime/journal.sqlite3 --identity owner --json
uv run jarvis notifications set-preferences --policy-file <preferences.json> --expected-revision <revision> --database .jarvis/dev/runtime/journal.sqlite3 --identity owner
uv run jarvis notifications recover --database .jarvis/dev/runtime/journal.sqlite3 --identity owner
```

Preferences include categories, available channels, minimum priority, cooldown and
IANA-zone quiet hours, including midnight and DST. Only explicit trusted URGENT
policy can override quiet hours. URGENT never grants action privileges.

## Tasks

GET `/api/v1/tasks?limit=20` lists owned safe summaries, ordered by task UUID;
pass the last UUID as `after`. GET `/tasks/<UUID>` returns states and result status/
error codes, not arbitrary arguments/provider output. Conversation responses carry
actual task links; owner-only runtime `/status` includes a bounded owned backlog
sample, clearly marked when it may be truncated.

TaskManager persists intent, attempts and results; current authorization and
immutable domain guards apply on every invocation. Recovery preserves completed
steps, retries only unchanged pure/idempotent replay profiles, and puts ambiguous
non-idempotent work in NEEDS_REVIEW. Cancellation is cooperative, not rollback.
No force-resume or general manual reconciliation API exists. Review provider/source
evidence and retain the task; do not change its SQL state to force execution.

Host `jarvis db-inspect --database <database>` shows durable task metadata.
[Runtime operations](../operations/runtime.md) explain background processing and
safe diagnostics; [backup/restore](../operations/backup-restore.md) explains
storage protection.
