# Phase 5: bounded cognitive coordination and local text interaction

For current Phase 10 model guidance, grounded reasoning, project retrieval,
fixture handling and genuine Qwen results, see
[cognition hardening](PHASE10_COGNITION_HARDENING.md). The Phase 5 acceptance counts
and initially blocked model downloads below are historical milestone evidence.

This milestone adds a functioning offline text assistant using the existing Phase
2 executor/policy, Phase 3 tasks/journal/audits and Phase 4 scoped memory. A real
local model uses an interchangeable OpenAI-compatible adapter. Authentication,
final model choice, public transport, scheduler and policy language remain OPEN.
COG-002 direct replies are implemented provisionally, retaining their PROPOSED
status. See provisional ADR-T06.

## Run it

```sh
uv sync --locked
uv run jarvis cognition-demo
uv run jarvis chat --model-provider fake
```

Fake mode is a deterministic keyword fixture, **not AI reasoning**. All lighting
is simulated. The CLI explicitly selects development-only host-trusted owner,
friend or guest identities; never expose this identity selection or the fixture
bootstrap/session methods to unauthenticated clients. Production authentication
and owner provisioning (IAM-005) are not implemented.

The first chat creates `.jarvis/chat/journal.sqlite3`, seeds development identities,
exact grants and an explicitly synthetic owner-private Slipstream fact once.
Reopening issues fresh development credentials and never restores/re-grants
revoked permissions. An existing database with missing/corrupt authority fails
closed. Chat has a separate default store from the ordinary kernel journal.
`JARVIS_DATA_DIR` changes its parent directory; `--database` explicitly selects
another development database. `JARVIS_DATABASE_PATH` remains the ordinary kernel
database override and does not silently select the chat fixture store.

Resume the conversation ID printed at startup using a newly authenticated session:

```sh
uv run jarvis chat --model-provider fake --conversation <conversation-uuid>
uv run jarvis chat --model-provider fake --identity guest
```

A guest cannot resume the owner's conversation by supplying its UUID. `/exit`,
`/quit`, EOF and Ctrl-C exit cleanly; messages and accepted tasks remain durable.
Common password/API-key/bearer/private-key patterns are rejected before storing
or transmitting conversational input. This conservative guard is not a universal
secret classifier: do not paste secrets. No runtime credentials, assembled model
prompts or unrestricted
internal reasoning traces are stored. Validated final decisions and permitted
messages/results are retained in the private local database. This is logical
access isolation, not encryption or physical erasure.

## Configure a real local model

Install a local inference server separately. The adapter needs a loopback HTTP
`/v1/chat/completions` endpoint supporting non-streaming chat and
`response_format: {"type":"json_object"}`. No paid account/API key is needed.
For example, with Ollama installed on your own machine:

```sh
ollama serve
# In another terminal; initial weight download needs network access:
ollama pull qwen2.5:3b
export JARVIS_MODEL_PROVIDER=local
export JARVIS_LOCAL_MODEL_ENDPOINT=http://127.0.0.1:11434/v1
export JARVIS_LOCAL_MODEL_NAME=qwen2.5:3b
export JARVIS_MODEL_TIMEOUT_SECONDS=60
uv run jarvis chat
```

Other compatible servers, including llama.cpp and LM Studio, can be selected:

```sh
uv run jarvis chat --model-provider local \
  --local-endpoint http://127.0.0.1:8080/v1 --local-model your-served-model-name
```

`.env.example` documents process variables; dotenv is not loaded automatically.
The local adapter pins `localhost` to `127.0.0.1`, accepts numeric IPv4/IPv6
loopback addresses, disables environment proxies and redirects, and rejects remote
hosts, credentials, fragments and query strings. A homelab server requires a
trusted loopback port-forward or a future explicit self-hosted endpoint policy;
it cannot be disguised as a local endpoint through arbitrary model selection.
Keep the inference server itself configured for local computation, without its
own remote fallback. Kernel trust cannot constrain a separate server's internals.

