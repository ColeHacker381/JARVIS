# Local development API security boundary

**This release is not production secure or publicly deployable.** Run under a
trusted unprivileged OS account on a private local filesystem. Only literal
`127.0.0.1` or `::1` listeners are supported. Do not add a tunnel, reverse proxy,
public bind, forwarded-header trust or broad CORS workaround.

Threat model: untrusted clients, hostile browser pages/model output and other
JARVIS principals may call the loopback HTTP service or guess record IDs. The
trusted computing base is the OS user, private credential/store/lock directory,
registered Python adapters, composition, deterministic policy and core services.
A process/user that can read or modify those files can bypass this development
boundary. Loopback alone is not authentication. No untrusted Python plugin sandbox,
production account assurance, encrypted/tamper-proof storage or distributed claims
are supplied.

Host-only explicit provisioning generates 256-bit opaque client/worker credentials.
SQLite migration 8 stores SHA-256 verification digests and immutable persisted
session bindings, kind and revocation. It stores no raw credential. Constant-time
digest comparison, bounded token input, current session expiry/closure/principal
status and current grant checks apply. Client credentials cannot become workers;
worker credentials are rejected by the client API. Only explicit owner/user IDs
in the trusted host provisioning command select principals. There is no HTTP
bootstrap/login-by-role/session-UUID endpoint.

Raw credentials exist only in explicitly provisioned private 0600 files. Protect
their 0700 parent directories and backups. Runtime worker files must be regular,
same-user, bounded, 0600 and not symlinked. Do not print credentials, use them in
URLs/command arguments, commit them, paste them into chat or store them in settings.
The curl export command writes a new private config instead of putting a bearer
token on the command line. Runtime access logging is off; application logs/metrics
do not contain credentials or payloads. Known credential strings and recognized
secret patterns are rejected in conversation input; this is not universal DLP.

The API uses explicit Authorization: Bearer credentials, no cookies or implicit
browser authority. Requests containing Origin or browser fetch-site metadata other
than `none` are denied, including same-origin mutation attempts. Host checks and
actual loopback client checks resist DNS rebinding; forwarded headers are untrusted.
There is no permissive CORS middleware. Strict bodies reject role/session/grant
fields; explicit identity headers are rejected. Body bytes/read time, pagination,
active requests and executor queues are bounded. Local denial of service is reduced,
not eliminated. API JSON errors omit raw inputs/internal stack traces.

OpenAPI is bearer-protected at `/api/v1/openapi.json`; public Swagger/Redoc pages
are disabled. Download it using a private client configuration to inspect with a
trusted local editor. Unauthenticated health contains no private records or global
user backlog counts. Missing and foreign resources share unavailable errors; owned
pagination does not expose global insertion sequences.

Every private route delegates to current core service authorization. The HTTP
layer does not select protected SQLite rows or authorize based on model claims.
Approval is a separate revision-checked operation against the existing immutable
initiative proposal; read/ack/dismiss/urgency cannot approve or grant privileges.
Pending/recovered tasks use the same executor/guards/current grants. Revocation
cannot undo an already started effect; the host-wide security claim serializes
committed revocation with subsequent synchronous invocation.

Cloud AI is off with no automatic fallback or cloud notification provider.
Local model endpoints retain the existing credential-free loopback, no-redirect,
no-proxy restrictions. Future cloud adapters still require separate trusted
per-destination/per-data-class egress grants; model or channel selection is not
egress authorization.

OPEN owner decisions before wider deployment: production authentication and owner
enrollment/recovery, policy language, credential lifecycle/storage, TLS/network
access, permanent scheduler/distributed locking, real sensor consent/enrollment,
channel destination/egress, severity/urgent rules, retention/purge, provider
idempotency/reconciliation and operational SLOs. ADR-T10 is provisional, not an
acceptance of these choices. Restoring a stale backup can restore stale authority;
reconcile revocations/rotate keys before treating a restore as current.
