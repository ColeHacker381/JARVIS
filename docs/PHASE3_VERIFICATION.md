# Phase 3 verification — persistence, security and concurrency

Reviewed 2026-10-10 against the existing Phase 3 implementation, preserving confirmed
ADRs and the Phase 2 model-independent policy/executor boundary. This is local
verification and hardening, not a production authentication or scheduler redesign.

## Baseline and original findings

The existing suite passed **256 tests** before edits. Phase 3 already persisted
important events, task checkpoints/history, and policy/execution audits in SQLite.
`identity.py` and `permissions.py` supplied only in-memory security implementations.
The recovery demo recreated identities and explicitly re-granted permissions in
each worker. That was an isolated fixture, not a durable authoritative permission
source; changing grants in one process could not govern another process's memory.
The original limitation was documented in PHASE3.md and ADR-T03.

Task concurrency already used nonblocking POSIX file claims in
`SQLiteTaskStore.claim`, short SQL transactions, revision comparison, immutable
completed checkpoints, and stable task/step keys. Existing tests proved process
lock exclusion, but only some recovery/authority scenarios used independent
processes. Permission mutation and invocation shared only a process-local RLock.
That did not serialize authorization/revocation across independent processes.

## Question one: security and audit persistence

| Question | Verified behavior after hardening | Implementation / tests |
|---|---|---|
| Are grants and revocations durable? | Yes for the SQLite adapter. Exact owner-authorized grant rows persist, with versioned grant data and retained revocation timestamps/history. Grant/revoke success and its audit commit together. | `storage/security.py`: SQLitePermissionStore, GrantRecord; `test_security_processes.py`: grant/revocation restart tests; `test_security_storage.py`: atomic mutation tests |
| What happens to active permissions on restart? | They are read afresh from authoritative SQL rows; no cache, task references or historical allow reconstructs access. The old in-memory adapter still loses its state and is used only by the Phase 2 development demo. | `permissions.py`: shared administration checks/storage hooks; `storage/security.py`: _active_grant |
| Can revoked access become active after restart? | Ordinary restart cannot reactivate the old grant. Revoked rows cannot be reset or deleted through normal SQL updates/triggers. Only a fresh owner-authenticated grant creates a new grant ID. Missing/deleted rows deny, without looking up old task references. | `test_revocation_persists_and_new_process_denies_execution`; `test_recovery_does_not_restore_saved_grants_after_revocation`; revocation/history storage tests |
| Are audits durable and queryable? | Yes. SQLiteAuditSink.records supports correlation filtering. Permission administration and policy allow/deny/outcome records survive restart, with principal/session/proposal/task/decision IDs, reason and policy references. Full PolicyDecision objects remain transient; their decision evidence is durably represented in the audit records. | `storage/sqlite.py`: SQLiteAuditSink; `test_grant_and_audits_survive_independent_restarts` |
| Is security reconstructed from an authoritative source? | Principal/session records, pinned owner/authority metadata and grants come from the security tables in the same configured database. Reopening never bootstraps or re-grants. Migration adds empty tables without deriving authority from old tasks/audits. | `storage/security.py`: SQLiteIdentityRepository; `recovery_demo.py`: one explicit bootstrap; migration test |
| Does recovery revalidate current authority? | Yes. Every eligible step invokes the existing executor/policy against the current identity/permission repository. TaskManager and executor must share the same trusted identity instance. | `task_manager.py`, `execution.py`, `authorization.py`; restarted-revocation recovery test |
| Does losing/corrupting security state fail closed? | Yes. Missing DB/owner/session/grant, malformed or unsupported records, wrong grantor or wrong authority namespace prevent invocation. No DB creation, in-memory fallback, cached allow, or automatic owner recovery occurs. If audits are unavailable, the result reports audit/storage failure and still never invokes. | cross-process damage tests; `test_missing_database_never_bootstraps_or_uses_cached_privileges`; default repository tests |

