# Phase 10 cognition hardening

This is a follow-on to Phase 10, under provisional ADR-T06/T11. COG-002 and INT-002
remain PROPOSED; production identity, final schemas/model stack, retention,
hardware and latency targets remain OPEN. No new architectural decision is accepted.

The later [mixed-request follow-on](PHASE10_MIXED_INTENT.md) closes the reported
“were set” presentation hole and adds trusted separation, history/replay/voice
projection and repeated acceptance for both configured structured-output modes.
Historical measurements below describe the earlier hardening milestone.

## Confirmed causes and reproduction limits

The pre-change code was inspected before implementation. The existing 889-test
suite passed. Real Ollama/Qwen tests used a new development database through the
authenticated conversation text API, independently of Whisper/TTS.

| Finding | Evidence | Change |
|---|---|---|
| Restrictive project reasoning | System instruction said “using only supplied context”; real partial-context design request returned clarification | Allow general technical knowledge, with separate recalled claims, assumptions, inferences and recommendations |
| Late project mention omitted | Exact brainstorming request selected the first four keywords; three real runs included zero memories | Filter request filler words and prioritize named terms before four bounded searches |
| Generic “other projects” | No structured project retrieval or current source snippets for follow-ups | One bounded current project-only query; bounded authorized source lookup from permitted recent history |
| Lighting `proposal_context_invalid` | Real Qwen action used `origin: "user"`, without a task ID; no task/handler ran | Constrain model-facing origin to `model`; omit kernel-generated task/proposal/time metadata; retain independent binding checks |
| Administrator `model_output_invalid` | Real escalation fixture returned `kind: "failure"` without ErrorDetail; strict consistency validation rejected it | Guide refusals to `unsupported`; optional structured schema expresses existing cross-field rules |
| Intentional interruption reported failed | Voice CLI grouped `playback_cancelled` with operational AudioError | Report `status: cancelled`; ambient SpokenResponse already supports cancelled; preserve completed cognitive/task result |
| Long speech | Entire response went to TTS with no speech-specific generation guidance | Voice requests concise generation through a presentation preference; never truncate verified results |
| Fixture promoted into a design constraint | Initial schema-mode real Qwen brainstorming referred to microcontroller B as already chosen despite the fixture warning | Deterministically exclude development records and their derived history from engineering context; retain explicitly labeled recall |
| Offline demo failed under slow inference | A 2.1-second delayed fake model reproduced a duplicate assertion after the production two-second acoustic window expired | Use an injected acoustic fixture clock for simultaneous demo microphones; keep production expiry unchanged |
| Unverified lighting completion | Real Qwen produced a direct “lights have been set” reply without a proposal, task or handler | Add a concrete existing-contract action example and reject the reproduced unverified completion pattern; permissions remain independent |
| Status 500 after real inference | OpenAI envelopes omit optional timing metrics, recorded internally as None; the status v1 schema requires floats | Omit unavailable samples from status, retain internal missing values, never invent zero timings |
| Small context omitted world evidence | Expanded instructions consumed the existing 4 KiB context test budget | Compress instructions while retaining the same limits and security rules |
| Quickstart raced the scheduler | A submitted turn was pending while its task had already completed | Wait for the actual task, then replay the same request ID; never submit another action |
| JSON syntax brainstorming without supplied memories | Three interim real responses failed classification; an instrumented repeat confirmed `memory_grounded`, zero memory/world references and `memory_references_required` | Explicit no-citation guidance and a model-facing kind restriction when no memory snippets are supplied; invalid outputs still fail closed |

The exact reported brainstorming prompt returned valid `direct` JSON **3/3 times
before these changes here**, but lacked project memories. Its MacBook
`model_output_invalid` was not reproduced. The original raw output and validator
error were not available, so its exact cause cannot be established retrospectively.
The administrator reproduction is a representative escalation fixture; the user's
original exact escalation utterance was not supplied.

A later fixture-only test with interim hardening guidance reproduced
`model_output_invalid` for the exact brainstorming request. JSON parsing and field
types succeeded; the decision's citation-required kind contradicted its empty
reference arrays. The instrumented validator rule confirmed that specific cause.
This explains that observed reproduction, not the unavailable original Mac output.

One initial real request was interrupted to reload the owned CPU inference runner.
It produced `model_http_error`, a setup interruption separate from output validation.
Results are finite observations, not a guarantee for every prompt/model/version.