If the endpoint is unavailable, chat prints `model_unavailable` with selected
provider/model provenance, persists the failed turn and stays usable. HTTP errors,
timeouts, incomplete/oversized output and invalid decisions have separate stable
codes. No automatic provider retry, repair loop or cloud fallback is implemented.
Malformed output is rejected rather than executed; smaller local models may need
better instruction-following models or adjusted timeout/output limits.

**Validation limit in this cloud environment:** no model server/weights were
initially present. A llama.cpp CPU release was downloaded and SHA-256 verified,
but Hugging Face model downloads returned a network-policy 403. Automated tests
exercise actual loopback HTTP and the installed CLI with a protocol test server;
that server is explicitly not an AI. Genuine inference/real-model chat must be
validated with installed weights or an available inference endpoint before
claiming every Phase 5 definition-of-done criterion. To download public weights
here, environment network settings need `huggingface.co` and the model download
hosts it uses (commonly `cdn-lfs.huggingface.co`, `cdn-lfs.hf.co` and
`cas-bridge.xethub.hf.co`). Model downloads are distinct from private-context
egress; allowing download hosts does not authorize cloud AI.

## Model contracts and coordinator

`ModelProvider.complete(ModelRequest) -> str` is asynchronous and receives no
credential. ModelRequest v1 contains trusted request/session/correlation IDs,
system/developer/user/assistant messages, bounded authorized memory snippets and
authorized capability metadata. Provider-specific transport stays in `models.py`.
`FakeModel` and `OpenAILocalModel` implement the same interface. Model identity,
destination, invocation time and duration come from trusted adapter registration,
never a model-produced provenance claim. Async deadlines cancel model requests;
HTTP resources close on timeout/cancellation. Provider exceptions are normalized
without logging their possibly sensitive messages.

`CognitiveDecision` v1 supports `direct`, `memory_grounded`, `action`,
`clarification`, `unsupported` and `failure`. It references the existing
ActionProposal v1 and ErrorDetail v1. Wire decoding requires explicit supported
versions on nested contracts too, rejects unknown fields, and distinguishes JSON
argument/content data from contract envelopes. Reasoning traces, approvals,
permission changes and arbitrary principal fields are not in the decision schema.

The bounded flow is:

```text
trusted credential -> owned conversation -> durable received turn
 -> authorized bounded context -> one selected model call
 -> validated CognitiveDecision -> durable accepted decision
 -> TaskManager.submit/run -> existing CapabilityExecutor -> current policy
 -> actual ExecutionResult + task checkpoint -> persisted final response
```

There is one model call and at most one action per turn. No autonomous agent loop
or framework is installed. Unsupported versions, forged session/correlation,
non-model origin, imported task IDs, unknown capabilities, fabricated citations
and injected approval fields are rejected before task execution. The executor
validates arguments and checks current policy for every actual invocation,
including a hostile model's proposal for a known capability omitted from discovery.
Off-catalogue intent therefore cannot create another invocation path. The
coordinator never calls a capability handler or mutates permission state.

Conversational text does not execute commands. Action-response text is generated
deterministically from the observed task and ExecutionResult, ignoring any model
claim that a tool succeeded. Sensitive/large results are represented by restricted
task references rather than copied into history. Direct conversational answers
remain model claims; memory citations reference supplied evidence and are not a
general factual verifier.

`defer_actions=True` is a trusted coordinator option for operations that should be
queued for a runtime worker. It returns a real durable task reference with its
observed state and reports submission, not completion. The development chat's
short simulated capabilities run synchronously. A production background scheduler
and capability cancellation/deadline design remain OPEN.

## Context rules

ContextBuilder uses only MemoryService's Phase 4 scoped deterministic retrieval.
It extracts up to four nontrivial literal keywords and makes bounded current-fact
queries (five visible records per term), without loading the memory collection.
No embeddings, vectors, cloud search or extraction are added.

