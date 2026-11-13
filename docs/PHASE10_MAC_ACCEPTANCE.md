# Phase 10 MacBook acceptance — 2026-11-13

## Final repair and preserved migration run

**Recommendation: NO-GO for closing Phase 10.** Playback lifecycle, the complete
speech harness and preserved schema migration now pass. Human audibility and a
physical microphone → Whisper → Qwen → authorized result → audible response have
not passed. The preserved runtime also has an expired worker session; no new
credential has been issued without the requested owner decision.

Local generated measurements separate real speech inference, physical device
lifecycle, simulated capabilities and failures. Generated JSON, recordings, stores
and credentials are excluded from Git; this document retains the reviewed summary.
Earlier evidence below is retained as history, not the current implementation state.

| Gate | Final observed result |
| --- | --- |
| Speaker stop/close, repeat, cancellation and reuse | Passed device lifecycle; audibility unconfirmed |
| Complete real-speech harness | Passed, real Whisper and say; fake cognition |
| Preserved schema 9 → 10 migration | Passed |
| Existing runtime authority/database/model | Passed doctor checks |
| Existing runtime worker readiness | Degraded: preserved worker session expired |
| Physical acoustic round trip | Failed recognition with `stt_no_speech` |
| Human spoken request and audible response | Unverified |

### Playback correction and hardware evidence

Installed sounddevice 0.5.6 uses a PortAudio 19.7.0 development build. The previous
`RawOutputStream` context manager called `stop()` and then `close()`. The saved
native sample proves the hang was in `Pa_StopStream` / `FinishStoppingStream` →
`AudioOutputUnitStop`, before close. CoreAudio device-stop and PortAudio's native
`startStopCallback` were waiting on native mutexes. Python's write worker was idle.
The sample does not prove a Python write-thread bug or identify an upstream fix.