## Contracts, guidance and diagnostics

CognitiveDecision, ActionProposal, MemoryRecord and conversation wire versions are
unchanged. `memory_grounded` supports factual recall **and** engineering reasoning
using authorized evidence plus general knowledge. Text should label stored claims,
assumptions and recommendations. Missing evidence warrants clarification for an
established-fact question; it does not prohibit conditional design options.

The adapter's model-facing schema is derived from the existing contracts. It removes
optional kernel-generated proposal metadata and pins session/correlation/source/origin
to supplied values. JSON Schema mode additionally expresses the existing consistency
rules: action requires ActionProposal, failure requires ErrorDetail, grounded replies
require citations, and direct replies cannot carry memory citations. Pydantic's original
generated schema does not export these `model_validator` constraints.
When no memory snippets are supplied, the generation schema excludes
`memory_grounded` and requires an empty memory-reference array. The original decoder
and coordinator remain strict; no invalid grounded reply is downgraded to direct.

This schema is generation guidance. Every output still passes the full existing
decoder, explicit-version checks, citation checks, proposal bindings, registry,
TaskManager and current executor authorization. A server ignoring schema constraints
cannot bypass validation. There is no output coercion, repair loop, tool-success
fabrication, conversational approval, authorization mutation or cloud fallback.

A compact conditional engineering example addresses the reproduced codec-design
refusal. It applies only to exploratory questions and illustrates the existing
`memory_grounded` envelope with a supplied memory ID when available. Example text
is explicitly hypothetical, not project evidence. Ordinary direct and action
requests retain their existing adapter guidance. This question matching affects
presentation only; it cannot grant permission or submit work.

An authorized capability is used only to illustrate a minimal action envelope in
the local prompt. Bounded name matching chooses that example; empty arguments must
be filled from the request and capability input schema. This does not choose or
submit an action, infer authorization, or supply an alternate executor. Real testing
also reproduced a direct lighting-completion claim with no execution. A contextual
output-integrity guard rejects that specific English completion pattern, and the
observed direct “Setting the simulated lights…” claim, for lighting
requests with `unverified_action_claim`. It never submits an action. This guard is
not general natural-language fact verification; valid JSON alone proves neither
project truth nor capability success. Actual action responses still come from tasks
and observed executor outcomes.
The progress-phrase check applies to direct replies, preserving unsupported/
clarification explanations such as “Setting lights requires permission.” Explicit
completed-action claims remain rejected even when mislabeled `unsupported`.

The default `json_object` mode preserves existing compatible servers. Explicit
`json_schema` mode requires backend support and reports actual HTTP failures if
unsupported. Selecting it grants no data-egress permission.

Failures retain stable error codes. ErrorDetail.message and correlated
`model.output_rejected` logs now include allowlisted metadata: validation stage,
byte count, JSON root type, known field types, unknown-field count, reference-array
counts, allowlisted consistency-rule codes and built-in validation type codes.
Diagnostics exclude field values, unknown field names,
prompts, raw responses, validation input/context and internal reasoning. Stages
separate provider envelope/JSON, decision JSON/version/schema/classification,
memory citation, proposal identity binding, origin and imported task ID. Voice
metadata prints the structured error and correlation ID for diagnosis.

An incomplete completion remains `model_output_incomplete`; it is never repaired
or executed. Actual endpoint-reported token metrics are retained even for truncation.
The request output-token default remains 1024; no silent limit increase is introduced.

## Memory relevance and fixture migration

Retrieval remains local and deterministic: up to four scoped current keyword queries,
one five-record project query only when the request mentions plural projects, and
bounded current source lookup from permitted recent history. Project filtering occurs
in the existing SQL query before ordering/limits, with the same authorization,
scope, lifecycle and audit checks. It supports arbitrary non-null project references,
not just a hardcoded Slipstream or `project:` prefix.

Budgets remain 32 KiB normalized request, six recent pairs, five snippets of at most
4 KiB, and sixteen capability descriptions by default. All included dependencies
are checked again before using model output or replaying history. Superseded,
expired, tombstoned or revoked evidence cannot return through older conversation.
Conflicting/provisional claims keep their content, confidence, provenance and validity;
no automatic conflict resolution or new fact-verification claim is introduced.