Defaults cap the normalized serialized request at 32 KiB, six prior turn pairs,
five memory snippets of at most 4 KiB each, and sixteen capability descriptions.
Up to three task IDs/current states are included only from this owned conversation;
private task arguments/results and global task listings are not loaded into context.
The fixed decision schema adds bounded adapter instruction overhead. Large facts
are omitted rather than truncated into misleading statements. ContextLimits
configures these budgets. Registry discovery calls the existing deterministic
policy with trusted registration scopes; models receive metadata only for allowed
capabilities. Missing `memory.read` allows ordinary conversation with no memory,
not an implicit read permission. Storage/policy failures fail visibly.

Memory filtering precedes inclusion: private/shared/household scope, current grants,
tombstones, expiry, effective validity and supersession are enforced by the existing
memory service. Snippets retain source, validity, confidence and current-fact labels.
Prior conversation is bounded separately and is historical interaction data,
not authoritative current facts. Context dependency IDs include facts inherited
through history. They are rechecked for current access/validity before including
history and before using model output. Revoked/expired/deleted/superseded facts
cannot re-enter the model through an old grounded answer. Raw snapshots remain
retained in private history/backups; this does not erase previously disclosed facts.
Retrieved malicious text is data in a user-context block, never a system/developer
instruction or evidence of privilege.

## Conversation and action durability

Migration 4 adds owner-indexed conversations and ordered versioned turns to the
existing SQLite journal. It preserves all schema 1–3 authority/grants/revocations,
memory, tasks/history, events and audits without bootstrapping privileges. Back up
existing journals before migration. Initialization is explicit/repeatable; prior
Memory v1/v2 and Task v1/v2 decoders remain available.

Each turn stores user/final assistant text, times, trusted owner/session,
correlation, model provenance, memory dependencies, validated intent and task link.
Turn/event/content-free audit writes are atomic and revision checked. SQL ownership
filters precede decoding; another user gets the same unavailable code for a missing
and an inaccessible conversation. Public conversation operations require a trusted
token; storage mutation methods and bind keys are internal trusted dependencies.
Database file owners/trusted Python code can bypass these interfaces and must not
be treated as untrusted plugins.

Crash-released POSIX conversation claims serialize a conversation, while separate
users can await models concurrently. No security/SQL transaction spans a model
await. A request ID is an owner/conversation-bound retry reference, never a
credential. Reusing it with different text is rejected. Completed responses replay
without another model call or side effect; accepted decisions resume the same
owner-scoped task submission key even if the process died before linking the task.
Interrupted model calls without accepted intent fail explicitly, rather than
silently asking the model to invent a new action on retry. Task recovery retains
completed checkpoints and sends ambiguous non-idempotent effects to NEEDS_REVIEW.
Current authentication/grants are checked again. No exactly-once guarantee is
claimed for arbitrary external providers.

## Cloud boundary

Cloud AI is disabled by default and cannot be enabled through chat configuration
or model output. `CloudModelProvider` defines a future adapter boundary; there is
no production cloud implementation/account dependency. Trusted programmatic use
would require BOTH ModelRouter cloud enablement and EgressPolicy enablement,
an allowed provider/destination pair, a matching current authenticated session,
and every current exact grant `model.egress:<provider-id>:<data-class>`.

Provisional data classes are conversation, identity, memory and capabilities
(including restricted task-state metadata).
Conversation/identity are always required; memory is required for both retrieved
snippets and memory-dependent history. Egress allow/deny is audited before the
adapter receives context. Ordinary model selection, a capability grant, an old
approval, or a model claim grants no transmission rights. Tests instrument adapter
calls and prove denied context never reaches them, including missing memory-class
permission, wrong destination, disabled cloud and failed local providers. These
temporary names do not settle the OPEN final classification/provider policy.

## Demonstrated output

Example from an actual deterministic demo (UUIDs/audit arrays abbreviated):

