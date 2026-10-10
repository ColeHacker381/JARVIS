# Phase 4 — Persistent, permission-scoped memory v0

Implemented against the verified **291-test Phase 3 baseline**. Confirmed
ADR-001–012 remain unchanged. This local milestone addresses MEM-001–006,
SEC-001/003/004, IAM-001/007, SOC-001, PER-001/003, ARCH-004 and REL-001/002.
It uses the existing SQLite journal, durable authority, deterministic policy and
cross-process security claim. Production authentication/provisioning remain OPEN.

## Domain and persistence boundaries

`MemoryService` accepts an opaque `session_token` separately from untrusted
`MemoryDraft`/`MemoryQuery` data. It resolves current identity and exact grants for
EVERY operation under the Phase 3 security lock. A UUID, Session object, subject,
creator, relationship, consent string or model claim never establishes authority.
Missing/corrupt security state and unavailable audits deny protected access.

Domain scope, lifecycle, correction and policy logic live in `memory.py`.
`MemoryStore` is a replaceable storage interface; `storage/memory.py` is its
SQLite implementation. Its methods are private, require a service binding and
server-derived visibility, and expose no public `get`, `all`, metadata-count or
unscoped search API. Application callers use the service. The binding prevents
accidental direct adapter use; Python code with access to private attributes or
the database file is trusted infrastructure, not a sandboxed adversary. Do not
expose SQLite, repository internals or the audit sink as ordinary user endpoints.
Future model/capability adapters must call the same service with trusted runtime
credentials through the existing executor; no privileged provider path changes.

Policy uses the existing DeterministicPolicy with fixed action metadata, no
query/content arguments. Read decisions persist before returning content. Mutation
success audits link to the allow decision, actor/session/proposal/correlation IDs
and opaque memory-scope references (plus recipient ID on access revocation);
row/index changes and successful mutation audit commit together. Errors contain
stable `MemoryError.code` and a serializable `ErrorDetail` in `.detail`; no private
payload appears in logs, audits or exception messages. ID lookup filters SQL scope
before decoding, so corrupt inaccessible records cannot reveal their existence
through different errors. Absent/inaccessible/inactive IDs use `memory_unavailable`.

## Ownership, scopes and current grants

| Operation | Current permission(s) and restrictions |
|---|---|
| Create | `memory.write`; default private ownership is the authenticated actor |
| Create for another owner | Pinned system owner AND `memory.write_others`, in addition to write; creation acknowledges only newly submitted data and grants no later read access |
| Create shared/household | `memory.share` in addition to write; owner/recipients must be known active principals |
| Get/search | `memory.read` AND record scope |
| History or explicit date query | `memory.history` in addition to read; same current scope and retention/deletion rules |
| Household get/search | Explicit stored members AND current `memory.household.read`; owner also needs this grant; fixed `policy:memory.household` marker |
| Correct/supersede/delete/retention | `memory.write` AND exact record owner; expected revision required |
| Revoke a stored recipient | `memory.write` + `memory.share` AND exact record owner; expected revision required |

Owner-private and principal-private records use the same private scope with
explicit ownership. Shared records list exact recipients. Household is a scoped
explicit member list plus a separate owner-administered policy grant, not inferred
role/group membership. The system owner does not automatically read another
principal's private records. A creator may differ from owner; subjects and creators
are not automatically readers. Shared readers cannot edit or re-share a record.
There is no general ACL expansion/ownership transfer endpoint in v0.

Revoking a global read/history/household grant governs the next operation,
including previously stored records and subsequent pages. `revoke_access` removes
one recipient from one record; removing the last recipient makes it private.
Supersession versions are separate records with separately retained scopes;
revoke each relevant version, revoke the global read grant, or delete relevant
versions when policy requires removing historical access too. Current authority
is never reconstructed from stored scope references, source text or old audits.

## Contracts, provenance and temporal behavior