`development.cognition.fixture` is synthetic data, not verified Slipstream truth.
New fixtures use `source_category: import`; existing records are not rewritten.
Both old and new fixtures retain the exact source reference. Explicit recall includes
an enforced unverified-fixture preface even when the model omits its own disclaimer.
Engineering requests exclude development records and history whose context depended
on them. This conservative deterministic selection is not execution authorization;
genuine authorized records remain available. Fixture recall cannot silently turn
microcontroller B into an engineering constraint in a later design question.
Separate development stores remain required.

For a fresh runtime without the Slipstream fixture:

```sh
uv run jarvis runtime-init --data-dir /absolute/new/private/runtime \
  --model-provider local --local-model qwen3:4b-instruct --no-memory-fixture
```

For an existing store, back up first using the documented online backup workflow.
Use an authenticated trusted host program with the existing MemoryService to search
for the exact fixture source, inspect each record, and explicitly `delete` it with
its current expected revision. Create genuine project records separately through
`MemoryService.create(MemoryDraft(...), session_token=...)`, with actual owner-supplied
claims, source/consent/project references and uncertainty. Do not relabel or supersede
demo content into verified truth. Tombstones preserve audit/provenance history;
reopening never reseeds deleted fixtures or regrants permissions. The current HTTP
API does not expose memory creation/deletion. Host database/Python operators remain
outside the untrusted model boundary; supplied provenance is not source verification.

## Local setup and repeatable real acceptance

Keep Ollama bound to loopback and local inference only. Install the actual model:

```sh
ollama pull qwen3:4b-instruct
uv run jarvis runtime-init --data-dir /absolute/new/private/qwen-runtime \
  --model-provider local --local-model qwen3:4b-instruct \
  --local-response-format json_schema
uv run jarvis serve --config /absolute/new/private/qwen-runtime/runtime.json
```

To retain JSON syntax mode, omit `--local-response-format`. Existing runtime files
may explicitly set `local_model_response_format` to `json_schema` after checking
backend support. Chat supports `JARVIS_LOCAL_MODEL_RESPONSE_FORMAT=json_schema`.
The default timeout is still 60 seconds; CPU deployments may explicitly increase
`model_timeout_seconds` up to 300 after measuring actual inference latency.

The opt-in acceptance harness starts its own isolated loopback subprocess and
credentials, preserves the production default port, and uses only public synthetic
fixtures. It refuses an existing output directory or an unadvertised model:

```sh
uv run python scripts/validate_local_cognition.py \
  --model qwen3:4b-instruct --response-format json_schema --repeats 3 \
  --output-dir /absolute/new/private/qwen-acceptance
```

It exercises direct multi-turn conversation, memory recall, grounded design reasoning,
the exact brainstorming request, actual simulated lighting and replay, guest refusal,
administrator escalation, unsupported project facts and guest memory isolation.
Reports contain validated final responses to synthetic fixtures, model provenance,
actual word counts/durations, execution/audit references, numeric metrics and sanitized
rejection diagnostics. `completed` means the harness finished; inspect per-case
failures and the explicitly counted JSON-valid brainstorming rate. Semantic review
is separate: an earlier run returned valid JSON but improperly promoted fixture
hardware into a project fact, and was not accepted. It does not claim hardware
or universal factual correctness. Use `--scope brainstorm` for a short repeated probe.
The full harness checks lighting first and records `lighting_verified` independently
of conversational text. `--scope actions` selects capability/refusal cases.
`--partial-context` seeds an explicitly synthetic, provisional motorcycle scenario
in the new private store; it never changes an existing user's project memories.
Post-run authorized reads record actual context source references, including whether
development fixtures were excluded from design requests.

The internal metric sink preserves missing provider timings as None. The unchanged
status v1 float schema exposes only available numeric samples; missing timing is
not synthesized as zero. Core invocation durations and endpoint-reported token counts
remain separate from unavailable endpoint timing measurements.

The cloud validation installed Ollama v0.40.3 from an official release with verified
SHA-256, and registry Qwen3 4B Q4_K_M weights with verified layer SHA-256. The model
weight digest is `85e4a5b7b8ef0e48af0e8658f5aaab9c2324c76c1641493f4d1e25fce54b18b9`.
The local CPU parameter was set to four threads; this changes configuration, not
weights. Downloads are public artifacts, not private-context cloud inference.

The fixture-only JSON syntax failure was not truncation: observed input counts were
1,992–1,995 tokens and completed output counts 227–283, below the 1,024 output limit.
The instrumented repeat identified decision classification and empty references,
not JSON parsing. Fixed JSON syntax runs used 2,015–2,021 input tokens and 138–228
output tokens; local runner logs reported `truncated = 0`. This does not establish
the input/completion limits or original output shape on the user's MacBook.

