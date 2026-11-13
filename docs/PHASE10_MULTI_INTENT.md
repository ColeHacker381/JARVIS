# Phase 10 bounded intent orchestration and verified composition

This follow-on replaces fresh mixed-request deferral with bounded orchestration under
provisional ADR-T12. COG-002/INT-002 remain PROPOSED. Final schemas, model stack,
authentication, generalized planning and performance targets remain OPEN.

## Contracts and execution

Conservative English segmentation identifies explicit question/command boundaries,
including “Explain Opus and set simulated lights to 45%” and the reported polite
Slipstream request. It does not infer hidden subgoals. Up to four source inputs are
supplied to **one** model invocation. Five explicit new v1 contract types describe
input references, planned decisions, the plan, individual outcomes and the composed
response. Existing single-decision CognitiveDecision/CognitiveResponse v1 remain
supported. The new response uses `contract_type: composed_response`, `kind: composed`,
`status: completed/partial/pending/failed`, and `intents` with existing response/task/
execution contracts for each component. Clients must dispatch the new envelope;
top-level task/execution remain null because each action has its own task.

The model categorizes conversational, memory, technical-reasoning, action and
clarification intents. It supplies reasoning and existing ActionProposal objects;
input indexes must be complete, unique and ordered. Version, purpose, citation,
identity, origin, task/approval import and schema checks run before submission.
An invalid overall decomposition executes nothing. Invalid or unsupported individual
operations retain a useful independent answer and a structured unresolved outcome.

Multi-intent action admission currently supports absolute simulated-lighting values
and literal development echo. The proposed adapter/value must match an explicit
source request. Relative changes, unknown integrations and inferred additional
effects are rejected. This source check cannot grant permission. Each action still
passes the existing TaskManager, input validation, current deterministic policy,
execution guard and handler. Revocation between actions remains effective.

An action result is rendered exclusively from the trusted task. Generated text for
an operation with no proposal is replaced with a trusted no-submission statement.
Common false execution claims in reasoning are withheld. This conservative English
matching is not universal natural-language truth verification; arbitrary factual
accuracy remains a model limitation. No final model call rewrites task outcomes.

## Durability, partial success and privacy

The accepted parent plan is persisted before any child task submission. Deterministic
child request IDs and existing scoped task submission keys connect checkpoints,
events, audit records and results. Each child has one validated existing decision;
completed children and accepted tasks are reused on replay. A crash after one action
does not make the model reconstruct the plan or repeat its effect.

Children are private checkpoints and excluded from ordinary history. They cannot be
resumed through the public message endpoint. Parent history and source dependencies
retain current identity/memory/world authorization checks. Revoked private context
blocks reuse without exposing old private prose or losing accepted task state.
Malformed arguments and denied actions never reach handlers.

Ordered actions permit only one pending action at a time. Pending, uncertain and
NEEDS_REVIEW outcomes block later action submission. Questions can still be reported;
denied/invalid terminal operations preserve independent successes. Exact review and
approval continue through existing task/initiative systems; conversational prose
cannot approve anything. There is no autonomous retry/repair/reasoning loop.

## Capability awareness and voice

Local model context contains an inventory derived from authorized registry discovery:
internal identity/policy/tasks/memory/world/initiative/notification/audio services,
actual chat adapters, simulation flags, multi-intent admission support and unavailable
external integrations. An internal subsystem is not a chat tool or permission grant.
Simulated lighting changes actual local simulated state; it does not control hardware.

Voice requests target 25–40 words per answer while preserving uncertainty and
provenance. Trusted `spoken_text` keeps action output, denial/error and task state,
omitting redundant spoken task UUIDs. The visual response and per-intent metadata
retain full IDs, correlations and audit references. Critical output is not truncated
to meet a word target. Both ambient synthesis and the HTTP voice CLI use this field.

## Storage upgrade and local setup

Storage version **10** is a metadata-only reader/writer compatibility barrier for
plans and child-turn fields. It adds no tables, changes no rows/grants/credentials,
and prevents older binaries from reading new turn shapes. New code reads existing
single-decision records. Serve still refuses to bootstrap or migrate implicitly.
Before upgrading a runtime store, follow the existing
[online backup and explicit migration procedure](operations/backup-restore.md):
retain a new verified backup, stop its writers, upgrade code, run `jarvis db-init`
against the intended database, verify it, and restart serve. Do not overwrite a
backup, remove live locks/WAL files or treat this as a downgrade migration.

Use an already installed local model:

```sh
JARVIS_LOCAL_MODEL_RESPONSE_FORMAT=json_schema JARVIS_MODEL_TIMEOUT_SECONDS=180 \
  uv run jarvis chat --local-model qwen3:4b-instruct
```

The same settings apply to serve; explicitly select `--model-provider local` and
the installed model name. `json_object` remains the default and the strict decoder
applies in both modes. Cloud remains disabled without the existing trusted egress
authorization, and local failure cannot trigger cloud fallback.

Repeat genuine inference against a **new** isolated synthetic store:

```sh
uv run python scripts/validate_local_cognition.py --scope orchestration \
  --partial-context --response-format json_schema --repeats 3 --timeout 300 \
  --output-dir /tmp/jarvis-orchestration-new
```

The harness uses its own runtime socket, credentials and database, replays the same
request ID and collects actual per-intent task states plus content-free timings.
Run inference serially with the complete process test suite on CPU hosts.
Reports use public synthetic project records, not the operator's real project truth.

## Generation guidance and performance investigation

Two interim Qwen series produced valid plans and useful codec ideas but omitted all
three requested lighting proposals in each series. Trusted discovery and source/value
checks were independently confirmed; no handler ran. Adding action-request hints and
explicit simulation guidance alone did not fix this. Sanitized raw-shape diagnostics
identified `kind: unsupported` with no action, not JSON parsing or authorization failure.