Legacy `Memory` v1 remains readable. `MemoryRecord` v2 extends it explicitly with
flexible named types, trusted creator, scope/owner, project reference, revision,
updated/expiry/deletion/supersession times and bidirectional supersession IDs.
`MemoryDraft` rejects caller-supplied creator/revision/lifecycle authority and
requires an explicit consent/collection-basis reference. Records retain source
category/reference, supporting references, optional model reference, confidence,
subjects and content. Content is JSON, capped at 64 KiB per draft. Initial types
include person/interaction/task/episode/semantic/procedural/observation-derived;
new named types require no schema/table change.

Derived sources require evidence references and remain marked derived. Direct
sources cannot carry derivation/model fields masquerading as testimony. References
are opaque claims: v0 does not resolve or verify the existence/authority of an
external evidence reference, nor automatically dereference private source records.
A missing reference does not become verified evidence or grant any permission.
No model extraction, consolidation, profiling or contradiction inference occurs.

`created_at` is record time, distinct from `valid_from`/`valid_to`. Validity is
half-open `[valid_from, valid_to)`, with absent bounds unbounded. Default current
retrieval uses now and excludes superseded records. Authorized history without an
`at` value includes historical and current versions; an explicit `at` filters
validity and still requires history permission. Current queries do not restore
superseded versions merely by supplying an old date.

`supersede` atomically ends the prior fact's validity, increments its revision,
sets successor/time, and inserts a new ID linked to its predecessor. Original
content/source remain immutable. Correction cannot silently expand scope, change
collection basis or extend retention. Only an active current fact can be corrected;
future cutovers are rejected. Existing unrelated memories are unaffected.

## Search, retention and concurrency

`MemoryQuery` supports literal case-folded substring/keywords over JSON content,
kind, subject, project and effective-date filters. SQL applies access, tombstone,
expiry, temporal and other filters BEFORE recent-first ordering or LIMIT. Ordering
is descending UTC creation time then UUID, with a keyset `MemoryCursor` from the
last visible item. Pages contain 1–100 requested items; backend lookahead is bounded
to limit + 1. `MemorySearchResult` has records and optional next cursor, no global
count/score/ranking information about private records. Each page rechecks current
identity/grants; pagination is not a snapshot across changing records. Literal `%`
and `_` are not search wildcards. No FTS/vector/cloud index or embeddings are used.
Owner/kind/project/reader/subject indexes support selection; text matching scans
permitted candidates and has no production-volume performance guarantee.

Deletion is a logical tombstone with a deletion timestamp, not physical erasure.
Normal and historical queries exclude tombstones; their reader entries and search
text are removed transactionally. Rebuilding ordinary indexes cannot restore them.
Expiry is enforced against NOW even for historical date queries. Expired records
cannot be revived by removing/extending expiry; they may still be tombstoned by
the owner. Explicit `retention_ref` and `expires_at` are configurable per record;
`retention.manual` defaults to no inferred period. Consent/retention references are
metadata, not evidence of consent or authorization. Final collection policies,
retention durations, source invalidation and physical purge remain OPEN.

All local service operations share the verified POSIX security claim with grant
mutation, preventing revocation/read/write gaps. Short `BEGIN IMMEDIATE` mutation
transactions compare expected revisions and validate indexed fields against
versioned JSON. Stale updates fail `revision_conflict`; supersession and all row,
reader/subject index and outcome-audit mutations commit or roll back together.
Records/content/source/ownership are not overwritten in place. This is one local
POSIX host/canonical database, not distributed locking or a throughput/SLO claim.

## Migration and offline demonstration

Storage migration **3** adds `memories`, `memory_readers`, `memory_subjects`, indexes
and tombstone/revision/ownership guards to schema 2. It preserves all existing
Phase 2/3 principals, sessions/token digests, grants/revocations, tasks/history,
events and audits. It does not create identities or import legacy Memory v1 by
inventing missing creator/consent. Contract memory v2 and database schema 3 are
independent versions; missing/unsupported persistent versions fail closed.

Back up existing state first with `jarvis db-backup --destination <new-file>`;
then stop old-version workers and explicitly migrate with `uv run jarvis db-init`.
Older code refuses schema 3; upgrade workers together. See PHASE3.md for backups,
private filesystem paths and safe archival. Never remove live security/task locks.

```sh
uv run --no-sync jarvis memory-demo
```