Ollama advertises thinking support for this tag. A separate public, one-sentence
JSON probe with default settings and `reasoning_effort: none` returned 36 completion
tokens and no reasoning field in both cases. The second request reused 22 prompt
tokens. This does not establish hidden reasoning as the cause of longer requests;
the adapter's backend defaults remain unchanged.

## Voice presentation and cancellation

Voice clients use the existing v1 body and optional `Prefer: response-style=voice`
header. It is a presentation hint, not identity or authorization. Ambient trusted
host calls request the same guidance. Ordinary explanations target 60–90 words,
with compact labeled design options. Essential provenance, uncertainty and verified
outcomes take priority over that target. Full returned text/history is preserved;
no string truncation or second summarizing model call occurs. Completed request
replays return the previously recorded response even if the presentation hint changes.

Enter cancellation prints:

```json
{"status":"cancelled","reason_code":"playback_cancelled","stage":"playback"}
```

The bounded audio write is drained and the output-stream context closes before the
client resumes. Cancellation after the last write is detected too. Ambient output
records `SpokenResponse.status=cancelled`. Genuine device errors still fail visibly.
Neither interruption nor TTS failure retries cognition, submits another task, or
undoes a completed effect. Durable request replay remains authoritative after the
short acoustic deduplication window expires.

The offline demo gives its simultaneous microphone fixture an injected clock.
Production acoustic expiration remains two seconds. The runtime quickstart uses
explicit fixture recall after restart and handles a scheduler-owned pending action
by waiting on its actual task and replaying the original request. Existing POSIX
listener inheritance, private readiness acknowledgement, child-only cleanup,
SIGKILL/SIGINT, claims, credentials and crash-recovery checks are preserved.

Run model acceptance and the full regression suite serially on this four-CPU cloud
allocation. A concurrent run incurred process-test deadlines and a model timeout;
the complete security process suite and all 924 tests then passed with inference
idle. No process timeout, claim, locking or recovery assertion was weakened.

Physical MacBook device release, microphone/speaker quality, latency and interrupted
playback remain operator acceptance checks. The cloud tests validate the stream
lifecycle with a PortAudio fixture; no physical output device is available here.

## Requirement mapping

ARCH-001/003/004, COG-001, provisional COG-002, INT-001/provisional INT-002,
MEM-001–004/006, IAM-004/007, PER-001, CAP-001–003, TASK-001/002, SEC-001–003,
REL-001/002 and DEV-003 are addressed through existing local services and validation.
Production authentication, real device control, semantic/vector retrieval, automatic
memory extraction, cloud inference and final architecture choices are not added.

## Files changed

New files are `src/jarvis/model_output.py`,
`tests/test_cognition_hardening.py`, `scripts/validate_local_cognition.py`, this guide,
and the accompanying sanitized acceptance report.

Existing cognition/model modules extended: `cognition.py`, `context.py`, `models.py`,
`memory.py`, `storage/memory.py`, `observability.py`, `application.py`, `chat.py`,
`config.py`, `runtime_cli.py`, `runtime_service.py`, `local_api.py` and
`documentation.py`. Voice/runtime fixes extend `speech.py`, `voice.py`,
`voice_cli.py`, `voice_demo.py` and `runtime_demo.py`. Existing regression suites
updated: `test_audio.py`, `test_cognition.py`, `test_models.py` and `test_runtime.py`.

Configuration and coverage documentation updated: `.env.example`, `README.md`,
`ARCHITECTURE.md`, `DECISIONS.md`, `REQUIREMENTS.md`, `ROADMAP.md`,
`docs/FOUNDATION.md`, `docs/PHASE5.md`, `docs/PHASE10.md`, `docs/README.md`, and the
generated CLI/configuration/OpenAPI references. Contract versions and database
schema versions are unchanged.

The next acceptance step is rerunning the harness and short/interruptible real voice
conversation on the user's MacBook. Production identity and explicit enrollment/
retention decisions should precede privileged hardware or ambient expansion.

## Real-model evidence

