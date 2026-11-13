# Phase 10 mixed requests and verified action reporting

This follow-on retains provisional ADR-T06/T11 and all v1 contracts. COG-002 and
INT-002 remain PROPOSED. Production identity, final model stack, generalized intent
planning, schemas and latency targets remain OPEN.

Fresh mixed requests are now extended by [bounded orchestration](PHASE10_MULTI_INTENT.md).
The results below document the earlier safe-deferral milestone; old request IDs and
stored evidence retain their historical meaning and never trigger retroactive actions.

## Confirmed cause

A deterministic provider returned a valid `direct` decision saying “The simulated
lights were set to 45%.” The real coordinator displayed it with no task, execution
or handler call. The older output guard matched “have been set” and “are now set,”
but missed “were set.” Validating JSON structure cannot establish execution truth.
The existing single-decision contract also cannot represent a separately verified
technical answer and action. Exploratory prompt guidance and action guidance were
both applicable to the reported combined request.

The exact combined request was also tested against genuine local Qwen through the
authenticated text API before this fix. Here it returned `memory_grounded`, no task,
and an incorrect explanation that the simulated capability could not adjust brightness.
It did **not** reproduce the MacBook's false completion in that finite real run.
The separate action completed at brightness 45. The deterministic reproduction
establishes the reporting hole independently of model variability or voice.

## Reporting and request separation

The coordinator recognizes conservative English lighting command clauses for the
current simulated adapter. A question plus such a command, or multiple commands,
produces a persisted, audited clarification **before** context retrieval or inference:

> This contains multiple requests. No action was submitted or executed for this request.
> Please send the question and each action separately.

This recognizes requests solely to defer them. It cannot authorize anything, construct
a proposal, change approval state or invoke a handler. Separate action proposals still
pass the unchanged version/schema/context/registry validation, TaskManager and current
executor policy. Completed, denied, pending and uncertain outcomes retain their trusted
task reporting. Model-generated action text remains unused for execution outcomes.

For a recognized single lighting request with no validated proposal, generated prose
is replaced by a trusted no-submission explanation, regardless of its wording or tense.
Common unsolicited lighting completion claims in other replies are also suppressed.
The older rejection diagnostic remains fail-closed. This conservative matching is not
a universal natural-language intent parser or proof of arbitrary text's factual truth;
other languages, indirect command phrasing and future capability domains need explicit
extensions and acceptance tests. Explanatory questions are not commands.

Replay, authenticated history and model history apply the same safe presentation to
older unsubmitted misleading turns. Original stored evidence, identifiers, source
dependencies and provenance remain intact; access and revocation are still rechecked.
An accepted action with a missing task link may already have effects and is **never**
projected as unsubmitted. No database migration or execution retry is introduced.
Voice synthesis consumes the coordinator's trusted response, preserving the same result.

The one-shot voice process test previously assumed its initial response must already
be completed. The scheduler can claim a submitted task before the foreground runner,
which correctly returns pending `task_busy`. The test now verifies completion by
replaying the same request ID and checks unchanged task/correlation IDs and exactly
one handler call. A deterministic held-claim regression covers that pending-to-completed
path. Production scheduling, claims and deadlines are unchanged.

## Structured output configuration

Both direct chat and the runtime's real composition root accept:

```sh
JARVIS_LOCAL_MODEL_RESPONSE_FORMAT=json_schema \
  uv run jarvis chat --local-model qwen3:4b-instruct
JARVIS_LOCAL_MODEL_RESPONSE_FORMAT=json_schema \
  uv run jarvis serve --config /absolute/path/to/development-config.json
```

The runtime environment overrides its JSON configuration. A saved runtime config may
also set `local_model_response_format` to `json_schema`. `json_object` remains the default.
Neither mode bypasses the strict decoder, authorizes model prose or grants cloud egress.
The endpoint is local `http://127.0.0.1:11434/v1`; select the installed model with
`JARVIS_LOCAL_MODEL_NAME=qwen3:4b-instruct` for serve. Availability/timeouts are explicit.
Protocol tests exercise the actual runtime factory and a separate chat CLI process
for both modes; mocked HTTP responses are clearly separate from genuine inference.

## Project context and latency

Existing bounded authorized retrieval already prioritizes named project terms,
supports project-only follow-ups and rechecks history sources. The exact standalone
“How could audio codecs help Slipstream?” request is included in real acceptance.
Synthetic partial project evidence has provenance and confidence 0.3; development
microcontroller fixtures remain excluded from engineering context. These tests do
not migrate fixtures into genuine project facts or create authoritative decisions.

This cloud host runs Qwen3 4B Q4_K_M on four CPU threads, with no GPU. Warm baseline
requests spent approximately 99% of model-call time in Ollama prompt evaluation and
token generation, while authorized context building took 77–152 ms. Measured prompt
evaluation took 22–36 seconds and generation 21–46 seconds. A cold first request had
additional startup overhead. These are measurements of this host, not a diagnosis of
the unavailable MacBook's hardware. JSON Schema can guide shape but does not guarantee
semantic reliability or improve latency by itself. No truncation, relaxed validation,
unapproved fallback or fabricated timing is used to mask delays.

Run genuine acceptance serially with process integration tests to avoid CPU contention:

```sh
uv run python scripts/validate_local_cognition.py --scope mixed --partial-context \
  --response-format json_schema --repeats 3 --timeout 300 \
  --output-dir /tmp/jarvis-mixed-schema-new
```