The command creates a NEW ignored private `memory-demo-<uuid>` directory, preserving
the ordinary database. It runs two actual Python workers, using the real service,
SQLite storage, durable principals and grants. Only its first worker explicitly
bootstraps/grants simulated authority. Tokens pass through private pipes, never
command arguments, persisted fixture files, ordinary output or logs. The reopened
worker independently verifies persistent owner/friend private records, sharing,
guest/cross-user denial, provenance, supersession/current history and tombstones.

Observed summary before and after restart:

```json
{
  "status": "ok",
  "private_retrieval": true,
  "explicit_sharing": true,
  "cross_user_denied": true,
  "guest_denied": true,
  "tombstone_hidden": true,
  "current_fact_count": 1,
  "historical_fact_count": 2,
  "current_fact": {"text": "Slipstream uses microcontroller B"},
  "provenance_preserved": true
}
```

This is a compact view of the real CLI's nested `before_restart`/`after_restart`
checks; stdout also includes its database path and two distinct worker PIDs.

## Files and verification

| Files | Changes / evidence |
|---|---|
| `core/memory_records.py`, `core/contracts.py` | Memory v2/draft/scope/query/cursor/search contracts, explicit decoding and legacy compatibility |
| `memory.py` | Domain service, current policy, private operations, correction, retention, deletion and revocation |
| `storage/memory.py`, `storage/sqlite.py` | Private scoped SQLite adapter, transactional CAS/audits/indexes, migration 3 |
| `memory_demo.py`, `__main__.py` | Real two-process memory demonstration and CLI command |
| `tests/test_memory.py` | 51 domain/storage/security cases, source/validity, private lookup/search, household, forged claims, revocation, audit rollback, deletion/expiry and content-free errors/logs |
| `tests/test_memory_migration.py` | 3 cases: real schema 2 data preservation, legacy v1 compatibility, future schema refusal |
| `tests/test_memory_processes.py`, `tests/helpers/memory_worker.py` | 12 actual-process cases: restart/source/current-history, revocation, forged identity, deletion/expiry, lost authority, deterministic competing updates, real CLI |
| `.github/workflows/ci.yml` | Existing frozen install/lint/format/tests/build and all previous demos, plus memory-demo on Python 3.12/3.13 |
| `MEMORY_SYSTEM.md`, `PERMISSIONS.md`, `ARCHITECTURE.md`, `REQUIREMENTS.md`, `ROADMAP.md`, `README.md`, `docs/FOUNDATION.md`, `docs/PHASE3.md`, `DECISIONS.md`, this document | Coverage, operations, boundaries, provisional choices and next scope |

The original 291 tests passed before edits. Phase 4 adds 66 automated cases,
including 12 real-process cases, for **357 tests passing** on Python 3.12.14.
Frozen install, Ruff lint/format, source/wheel build, whitespace and all four demos
passed. A separately installed wheel outside the checkout passed all four demos,
repeat schema 3 initialization, inspection and backup. Hosted Python 3.12/3.13 CI
results are attached to the PR. Process races use pipe barriers, not sleep-based
exclusion assertions. No external AI, device or network service is required.

## Limits and recommended Phase 5

Production exposure still requires trusted authentication, owner provisioning,
credential lifecycle, collection/consent and retention policy decisions. Fixture
identity methods must not become public authentication endpoints. The private
SQLite database/backups retain tombstoned content, are not encrypted, tamper-proof
or anti-rollback protected, and must be protected as local private data. A privileged
filesystem owner can bypass SQL/application guards; restoring old backups can
roll back deletions/revocations. No automatic restoration or re-grant exists.
Physical erasure, derived-evidence invalidation cascades, sophisticated search,
source authenticity, group policy engines and distributed database adapters remain
future work. ADR-T05 records only reversible local implementation choices.

Recommended Phase 5: a fake-first cognitive coordinator/context builder that calls
this scoped service, preserves evidence/uncertainty and produces validated action
proposals through the existing executor. Bound context size; test prompt injection,
revocation and cross-user context isolation. Design explicit cloud-egress checks
before any optional model can receive protected context. Do not start automatic
profiling/consolidation, embeddings, cameras, voice or autonomous initiative here.
