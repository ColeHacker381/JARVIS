# Local API v1 reference

The authoritative schema is [generated OpenAPI](openapi.json), regenerated from
actual routes by `python -m jarvis.documentation --write`. A running instance
serves it at **authenticated** `GET /api/v1/openapi.json`; Swagger/Redoc/public
OpenAPI are disabled. This is a provisional development surface, not a public API.
Listen only on 127.0.0.1 or ::1. Clients use HTTP bearer credentials bound to current
persisted sessions. Worker credentials are rejected by HTTP. No cookie auth,
role-selection headers, browser-origin calls or forwarded-header trust exists.

All resource identifiers below are UUIDs unless noted. Incoming JSON has explicit
integer `schema_version: 1`; nested observation/cursor contracts need their own
explicit supported versions. Unknown fields, coerced versions and malformed
arguments fail before action. Success has `schema_version`, server `request_id`
and `data`; errors have `schema_version`, `request_id` and a versioned
`error: {schema_version, code, message, retryable}` as defined by ErrorDetail.
`X-Request-ID` matches the server
envelope; a message's separate UUID request_id is its persistent replay key.
Responses use `Cache-Control: no-store`.

Public health returns a typed v1 Health object directly. Authenticated OpenAPI
returns the standard OpenAPI document directly. Model/domain cognitive failures
can be HTTP 200 with `data.kind: failure` and a structured error; inspect the
domain result rather than treating HTTP 200 as an executed action.

| Method | Route | Request / response / scope |
| --- | --- | --- |
| GET | /health/live | Non-sensitive lifecycle/booleans; 200 while process live |
| GET | /health/ready | 200 READY/DEGRADED with compatible core and live scheduler, else 503 |
| GET | /health/status | Same readiness summary; no private counts/paths/content |
| POST | /api/v1/conversations | RequestV1 → owned Conversation |
| POST | /api/v1/conversations/{conversation_id}/messages | MessageRequest → CognitiveResponse; one bounded decision/action |
| GET | /api/v1/conversations/{conversation_id}/history | Owned permitted recent HistoryEntry list; limit 1–100, default 20 |
| GET | /api/v1/tasks | Owned safe TaskSummary list; limit 1–100, after UUID |
| GET | /api/v1/tasks/{task_id} | Owned safe summary; no arguments/private provider outputs |
| POST | /api/v1/memory/query | MemorySearchRequest → current authorized MemorySearchResult with cursor |
| GET | /api/v1/memory/{memory_id} | Current authorized MemoryRecord; scope/grants/validity rechecked |
| GET | /api/v1/world | Current WorldState list; entity/location/attr ≤96 chars, limit 1–20 |
| GET | /api/v1/world/{state_id} | Current scoped state/evidence references, uncertainty/conflict/staleness |
| POST | /api/v1/world/observations | ObservationRequest → record/replayed; enrolled source + world.ingest only |
| GET | /api/v1/initiatives | Owned bounded InitiativeRecord list; limit 1–100 |
| GET | /api/v1/initiatives/{initiative_id} | Current owned record/evidence/task references |
| POST | /api/v1/initiatives/{initiative_id}/approve | RevisionRequest; existing exact current approval then guarded execution |
| POST | /api/v1/initiatives/{initiative_id}/deny | RevisionRequest; existing owned denial service |
| GET | /api/v1/notifications | Owned/source-authorized NotificationPage; limit 1–100, after recipient-local int, text ≤100 chars |
| GET | /api/v1/notifications/{notification_id} | Current recipient/source-authorized Notification |
| GET | /api/v1/notifications/{notification_id}/deliveries | Current recipient-scoped NotificationDelivery list |
| POST | /api/v1/notifications/{notification_id}/read | RevisionRequest → actual inbox state |
| POST | /api/v1/notifications/{notification_id}/acknowledge | RevisionRequest; acknowledgement never approves initiative |
| POST | /api/v1/notifications/{notification_id}/dismiss | RevisionRequest → actual inbox state |
| GET | /api/v1/status | Pinned owner/current credential: typed operational metrics and bounded owned backlog sample |
| GET | /api/v1/openapi.json | Client-authenticated generated schema, no private runtime data |

