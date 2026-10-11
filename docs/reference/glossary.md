# Glossary

| Term | Meaning |
|---|---|
| Principal | Persisted actor identity; its UUID is not a credential |
| Session | Persisted authenticated principal binding with current expiry/closure |
| Client credential | High-entropy development bearer mapped server-side to a session |
| Worker credential | Separately typed development credential supplied explicitly to bounded background jobs |
| Grant | Current exact principal/permission authority; an old grant reference cannot restore it |
| Scope | Private/shared/household data access boundary, independent of action permission |
| Proposal | Validated intent from a model/rule/user; not authorization or proof of execution |
| Task | Durable plan, attempts, checkpoints and verified outcomes under current executor policy |
| NEEDS_REVIEW | Terminal conservative handling of an ambiguous non-idempotent outcome; no blind replay |
| Evidence | Immutable authorized observation/source references, distinct from inferred belief |
| World state | Scoped current projection with confidence/status/TTL and evidence |
| Initiative | Bounded permission-governed rule proposal, optional exact approval and guarded task |
| Notification | Durable recipient-scoped source-derived inbox record with separate transport/interactions |
| DELIVERED | Observed local/simulated transport result, not human reading or approval |
| Acknowledgement | Inbox interaction only; no capability privilege or initiative approval |
| Idempotency | Stable request/provider keys plus a trusted adapter replay contract; not universal exactly-once |
| Readiness | Core authoritative services and scheduler operational; optional components may be degraded |
| DEGRADED | Core can operate while an optional model/worker/adapter is explicitly unavailable |
| Egress | Transmission outside the local privacy boundary; requires separately trusted policy |
| Provisional | Reversible implementation authorized for this milestone, not ratification of OPEN architecture |
