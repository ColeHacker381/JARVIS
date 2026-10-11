# Phase 9 integration plan

Baseline: **724 tests passed in 159.41 seconds**, Python 3.12.14, frozen uv install.
Phase 8 PR #8 is merged; this work starts from main `37e6346`.

Reuse deterministic policy, current durable identity/grants, registry/executor,
TaskManager claims/checkpoints/guards, private memory/world/conversation stores,
bounded initiative operations, notification bridge/delivery recovery and model
router. Move existing development composition into one `application.py` root;
retain the old development wrapper and every existing CLI/demo.

Add validated runtime settings, explicit host-only development credential
provisioning, read-only identity verification, one bounded scheduler, FastAPI
routes and foreground Uvicorn lifecycle. HTTP never selects owner/friend/guest or
issues credentials. Client and worker credentials have separate kinds, explicit
files, expiry and durable revocation. Loopback, explicit bearer authentication,
browser-origin denial, Host checks and request/concurrency bounds protect the
provisional local surface. Production provisioning/authentication remain OPEN.

Order: settings/logging -> existing compatible storage -> validate durable
authority -> compose/register execution guards -> authenticate configured worker
credentials -> bounded initial recovery -> model availability probe -> scheduler
and local listener -> readiness. Stop admission -> stop scheduled admission ->
drain active work within the configured timeout -> stop network resources.
Uninterruptible providers require process termination after timeout; persisted
attempts remain authoritative and unknown effects are never declared absent.

Database ownership stays with the existing SQLite adapter. Explicit migration 8
adds credential bindings without provisioning authority, changing grants or
rewriting source records. `serve` does not migrate/bootstrap automatically.
One local-host runtime claim protects scheduler ownership; existing domain claims
still protect actual tasks/deliveries. The scheduler invokes finite service passes
under separately provisioned current worker credentials. No policy is enabled at
startup. Jobs fail independently; storage/security failure stops unsafe admission.

Documentation deliverables: index, reproducible quickstart, complete settings/CLI/
API references, subsystem manual, operator deployment/backup/diagnostics/security/
troubleshooting, concrete extension examples, diagrams, tested link/quickstart
checks and v0.9 release report. Verify actual installed commands/routes/configuration
and all prior demos, plus real process API restart and integrated runtime-demo.

Risks/deferred: local POSIX locking only, blocking synchronous providers, retained
private data, stale-backup authority rollback, local credential theft by the OS
user, structured-output model compatibility. No real sensors/messaging, public
listener, distributed scheduler, production auth or cloud fallback is selected.