For grammar-capable JSON Schema mode, generation variants now follow each trusted
source index. Questions cannot choose action variants. Clear literal requests for
an exposed supported adapter get that proposal shape and requested value; unknown
or unclear operations get clarification shapes. Named supplied project context gets
citation-bearing reasoning shapes. This is a restriction of generation guidance,
not coercion/repair or authorization. An endpoint ignoring it still passes through
the unchanged full validators and current executor policy.

Positional-array guidance was unreliable on the tested Ollama build: a boolean
`items` schema was rejected by its grammar compiler, and an object-valued workaround
still allowed invalid decompositions. JSON Schema mode therefore uses an explicit
adapter-only `local_intent_slots` v1 envelope with required named slots. The adapter
validates that complete schema before ordering existing slot values into the core
IntentPlan v1 array. It supplies no missing text, citations, proposals or authority.
Malformed slots fail closed; the core independently validates the resulting plan.
JSON object mode continues to request the original IntentPlan directly. This is
transport normalization, not output repair or a second execution path.

Telemetry separates context construction, model invocation, schema bytes, endpoint
token counts, plan validation and orchestration. OpenAI endpoints often omit native
backend timing fields; missing samples remain missing, rather than fabricated zeros.
Observed CPU latency is not a MacBook hardware diagnosis. Model validity, fulfilled
intent count, task completion and semantic quality are reported separately.

## Actual local-model acceptance

[Sanitized measurements and verified task IDs](reports/phase10-multi-intent.json)
retain intermediate failures as well as the final results. Ollama 0.40.3 ran cached
Qwen3 4B Q4_K_M locally with four CPU threads and a 4096-token context. The named-slot
series accepted **9/9** genuine responses: an eight-case matrix, then the exact mixed
request again after correcting conflicting voice length guidance. All **4/4** mixed
requests supplied useful conditional codec recommendations, cited the authorized
synthetic provisional project source, and completed brightness-45 tasks. Each same-ID
replay was unchanged. Multiple-action testing independently completed 25 then 45;
there were six verified lighting invocations overall. Guest and Slack requests retained
their questions with unresolved operations and no restricted handler invocation.

This finite sample does not establish a guaranteed success rate or a new-plan
JSON object versus JSON Schema comparison. Earlier unrestricted plans omitted actions,
and positional-array guidance accepted only 2/3 mixed requests in its final series;
the third lacked required memory references. Those outputs remained fail-closed.

The four accepted mixed requests measured **55.3–74.6 seconds** for model invocation,
**65–86 ms** for context building, **0.6–1.8 ms** for plan validation and **148–202 ms**
for orchestration. Generation schema size was **14,081 bytes**, with **1712–1771 input**
and **283–309 output tokens** reported by the endpoint. Model inference dominates
elapsed time. Native prompt/decode timings are not exposed by this OpenAI endpoint;
missing metrics remain missing. There is no demonstrated orchestration latency problem
or justification for weakening validation. A cold request exceeded the unchanged
60-second default; the documented explicit 180-second local setting accommodates this
CPU sample. Voice word targets remain advisory; essential results are never cut off.

Latest genuine response (synthetic context, not actual Slipstream design evidence):

```text
1. Audio codecs like Opus suit low-latency intercoms, FLAC enables lossless
diagnostics, and AAC provides compact recordings. These are design options,
not established project features, given Slipstream's provisional status.
2. Verified capability result: {"brightness": 45, "simulated": true}
[Task b4a47437-948b-47e2-8d35-c0cacf399671: COMPLETED]
```

The final voice rendering omitted only the task UUID and retained state/output.
Physical speech capture/playback and the operator's MacBook latency were not exercised.

## Regression and packaging verification

Baseline: **961 passed**, one upstream warning, 290.39 seconds. Final complete suite:
**1003 passed**, one upstream warning, 305.02 seconds; focused contracts/providers/
initiative-state checks: **71 passed**, 16.63 seconds. The first full run exposed an
older test stub returning one CognitiveDecision for two questions. Its fixture now
emits the required plan while retaining actual pending-approval/no-handler assertions;
an additional test proves the core rejects a single decision substituted for a plan.

Ruff lint and formatting passed (162 Python files). `docs-check` verified 45 Markdown
files, 147 internal links, three generated references and 25 API paths. Sdist/wheel
build, isolated wheel installation, mixed-request fake chat through the installed
wheel, and all eleven checkout plus installed-wheel demos passed (**22** demonstrations).
Runtime results verified restart persistence, guest isolation and zero replayed lighting
invocations; voice results exercised actual simulated core workflows. The sole warning
is Starlette's deprecated AnyIO BlockingPortal alias. There were no skipped tests.

## Requirement coverage and limitations

ARCH-001/003/004, COG-001, provisional COG-002/INT-002, INT-001, IAM-004/007,
PER-001–003, CAP-001–003, TASK-001/002, MEM-001–004/006, SEC-001–004, REL-001/002
and DEV-003 are addressed or retain their existing boundaries. Tests cover questions,
memory/reasoning, permitted and denied actions, multiple adapters, unsupported and
malformed intents, false claims, exact replay, separate-process partial recovery,
pending ordering, revocation, child access and nested versions. No status is promoted.

Limits include explicit English boundaries, four intents, two current admission
adapters, absolute values only, bounded output, and conservative prose rejection.
Conditional/dependent plans, arbitrary integrations, universal grounding verification,
automatic approval/reconciliation and unrestricted autonomous execution remain deferred.
Physical microphone/speaker acceptance requires the operator's hardware.