Use a new output directory and repeat with `json_object`. This creates a separate local
development store, private credentials and owned ephemeral runtime port; it never uses
an existing user's store. Reports contain validated public synthetic fixture responses
and allowlisted numeric diagnostics, never private prompts, raw provider output or tokens.
Combined requests intentionally bypass inference: their clarification is a coordinator
result, not evidence of Qwen successfully reasoning about mixed intentions.

## Actual local-model acceptance

The [sanitized measured report](reports/phase10-mixed-intent.json) contains the real
authenticated API responses, provenance, audit references, task outcomes and numeric
backend profiles. Post-fix runs used separate development stores for each mode.

| Check | `json_schema` | `json_object` |
|---|---:|---:|
| Accepted genuine model responses | 7/7 | 7/7 |
| Direct codec explanations | 3/3 | 3/3 |
| Separate lighting actions, brightness 45 | 3/3 | 3/3 |
| Same-request replay, unchanged result | 3/3 | 3/3 |
| Owner combined requests safely deferred | 3/3 | 3/3 |
| Guest combined request safely deferred | 1/1 | 1/1 |
| Authorized partial-project reasoning | 1/1 | 1/1 |

All eight combined requests had no inference, task, execution or memory dependency.
Each store recorded exactly three lighting handler calls after all replays. Both
project responses cited `acceptance:partial-project-scenario`, not a development
controller fixture. The synthetic stored scenario and recommendations are not verified
facts about the operator's real project. Model wording may omit provenance detail;
use the returned references and stored records rather than treating prose as authority.

Both modes had 7/7 accepted outputs in these finite runs, so a reliability advantage
for JSON Schema was **not demonstrated**. It used about 600 fewer input tokens because
JSON-object mode includes the schema in prompt text. Observed mean end-to-end inference
requests were 71.5 seconds (schema) versus 83.6 seconds (object); ranges were 52.7–87.5
and 72.7–96.0 seconds respectively. This is an observed workload comparison, not a
general performance guarantee. Context building took 69–106 ms. Native prompt
evaluation took 27.2–50.6 seconds and generation 24.8–55.5 seconds, accounting for
approximately 99.5% of model-call time. Accepted outputs did not hit the adapter's
truncation rejection. The default output mode and timeout remain unchanged.

Actual completed tasks below all returned `{"brightness":45,"simulated":true}`:

| Mode | Round | Task ID |
|---|---:|---|
| Schema | 1 | `34d530f6-a0dd-458f-badd-ed46c24cbd51` |
| Schema | 2 | `aa73a9e4-cf98-4ede-8daf-a1aca773ce65` |
| Schema | 3 | `ae2c35f0-0abb-415d-9ed7-10282d66feb7` |
| Object | 1 | `ea196136-49b6-41ff-a15e-46f3ff286c80` |
| Object | 2 | `5b18273b-b580-4522-9388-35b9b11393bd` |
| Object | 3 | `b9d57a3d-8580-473c-8e6d-b0736de0e599` |

The real chat CLI with local Qwen configured also returned the trusted combined-request
clarification with `task_id`, `execution` and `provenance` all null. That CLI check
intentionally did not invoke the model. Separately, an installed wheel's explicit fake
chat mode deferred the combined request and executed the separate simulated action at
45; it is packaging/offline verification, not genuine AI inference.

Example of the actual combined response's public fields:

```json
{"kind":"clarification","text":"This contains multiple requests. No action was submitted or executed for this request. Please send the question and each action separately.","task_id":null,"execution":null,"provenance":null}
```

Files in this follow-on: new `src/jarvis/action_reporting.py`,
`tests/test_mixed_intent.py`, this guide and its JSON report; modified cognition and
conversation services, `scripts/validate_local_cognition.py`, cognition/hardening/chat/
audio tests, README, ARCHITECTURE, REQUIREMENTS, ROADMAP, FOUNDATION, the earlier
hardening guide and the local-model setup guide. No runtime port default, permission
policy, executor, task/claim implementation, schema, dependency or lockfile was changed.

## Coverage and remaining work

COG-001, provisional COG-002/INT-002, CAP-003, IAM-004, PER-001, TASK-001/002,
MEM-002/003/006, SEC-001–003, REL-001/002 and DEV-003 gain or retain regression coverage.
Tests cover misleading direct prose, separation, denied execution, one-time replay,
historical projection without evidence mutation, restart, isolation, spoken responses
and both structured modes. Existing approval, revocation, recovery and cloud-policy
tests continue to enforce their boundaries. No requirement or ADR status is promoted.

Baseline: 931 passed in 396.68 seconds. Final affected run: 69 passed in 43.46 seconds.
Full regression: **961 passed in 428.55 seconds**, with one upstream Starlette/AnyIO
deprecation warning. Ruff lint and formatting passed (159 files); docs-check validated
44 Markdown files, 139 internal links, three generated references and 25 API paths.
Source distribution/wheel build and an isolated wheel installation succeeded.
All eleven demonstrations, including runtime, cognition, voice and ambient, passed
from both the checkout and the installed wheel (22 executed demonstrations). The
installed reporting/coordinator/conversation modules matched the tested checkout.

The next operator gate is repeated MacBook Qwen and physical voice acceptance with
this reporting boundary. Broader intent planning and real device integrations remain
deferred; a model's successful JSON response alone is insufficient acceptance.
