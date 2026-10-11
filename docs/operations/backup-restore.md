# Database backup, migration and restore

Stores use SQLite WAL, FULL commits, foreign keys and short transactions. Keep them
on one private local POSIX filesystem with the database and claim paths intact.
Database schema **8** is independent of task/memory/observation v2 and other v1
contracts. Migration 8 adds local credential bindings without issuing credentials,
bootstrapping authority, regranting or resetting state. All migrations 1–7 remain.

Use explicit paths. The following example assumes the quickstart database and
**new** backup/restore destinations:

```sh
mkdir -m 700 .jarvis/backups
uv run jarvis db-inspect --database .jarvis/dev/runtime/journal.sqlite3
uv run jarvis db-backup --database .jarvis/dev/runtime/journal.sqlite3 --destination .jarvis/backups/before-migration.sqlite3
uv run jarvis db-verify --database .jarvis/backups/before-migration.sqlite3
```

`db-backup` uses SQLite's safe online backup API and includes committed WAL data.
It can run while the foreground runtime writes. Destinations must be new files;
create parent directories privately beforehand. Never copy only a live main DB
file, delete live WAL/SHM files, remove a live lock, or overwrite an existing backup.
`db-verify` checks current schema/SQLite integrity/foreign keys, not semantic
correctness of every domain payload or external provider result. `doctor` also
validates authoritative security records without mutating them.

For migration, first create a safe backup. Stop **all** writers for that database,
upgrade the checkout/dependencies, then explicitly initialize/migrate:

```sh
uv sync --locked
uv run jarvis db-init --database .jarvis/dev/runtime/journal.sqlite3
uv run jarvis db-verify --database .jarvis/dev/runtime/journal.sqlite3
uv run jarvis doctor --config .jarvis/dev/runtime.json
uv run jarvis serve --config .jarvis/dev/runtime.json
```

An old backup's schema may need explicit migration before `db-verify` can inspect it.
Migrate a **separate restore**, preserving the original backup. Migration transactions
roll back on failure; future versions are rejected without rewriting. Do not reset
security or delete the store to fix schema incompatibility. Retain logs/versions,
inspect the failure, and test the correct release against a separate backup.

Restore a verified online backup using the same SQLite backup mechanism into a
separate private directory:

```sh
mkdir -m 700 .jarvis/restore-check
uv run jarvis db-backup --database .jarvis/backups/before-migration.sqlite3 --destination .jarvis/restore-check/journal.sqlite3
uv run jarvis db-init --database .jarvis/restore-check/journal.sqlite3
uv run jarvis db-verify --database .jarvis/restore-check/journal.sqlite3
uv run jarvis doctor --database .jarvis/restore-check/journal.sqlite3 --model-provider fake
uv run jarvis serve --database .jarvis/restore-check/journal.sqlite3 --model-provider fake --port 8766
```

This standalone restore check has **no worker credential file**, so the scheduler
does not receive background authority. It reports DEGRADED but supports authorized
core inspection. Existing client tokens matching the restored bindings can be used
for a controlled test, unless revoked/expired. Do not enable real effects or run
original and cloned stores as interchangeable authorities: their local claims are
different and external effects are not rolled back by restoring SQLite.

Credential verification hashes/sessions survive backup, not raw secret files.
Securely retain required explicit credentials separately if continued access is
intended. A stale restore can roll back later revocations/expiry metadata. Reconcile
current security state and rotate/revoke credentials before using restored authority
for operation; no automatic reconciliation/provisioning is provided. Online backups
retain private records/tombstones and are not encrypted or tamper-proof.

Automated validation verifies a backup and separate restore, preserves credential
revocation, refuses destination overwrite, repeats schema initialization and proves
no authority is issued during migration. It does not simulate power loss/storage
hardware corruption or certify stale-backup authority as current.