```json
{
  "status": "ok",
  "mode": "deterministic_offline_development_fixture",
  "before_restart": {"history_turns": 2, "lighting_calls": [50], "guest_visible_memories": 0},
  "after_restart": {"history_turns_before": 2, "history_turns_after": 3,
                    "lighting_calls_on_replay": []}
}
```

Actual results include a memory-grounded owner response, fake provider/model/time
provenance, a completed lighting task with output `{"brightness":50,"simulated":true}`,
guest execution status `denied` / `permission_missing`, a guest response without
owner memory, durable task/correlation IDs and audit references. A hostile fake
proposes lighting even though guest discovery excludes it and claims permission;
the real executor denies it. Two independent workers reopen the same database,
issue fresh development credentials and prove history/correlation continuity.

Example interactive session verified with explicit fake development mode:

```text
uv run jarvis chat --model-provider fake
You> What does Slipstream use?
JARVIS> According to current authorized memory: Slipstream uses microcontroller B
You> Set lights to 40
JARVIS> Verified capability result: {"brightness": 40, "simulated": true}
You> /exit
```

Each reply also prints its structured envelope, model provenance and audit/task
references. This transcript demonstrates offline orchestration, not genuine AI.

## Files, requirements and validation

New source: `core/cognitive_records.py`, `models.py`, `egress.py`, `context.py`,
`conversations.py`, `storage/conversations.py`, `cognitive_runtime.py`, `chat.py`
and `cognition_demo.py`. Extended source: `cognition.py`, `capabilities.py`,
`core/contracts.py`, `storage/sqlite.py`, `config.py` and `__main__.py`.
Manifest/lock add the small async HTTP dependency httpx. New tests cover contracts,
models, coordinator, conversations, CLI and actual process crashes. Documentation,
example environment and CI include the new workflows.

| Requirement IDs | Implemented scope |
|---|---|
| ARCH-001/002/003/004, DEV-003 | Offline fake and interchangeable local/cloud boundary; explicit contracts/provenance |
| COG-001; COG-002 (PROPOSED) | One bounded coordination turn, direct/grounded/proposal/clarification/failure paths |
| IAM-004/007, PER-001, CAP-001/002/003 | Current authorized discovery, rejected forged authority, established executor, actual results |
| TASK-001/002, REL-001/002 | Persisted accepted intent/task links, stable retries, separate-process crash/review checks |
| MEM-001/002/003/004/006, SEC-001/003 | Scoped current retrieval, provenance, bounded history, isolation and revocation rechecks |
| SEC-002, ARCH-002 | Default-deny trusted egress, adapter-call negative tests, no silent fallback |

Baseline: **357 Phase 0–4 tests passed** before coding. The full suite now passes
**477 tests** (120 added) on Python 3.12.14. Frozen installation, Ruff lint/format,
source/wheel build and whitespace checks pass. All five finite readiness commands
pass from the checkout and a separately installed wheel imported from site-packages
outside the checkout. Wheel fake chat, repeated schema 4 initialization, inspection
and online backup pass. Hosted CI results are reported with the PR. Genuine model
validation is separate from protocol-server/fake checks and remains blocked as
described above; no real inference success is claimed.

## Known limitations and next milestone

No production authentication/public server, cloud backend, remote homelab endpoint
trust policy, background scheduler, real hardware, voice/GUI, autonomous initiative,
multi-agent reasoning, embeddings, automatic memory extraction/consolidation,
profiling, encryption, physical purge or hostile Python plugin sandbox is added.
Direct replies can hallucinate; citations are evidence references, not proof of
truth. Secrets/retention require further policy work. Final schemas, model stack,
authentication/provisioning, provider classification, storage/scheduler and final
failure/retention policies stay OPEN. Model/context/capability adapters are trusted
code, and externally hosted services require explicit policy before deployment.

Phase 6 should add local observation persistence and evidence-based world state,
with fake sensors, uncertainty/staleness, scoped retrieval and correction tests.
Continue using the same coordinator/task/executor boundaries; defer cameras,
wearables and autonomous initiative.