Final deterministic regression: **931 passed in 257.71 seconds**, zero failures or
skips, one existing upstream Starlette/AnyIO warning (42 cases added to the 889-test
baseline). Ruff lint and formatting pass for 156 Python files. Documentation checks
pass for 43 Markdown files, 131 internal links, three generated references and
the unchanged 25 OpenAPI paths. The full suite includes current revocation,
cross-user privacy, prompt injection, task/claim recovery, SIGKILL/SIGINT,
cancelled playback and the occupied-8765 regressions.
Locked voice-extra installation, source/wheel builds and all eleven smoke/demos
pass in both the checkout and a fresh installed wheel outside it. Wheel fake chat
retrieves labeled memory, executes simulated lighting and resumes in a separate
process. The saved environment installer was rerun successfully, including signed
audio packages, pinned whisper.cpp rebuild, both model SHA-256 checks and actual
device discovery (`devices: []`). No physical audio playback is claimed.

The [sanitized measured report](reports/phase10-cognition-hardening.json) contains
actual validated public-fixture responses, provider metrics, context source
references, tasks/audits and prior failed observations. It is separate from the
historical [real-speech report](reports/phase10-real-audio.json).

| Real check | Observed result |
|---|---|
| Exact brainstorm, JSON Schema with authorized provisional project context | 3/3 valid `memory_grounded`; three conditional engineering options; no fixture hardware |
| Exact brainstorm, default JSON syntax with fixture-only data excluded | 3/3 valid `direct` after the empty-citation fix; no memory supplied |
| Real multi-turn direct conversation | Two valid direct replies, topical continuity |
| Explicit fixture recall | Valid grounded reply with enforced unverified-fixture warning |
| Simulated lighting | Actual completed task at brightness 25; same request replay; one invocation per new store |
| Guest/escalation/missing project facts | No unauthorized handler or private-memory context; unsupported/fail-closed results recorded |
| Actual `jarvis chat` | Local Qwen direct response, clean `/exit`, 84 words; no fake inference |

The partial-context run preceded the empty-citation refinement, which applies only
when no memory is supplied. Its positive grounded-generation path is unchanged.
Later action and default-mode probes exercised the revised no-memory path. A real
unsupported guest reply was over-rejected by the interim progress guard; the final
guard distinction is covered by deterministic regressions, including rejection of
completed claims hidden under unsupported kind. A final real Qwen repeat verified
one lighting invocation and same-result replay; guest lighting and escalation
returned `unsupported`, and guest memory context remained empty.

The shorter “How can we use…” response now cites context and lists possible uses,
but remains too cautious about undefined requirements. The exact explicit request
produces more useful options. Qwen can still make general technical mistakes,
including calling WAV a codec and conflating compression with ADC conversion.
This milestone validates contracts, scope and observed execution, not arbitrary
technical truth. Actual generated voice-targeted responses ranged beyond the soft
60–90-word target (including 118 and 148 words); essential text was not truncated.
Earlier runs timed out on this CPU host. Finite successes do not guarantee every
prompt, model/server version or deployment load.

One actual brainstorming response with partial context was:

> Based on general knowledge, audio codecs could be used in the Slipstream project
> in the following three ways: 1. Compress intercom speech for efficient transmission
> and reduced bandwidth usage during rider communication. 2. Store recorded audio
> for playback, such as navigation cues or maintenance alerts, enabling on-demand
> access to critical information. 3. Stream real-time navigation cues using adaptive
> audio codecs to deliver clear, low-latency voice guidance under varying environmental
> conditions. These options are technical possibilities, not established project decisions.

The executed offline `cognition-demo` returned the following selected fields
(the full output includes actual request/task/correlation and audit IDs):

```json
{
  "status": "ok",
  "mode": "deterministic_offline_development_fixture",
  "memory_answer_kind": "memory_grounded",
  "lighting": {"state": "COMPLETED", "brightness": 50, "simulated": true},
  "guest_action_error": "permission_missing",
  "guest_visible_memories": 0,
  "lighting_calls_on_replay": 0
}
```

An actual installed-wheel development chat session (JSON metadata omitted here):

```text
You> Slipstream
JARVIS> Development fixture context is unverified and is not established project truth.
        Development fixture, not verified project truth (development.cognition.fixture):
        Slipstream uses microcontroller B
You> Set lights to 25
JARVIS> Verified capability result: {"brightness": 25, "simulated": true}
You> /exit
```

The separate process resumed that printed conversation ID and retrieved the same
labeled fixture on explicit recall. Fake chat demonstrates core integration, not
AI reasoning; the separate genuine Qwen CLI/API observations above establish
local inference.