A bounded real-device probe using callback completion reached inactive, stop and
close successfully. Playback now supplies samples through a callback, pads the final
buffer and returns `CallbackStop` to drain naturally before stop/close. Cancellation
returns `CallbackAbort`; a single owner performs start, inactive wait, stop/abort and
close with errors enabled. No device-control APIs or filesystem work run inside
the audio callback. This follows PortAudio's documented
[completion and abort lifecycle](https://portaudio.com/docs/v19-doxydocs/start_stop_abort.html).

Because Python cannot safely interrupt a thread stuck inside a native driver,
the adapter owns a small playback child process. It passes bounded WAV on stdin,
uses a private cancellation marker, and waits for the child after full cleanup.
Normal operation has an audio-duration-plus-10-second deadline; cooperative
cancellation has a three-second cleanup allowance. A hung child is killed and
reaped and reports `playback_device_timeout` or `playback_cleanup_timeout`, never
success. Normal hardware acceptance used no forced termination. No execution,
authority, model, transport or persistent contract moved into this worker.

Real built-in speaker tests completed three times in 2285, 2262 and 2296 ms.
Cancellation after 1.5 seconds returned `playback_cancelled` in 1668 ms with child
exit 2; a subsequent playback exited 0. All five owned children were reaped.
These are successful device operations, not human confirmation of audibility.
The one-shot voice client also now treats closed stdin as EOF rather than Enter,
preventing false playback cancellation in file-driven clients.

Fake-backend regressions cover drain/padding, stop/close order and thread ownership,
repeated playback, cancellation, underflow and open/stop/close failures. Owned-child
tests cover success, device error, cooperative/task cancellation, cancellation
during launch, and native cleanup that hangs: that child is reaped and the call
fails explicitly. The existing core test still verifies cancellation preserves the
completed task and same-ID replay invokes no second lighting effect.

### Speech harness repair and complete rerun

The nonexistent-voice result was an implementation defect: macOS `say` silently
fell back instead of honoring the explicitly selected installed voice. The adapter
now enumerates local voices with `say -v '?'` and requires an exact installed name.
Unknown names fail with `tts_voice_unavailable` before synthesis. Tests include
multiword voice names and reject prefixes/locale strings. Linux continues to use
its provider's explicit failed-exit behavior; no failure assertion became success.

The memory-recall failure was an outdated probe assumption. The current intended
fixture filter excludes development facts from ordinary project reasoning and
admits them for explicit recall. The spoken fixture now asks “What do you remember
about Slipstream?” rather than “Continue with Slipstream.” Runtime filtering and
memory permissions are unchanged; the original memory-grounded assertions remain.
The probe saves partial results with an incomplete stage before later assertions.

Executed the full repaired harness with both pinned tiny.en and base.en models,
installed Samantha, and a fresh private temporary store. It passed 16 STT cases
(12 recognized speech and four expected no-speech rejections), nine TTS/input
cases, STT/TTS deadlines, nonexistent voice, corrupt-model rejection, cancellation
and child reaping. Authenticated audio-file integration passed owner recall,
completed simulated brightness 25, guest unsupported/no-handler behavior, private
history isolation, restart with three history turns, and spoken pending `READY`
without premature capability execution. Cognition here was explicitly fake.

Successful STT samples took 229–600 ms and 213.2–327.3 MiB peak child RSS; successful
TTS samples took 1575–1714 ms and 35.5–35.7 MiB peak child RSS. These are subprocess
measurements including startup, not a live voice latency guarantee. Quiet/noisy
tiny-model recognition errors remain recorded; transcripts were not repaired.

### Preserved migration acceptance

The exact configured database resolved to
`.jarvis/dev/runtime/journal.sqlite3` within the repository checkout.
No open database/WAL/runtime-lock descriptors or runtime-port listener were present;
no writer needed termination. The migration procedure held the existing runtime
claim to exclude server startup. The supported path was 9 → 10, whose migration
statement list is empty: this is the existing reader/writer compatibility barrier.

A new private online backup was created at
`.jarvis/backups/phase10-before-schema10-20261113T223736405877Z.sqlite3`.
Live and backup integrity and foreign keys passed. Canonical sorted row digests,
counts and schema definitions matched before migration. Explicit
`SQLiteDatabase.initialize()` then performed the documented migration, followed
by `verify()` and another complete content comparison. All **42 tables and 68,284
rows** matched, including authority, principals, grants, credential bindings,
sessions, memory, events/audits, tasks/history and conversations/turns. All seven
existing credential/configuration JSON files retained their byte hashes. Only
`user_version` changed from 9 to 10; there was no reset, rebootstrap or grant.

`jarvis db-verify` passes at schema 10. Doctor now reports database, authority and
local model available. Its separate worker failure was diagnosed read-only: the
preserved binding exists, is worker-kind and unrevoked, but its session expired.
Expiry enforcement was not bypassed. Raw credential material and preservation
digests remain private; the full backup/evidence files were not published.

### Physical acceptance limitation and final verification

An attempted generated acoustic loop used the actual built-in speakers and microphone.
The nine-second capture contained 142,400 PCM frames, 141,897 nonzero samples and
peak magnitude 2813. Whisper returned `stt_no_speech` for the public question.
Device enumeration still selected the built-in microphone/speakers; volume
inspection reported output 50, input 50, output unmuted. No OS permission denial
was observed. This establishes a recognition failure on captured signal, not its
acoustic cause. Echo suppression, physical audibility and live speaker placement
were not established and must not be guessed. That captured audio was not sent
to Qwen after recognition failed and remains private outside Git.

The required live speech and audibility checks remain pending operator participation.
No physical end-to-end success is claimed from generated audio files, simulated
lighting or successful device close. Phase 11 has not begun.

Separately, a fresh isolated runtime passed the real `voice --wav` client with
Whisper base.en, Qwen3 4B JSON Schema, current policy/tasks, Samantha and physical
speaker playback. Public generated inputs exercised a conversational codec question,
a mixed Opus question/brightness-45 action, and unsupported Slack plus an independent
question. The mixed task was independently read through the task API as COMPLETED
with simulated output 45. Enter during its response playback produced the expected
cancelled result. Same-ID HTTP replay was identical and left the invocation count
unchanged. The entire series invoked simulated lighting exactly once; unsupported
Slack invoked no handler. Later response playback succeeded after cancellation.
Total client times were 60.05, 34.29 and 49.95 seconds; these include recognition,
model, synthesis and playback, and are not isolated model benchmarks. Focused tests
overlapped this series. Source audio was generated directly to WAV: this passes
the integrated software/device-output route, **not physical microphone input or
human-confirmed audible output**. Evidence labels this distinction explicitly.

Full `uv run --no-sync pytest -q`: **1020 passed**, one upstream Starlette/AnyIO
deprecation warning, **386.84 seconds**, no skips. Repository-wide Ruff lint and
formatting pass (165 files). Final focused audio/runtime/storage/multi-intent
regressions: **209 passed**, one upstream warning, **125.32 seconds**. Documentation
checks passed before publication cleanup: 46 Markdown files, 152 internal links,
three generated references and 25 OpenAPI paths. Publication checks are recorded below.

Publication verification: 12 playback regression tests passed in 3.95 seconds;
Ruff lint and formatting passed (165 files); docs-check passed with 46 Markdown
files, 149 internal links, three generated references and 25 OpenAPI paths.
Application code is unchanged from the full 1020-test and focused 209-test runs.

Requirements: INT-001, provisional INT-002, ARCH-003/004, PER-001–003, IAM-004/007,
TASK-001/002, MEM-002/003/006, SEC-001–004, REL-001/002 and DEV-003. Constraints remain
provisional ADR-T10/T11/T12; no architectural or status decision is promoted.
Changed implementation: `speech.py`, new private `audio_playback.py`, `voice_cli.py`
and `validate_real_audio.py`; regression coverage: `test_audio.py` and new
`test_audio_playback.py`; documentation: this report and voice checklist. Generated
measurement JSON remains local. No local configuration, credentials, recordings or weights
are committed.

Next gate: obtain the explicit worker-credential decision and live audible speech
participation, then complete physical conversational, mixed-action, cancellation,
replay and unsupported-action acceptance. Until then, retain NO-GO.

## Initial acceptance — retained history

**Phase 10 is not ready to close.** Genuine local Qwen inference and physical
microphone capture passed. Speaker shutdown hung, and the existing real-audio
probe failed two acceptance assertions. No application code, authority, credentials,
grants, tasks or memory in the existing development store were changed.

Baseline: branch `codex/jarvis-phase-10`, commit `cdd9eb5`. Host: macOS 26.5.2,
arm64; Python 3.12.15. Measurements below are observations, not performance targets.
The local inference measurements contain only public synthetic conversations and
test task identifiers. Local speech measurements exclude local paths. These generated
artifacts are excluded from publication; the findings are summarized below.

## Existing development runtime

`uv run --no-sync jarvis doctor --config .jarvis/dev/runtime.json` exited 1:

```json
{"database":false,"authority":false,"model_available":true,"workers":false,"errors":["storage_authority_or_worker_configuration_unavailable","database_version_unsupported"],"status":"degraded"}
```

Read-only SQLite inspection found `user_version=9`; this checkout requires 10.
`PRAGMA integrity_check` returned `ok`; `foreign_key_check` returned zero violations.
Doctor's false authority/worker flags do not establish corrupt authority or invalid
credentials: schema validation prevented that stage from completing. No credentials
were printed or reissued. No bootstrap, migration, restore, grant or reset was run
against this store. Existing-store startup remains unverified beyond this failure.

The documented next storage step is a verified new online backup, quiesced writers,
explicit schema migration and verification under the
[backup procedure](operations/backup-restore.md). This acceptance run leaves that
production-like data unchanged as requested. Do not bypass the version check.

The sandbox initially blocked uv's cache and loopback access. Retrying with approved
host access succeeded; those initial failures were sandbox restrictions, not Ollama
or microphone failures.

## Genuine local inference and simulated effects

Ollama advertised one installed model, `qwen3:4b-instruct`, ID `0edcdef34593`,
4.0B Q4_K_M, approximately 2.5 GB on disk. No model was downloaded. `ollama ps`
reported 100% GPU, 4096 active context and 3.2 GB resident model size. The advertised
model maximum context was 262144; that is not the active context used here.
No Gemma model was installed or tested.

Executed the existing harness against a new private temporary store:

```sh
uv run --no-sync python scripts/validate_local_cognition.py \
  --scope orchestration --partial-context --response-format json_schema \
  --repeats 3 --timeout 300 --output-dir /private/tmp/jarvis-mac-acceptance-20261113-cognition
```

All eight requests returned useful structured responses. The three mixed technical
questions/actions produced valid plans, cited authorized synthetic project context
and completed brightness-45 tasks. Same-ID replay returned identical responses
three times without additional lighting invocations. Two ordered actions completed
25 then 45, for five actual simulated lighting handler invocations overall.
Guest and unsupported Slack requests preserved their independent questions and
submitted no lighting task; invocation counts stayed unchanged. These are real
SQLite tasks and policy/executor outcomes against a **simulated lighting adapter**,
not physical smart-home control.

Semantic review: the mixed responses proposed Opus for low-latency communication,
FLAC for lossless diagnostics and AAC for compact recordings, explicitly treating
them as options rather than established Slipstream facts. Capability discovery
correctly identified simulated adapters and unavailable external integrations.
The two-question answer loosely described codecs as compressing analog sound into
digital data; codecs generally encode/decode digital samples, so factual precision
is imperfect despite schema success. The 106-word two-question response also exceeded
the advisory voice word target. No claim of universal factual accuracy is made.

| Mixed-request measurement | Observed range |
| --- | ---: |
| Model invocation | 21.431–66.953 s |
| Context construction | 59.2–139.5 ms |
| Plan validation | 1.56–7.26 ms |
| Orchestration | 143.9–291.3 ms |
| Entire request | 21.711–67.517 s |
| Input/output tokens | 1730–1732 / 299–309 |
| Generation schema | 14081 bytes |

The first mixed request was slower than the 60-second default. The explicit
300-second acceptance timeout was used; defaults were not changed. Inference
dominates measured latency. Native prefill/decode timing was not exposed by this
OpenAI-compatible endpoint. `/usr/bin/time -l` measured 201.56 s for the complete
harness and maximum RSS 73728000 bytes (70.31 MiB); this excludes the independently
running Ollama service and is not total system memory. Ollama's 3.2 GB figure is a
separate resident-model observation. The initial inference series overlapped speech
probes, so its timing is not an isolated hardware benchmark. The capability-awareness
record carries previous plan metrics from the status snapshot; those values must
not be interpreted as a new plan-validation sample.

## Real speech and physical hardware

Whisper source pin and tiny.en/base.en checksums passed the existing probe checks.
Actual `local.whisper-cpp` inference ran on public JFK and generated-command WAVs.
Independent repeated measurements persisted 16 STT cases: 12 speech transcriptions
and four expected `stt_no_speech` rejections for silence/seeded noise. Both models
transcribed “Please adjust the lights to 25.” correctly, including the standalone
`jarvis audio stt-test` CLI. Tiny lost the final word on quiet JFK and changed
“Americans” to “American” under noise; base retained the reference wording.

Across these isolated speech subprocess samples, wall time was 218–854 ms and
peak child RSS 203.6–326.5 MiB. These include process/model startup; they do not
establish live acoustic accuracy. Actual macOS `local.say` synthesized at requested
rates 80, 175 and 350, with approximately 0.92–1.95 s wall time and 35.6–35.9 MiB
peak child RSS. Audio was mono 16 kHz PCM16. Rate selection was exercised, without
claiming calibrated spoken words per minute. Real STT and TTS cancellation killed
and reaped their owned subprocesses.

Device enumeration exposed the built-in microphone and speakers. The explicitly
bounded five-second `mic-test` on the built-in microphone succeeded, measured input
levels and returned `microphone: OFF` (4600 ms captured). Samples were discarded.
No microphone OS permission denial occurred. This verifies capture, not recognition
of a particular live utterance, speaker identity or ambient understanding.

Speaker `tts-test` at rate 200 on the built-in speakers hung. A one-second process
sample located the block in PortAudio `FinishStoppingStream` →
`AudioOutputUnitStop` → CoreAudio `HALC_ProxyIOContext::StopIOProc` → mutex wait.
The callback thread was also waiting within CoreAudio. This is an observed device
shutdown deadlock, not evidence of a missing microphone permission. SIGINT did not
release it; SIGTERM ended only the verified acceptance process (exit 143).
Audibility has not been confirmed. Successful synthesis is not speaker acceptance.
The stack sample remains private in `/private/tmp/jarvis-playback-sample.txt`.

## Failed probe assertions and remaining gates

The unchanged `scripts/validate_real_audio.py` exited 1 when its nonexistent-voice
probe expected `speech_provider_failed`. On this macOS installation, `say` instead
returned valid audio for `jarvis-invalid-voice`. The selected voice is therefore
not verified by successful synthesis; exact offline voice validation needs a
correction. The assertion was not relaxed. This early failure also prevented the
main probe from writing its final report and running its later corrupt-model check.
Independent measurement saved this behavior explicitly rather than claiming a pass.

Separately invoking the remaining audio-file pipeline also exited 1: it expected
two `memory_grounded` turns for “Continue with Slipstream.” Its legacy development
memory exists, but current `fixture_recall` rules exclude it for this phrase.
Actual outcomes were `clarification`, completed simulated lighting 25, `direct`,
then guest `unsupported`. History length/isolation and one lighting invocation
passed before the assertion. Restart and pending-speech checks later in that
function were not reached. The fixture should explicitly ask for recall under
current context rules; runtime privacy filtering must remain unchanged.

Still unverified: a live microphone → real STT → Qwen → authorized core → real
TTS → audibly confirmed speaker round trip; physical playback cancellation and
device release; the full repaired speech probe including corrupt-model, restart
and pending branches; existing-store startup after an explicit preserved migration.
Gemma, physical lighting, Slack, real diarization, localization and continuous
ambient intelligence were not tested. No simulated result substitutes for them.

## Regression and disposition

Focused regression command:

```sh
uv run --no-sync pytest -q tests/test_audio.py tests/test_runtime.py \
  tests/test_security_storage.py tests/test_multi_intent.py tests/test_runtime_local_model.py
```

**195 passed, one upstream Starlette/AnyIO deprecation warning, 102.32 seconds.**
No application correction was made during acceptance, and no failing hardware
assertion was changed to pass. This is focused coverage, not a new full-suite run.

Requirements exercised: ARCH-001/003/004, COG-001, provisional COG-002/INT-002,
INT-001, IAM-004/007, PER-001–003, CAP-001–003, TASK-001/002, MEM-002/003/006,
SEC-001–004, REL-001/002 and DEV-003. Existing provisional ADR-T10/T11/T12 remain
unchanged; no OPEN decision or proposal is promoted. The voice checklist links
this written report; generated measurement JSON is retained locally. No
credentials, private utterances, weights or local configuration are included.

Next work: correct exact-voice validation and probe fixture/report durability,
resolve the CoreAudio shutdown hang with bounded cleanup, rerun focused regression
and real speech acceptance, perform the documented preserved store upgrade, then
complete a live Qwen voice round trip with human audibility confirmation.