Principal v1 and Session v1 records are stored and validated against indexed
identity fields. Status changes, session closure and expiry remain effective after
restart. The security authority pins an owner principal ID and a versioned random
authority ID; a live repository refuses a changed authority identity. Ownership
never comes merely from `kind="owner"`, a social label, a task owner claim, or a
model-produced principal UUID.

Authentication remains **OPEN (IAM-005)**. The default SQLiteIdentityRepository
has no testing/bootstrap/token issuance methods, requires a trusted runtime
SessionAuthenticator, and denies if it is absent. It refuses development authority
stores and sessions marked `development.*`, even with a supplied verifier.
An authenticator must verify credentials and return a persisted session reference;
a caller-supplied UUID is not such verification. Trusted production provisioning
and the actual authentication technology are not implemented or declared accepted.

SQLiteDevelopmentIdentityService is explicitly an offline fixture subclass. It
alone bootstraps a development store, creates principals and issues random 256-bit
bearer credentials. Only SHA-256 verification digests and Session metadata persist,
not raw tokens. Holding the token remains necessary after restart; knowing its
principal/session ID is insufficient. Fixture credentials are not production
authentication. The recovery demo issues fresh fixture sessions against existing
principals, but grants only once in the first process. Revocation is not reseeded.

## Question two: concurrent task recovery

| Question | Verified behavior | Implementation / tests |
|---|---|---|
| Can workers select the same task? | Yes: inspection is not a claim. Each must acquire the task claim before recovery. | TaskStore.unfinished and SQLiteTaskStore.claim |
| Can they execute the same active step concurrently? | No under the scoped local guarantees. A whole-task exclusive OS claim is held across steps/invocation. Two synchronized independent workers yield one active worker and one busy result. Busy returns no private task contents. | `test_two_independent_recovery_workers_race_with_one_active_claim` |
| How are claims protected? | POSIX flock on a canonical database/task lock path, plus transactional expected-revision saves. SQL transactions are short; provider calls do not hold SQL write transactions. | `storage/sqlite.py`: _file_lock, SQLiteTaskStore.save/claim |
| How are expired claims handled? | There is no TTL or lease expiration. A living worker retains its claim; a hung worker is not silently displaced. This avoids declaring an effect absent because time elapsed. | No lease/scheduler change introduced; confirmed in the lock/crash tests |
| What happens when a claimed worker crashes? | The OS releases task and security file claims on process death. Running attempts remain persisted, are classified OUTCOME_UNKNOWN, and undergo replay safety checks. Actual SIGKILL tests verify release and subsequent recovery. | `test_crashed_worker_releases_claim_without_proving_effect_absence` |
| How are keys scoped? | Submission keys use (owner_id, key), bound to the immutable plan. Provider operation keys use `task:<task_uuid>/step:<step_id>` across different attempt UUIDs. Executor context now validates this scope as well as its task binding. | TaskManager, TaskRecord v2, CapabilityExecutor; scope and retry tests |
| Can workers duplicate external effects? | Competing active workers cannot invoke together locally. After a crash, only trusted pure/idempotent providers with unchanged replay metadata retry. The fake provider's atomic ledger demonstrates two calls/one effect. Arbitrary external exactly-once semantics are not claimed. | synchronized race, completed checkpoint and safe provider crash tests |
| Are ambiguous non-idempotent retries blocked? | Yes. A committed fake effect followed by SIGKILL produces NEEDS_REVIEW, without a second call; repeated recovery remains terminal and safe. | unsafe branch of the real killed-worker test |

An added reentrant, host-wide security flock serializes identity/grant mutation,
current policy checks and synchronous invocation across processes. Grant/revoke
cannot commit in the interval between an authorization check and its invocation.
An already-started provider finishes before a waiting revocation commits; subsequent
invocations see the committed revocation. Slow providers block security mutation,
so actual deadlines/interruptible providers remain future work.

TaskManager acquires the nonblocking task claim before waiting for the security
lock. This prevents a competing recovery from blocking behind the active provider
before it can discover `busy`. SQL read/claim/revision protections and completed
checkpoint immutability remain intact. Symlink paths canonicalize; hard-linked
DB aliases are rejected to prevent independent filename claim namespaces. Workers
must still share a private local POSIX filesystem; no network-filesystem or
cross-host lease guarantee is provided. Never remove a live worker's lock files.