Lists are bounded, not snapshots or an invitation to dump an entire store. Task
pages use the last returned UUID as `after`, ascending UUID order. Notification
pages use `data.next_cursor` as `after`, recipient-local rather than a global
SQLite sequence. Memory pages use the returned typed cursor with every original
query filter and explicit cursor version. History, world and initiative reads
are bounded windows without pagination cursors; omitted/inaccessible items are
not revealed as private totals. Backlog metrics explicitly mark sample truncation.

## Examples

Create private client/curl files through the [quickstart](../getting-started/quickstart.md).
Bearer tokens must not appear in command arguments, terminal output or Git. The
curl config supplies the authorization header; `$conversation`/`$initiative` below
contain non-secret resource UUIDs from earlier responses.

```sh
curl --fail-with-body --config .jarvis/dev/credentials/owner.curl \
  -H 'Content-Type: application/json' -d '{"schema_version":1}' \
  http://127.0.0.1:8765/api/v1/conversations
curl --fail-with-body --config .jarvis/dev/credentials/owner.curl \
  -H 'Content-Type: application/json' \
  -d '{"schema_version":1,"request_id":"e7c40690-6405-4e84-a6a0-e1f0a697677f","text":"What is Slipstream using?"}' \
  "http://127.0.0.1:8765/api/v1/conversations/$conversation/messages"
curl --fail-with-body --config .jarvis/dev/credentials/owner.curl \
  -H 'Content-Type: application/json' -d '{"schema_version":1,"text":"Slipstream","limit":5}' \
  http://127.0.0.1:8765/api/v1/memory/query
curl --fail-with-body --config .jarvis/dev/credentials/owner.curl \
  -o .jarvis/dev/openapi.json http://127.0.0.1:8765/api/v1/openapi.json
```

For a pending exact initiative, first GET the current record and copy its revision:

```json
{"schema_version":1,"expected_revision":0}
```

POST that body to its `/approve` route. A revision conflict is 409; refetch and
review current intent rather than retrying a stale approval. Current evidence,
policy/grants, target, intent hash and expiry remain decisive. API approval attempts
execution only after actual APPROVED state. Observe-only/blocked/stale records
cannot be made permitted by sending an owner name or forged decision.

Message replay with the same UUID and unchanged text returns the persisted
current-authorized result without repeating a completed effect. Reusing that key
with different text is 409. Identical ObservationInput observation_id/content
replays without another accepted event; changed content conflicts. Inbox mutation
uses optimistic expected revisions; a repeated stale mutation conflicts. There
is no generalized force-retry or reconcile endpoint for ambiguous external work.

## Failures and validation

| HTTP | Meaning / safe response |
| --- | --- |
| 401 | Missing/invalid/expired/revoked/wrong-kind credential; provision a new explicit file |
| 403 | Current policy denial, forbidden identity claim/browser Origin/Host; do not bypass grants |
| 404 | Foreign/missing/inaccessible resource uses the same unavailable code |
| 409 | Replay payload, expected revision, transition or busy conflict; review/refetch |
| 408 / 413 | Body timeout / configured byte limit; send a bounded request |
| 422 | UUID/schema/type/unknown field/limit validation failure; no handler invoked |
| 503 | Core not ready, authoritative store failure or bounded admission saturated |
| 500 | Sanitized unexpected operation failure; inspect trusted operator diagnostics |

Auth/resource-specific details are intentionally sanitized. There is no principal/
session/grant issuer, raw audit reader, global history dump, arbitrary command,
model-inferred approval, memory mutation, source enrollment, policy management,
public listener or real notification send route. Use trusted core SDK/host
administration for supported setup and explicit policy decisions. See
[security](../operations/security.md) and the [subsystem manual](../guides/subsystems.md).