## Changes, migration and reversible decisions

- `storage/security.py`: durable IdentityRepository/PermissionLookup adapters,
  explicit default denial/fixture isolation, versioned grant rows, current reads,
  and shared security locking. Existing owner-only administration/validation is
  reused through narrow hooks in `permissions.py`.
- `storage/sqlite.py`: storage schema **2** adds principal/session/authority/grant
  tables, development token digests, active-grant uniqueness and revocation guards.
  Schema 1 journal/task/audit data survives explicit initialization/migration.
  Existing Event v1 and TaskRecord v2 wire formats do not change. IdentityError
  remains a domain denial instead of being mistaken for an OS storage error.
- `execution.py` / `task_manager.py`: handle failed identity transaction entry
  without invoking, bind task/executor authority and operation keys, and claim
  before authorization waiting without disclosing private task data on `busy`.
- `recovery_demo.py`: persist fixture principals/grants; bootstrap/grant once.
- New unit and real subprocess verification tests, plus this report and updated
  architecture/coverage/operations/ADR documentation.

Use `uv run jarvis db-init` to migrate an existing local database explicitly.
Back it up first as documented in PHASE3.md. Migration does not import ephemeral
permissions, authenticate users, provision an owner, or reconstruct security from
task/audit IDs. Existing tasks remain inspectable and fail closed until independently
trusted authority is established. Owner recovery, production provisioning and
schema/storage/authentication policy remain open; ADR-T04 records only the
reversible local persistence and locking choices authorized for this milestone.

## Requirements and validation

Affected: IAM-001/002/003/004/007, PER-001/002/003, SEC-003/004, ARCH-004,
EVT-001, TASK-001/002, REL-001/002, DEV-001/003. Confirmed ADR-001 through ADR-012
are preserved. IAM-005, PER-004 and TASK-003/004 remain OPEN.

The baseline **256 tests passed**. The hardened suite has **291 passing tests**:
35 added cases, including **17 real cross-process cases** in
`tests/test_security_processes.py` and 18 storage/security cases in
`tests/test_security_storage.py`. Processes share one file-backed database; pipe
barriers synchronize claim races and committed-effect crash windows. No arbitrary
sleep is used to infer exclusion. Fixtures are killed and joined deterministically,
with timeouts solely to detect stalled tests. Other subprocess tests verify original
Phase 3 recovery and historical task/event durability.

Frozen installation, the complete suite, Ruff lint/format, source/wheel build,
foundation smoke, Phase 2 permissions demo, Phase 3 recovery demo and whitespace
checks all passed on Python 3.12.14. A separately installed wheel outside the
checkout passed all three CLI demos, repeated schema 2 initialization, inspection,
backup and inspection of the backup. See FOUNDATION.md; hosted Python 3.12/3.13
CI results are reported with the review PR.

## Remaining limits and Phase 4 prerequisites

Local Phase 4 development can begin: build scoped memory storage/retrieval through
current policy with provenance, consent, retention/deletion and cross-user/revoked
access tests. Use explicit offline fixtures; do not expose fixture bootstrap/session
issuance as a production authentication endpoint. All future memory reads/writes
must use current authenticated identity and policy before returning private data.

Production exposure still requires selecting/implementing trusted authentication,
owner provisioning/recovery, token lifecycle and administration interfaces. No
biometric/account technology, final policy syntax, scheduler, or permanent database
engine is selected here. Protect the database/backups/lock directory as private
local data; encryption, tamper resistance, anti-rollback storage, retention and
public scoped repository APIs are not implemented. Restoring an older backup can
roll back later revocations; operator recovery must reconcile current authority
rather than automatically treating a stale backup as current security truth.
There is no automatic restore or fixture re-grant on startup. A filesystem owner
can bypass application triggers. Provider side effects cannot be rolled back by
SQLite checkpoints, and there is no force-resume/manual review resolution API,
distributed scheduler or guarantee of exactly-once effects across arbitrary systems.
