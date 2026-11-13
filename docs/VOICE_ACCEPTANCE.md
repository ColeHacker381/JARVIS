# Phase 10 real speech validation and MacBook acceptance

The [2026-11-13 MacBook acceptance report](PHASE10_MAC_ACCEPTANCE.md) records
genuine Qwen/Whisper/say execution, repaired speaker lifecycle and speech-harness
acceptance, and a preserved schema-10 migration. The physical audible round trip
and an expired existing worker session remain open. The cloud results and original
operator procedure below are retained as historical evidence.

**Real-audio acceptance is pending.** Linux CPU STT/TTS and authenticated
audio-file integration were exercised with actual binaries and verified weights.
Cognition in those tests was explicitly fake. The cloud container has no audio
devices, no running Ollama instance and no access to the user's MacBook files,
Gemma tag, microphone, speakers or development credentials. No Gemma inference,
physical capture/playback or macOS `say` execution is claimed here.

This guide is an operator checklist. Run commands from the JARVIS checkout, in
your own terminals. It does not enroll devices, issue credentials or change grants
automatically. Review each explicit permission grant. Keep cloud routing disabled.
Architectural boundaries are in [Phase 10](PHASE10.md); model configuration is in
[local models](getting-started/local-models.md).

## Reproduce real speech measurements

Install dependencies with the project's locked workflow:

```sh
uv sync --locked --extra voice
```

On macOS, install PortAudio and CMake using your chosen package manager; Homebrew:

```sh
brew install portaudio cmake
say -v '?'
```

On Debian/Ubuntu with an operator-managed system install:

```sh
sudo apt-get update
sudo apt-get install libportaudio2 espeak-ng cmake build-essential
espeak-ng --voices=en
```

The tested cloud container uses `bash scripts/install_audio_linux.sh` to extract Debian packages under
`/workspace/jarvis-audio/sysroot` because its system directories are read-only.
APT verified the signed Debian 13 index and package checksums. No TLS/signature
verification was disabled. The saved cloud setup describes that installation.
For that workspace-local install, set these variables only in the process using
the speech tools (ordinary system installs do not require them):

```sh
export PATH=/workspace/jarvis-audio/sysroot/usr/bin:$PATH
export LD_LIBRARY_PATH=/workspace/jarvis-audio/sysroot/usr/lib/x86_64-linux-gnu
export LIBRARY_PATH=/workspace/jarvis-audio/sysroot/usr/lib/x86_64-linux-gnu
export ESPEAK_DATA_PATH=/workspace/jarvis-audio/sysroot/usr/lib/x86_64-linux-gnu
```

The local PortAudio development symlink and `LIBRARY_PATH` are needed for Python
library discovery; `LD_LIBRARY_PATH` alone is insufficient in this container.
Actual discovery succeeds with those paths and reports no available devices.

Build whisper.cpp **v1.8.2**, exact source commit
`4979e04f5dcaccb36057e059bbaed8a2f5288315`, outside the checkout:

```sh
uv run --no-sync bash scripts/install_whisper.sh "$HOME/jarvis-audio/whisper.cpp"
# Optional explicit comparison model; tiny.en remains available and is not replaced:
uv run --no-sync bash scripts/install_whisper.sh "$HOME/jarvis-audio/whisper.cpp" base.en
```

The script checks the source pin, refuses modified tracked sources, builds with
two jobs and disables whisper.cpp's download client. Model files are downloaded
over verified HTTPS, checked before atomic installation, then checked again when
reused. Existing mismatched artifacts are left unchanged. Weights are never
committed. For this checkout Python 3.12+ is supplied through `uv run`.

| Model file under `~/jarvis-audio/whisper.cpp/models` | Bytes | SHA-256 |
| --- | ---: | --- |
| `ggml-tiny.en.bin` | 77,704,715 | `921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f` |
| `ggml-base.en.bin` | 147,964,211 | `a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002` |

Licenses reviewed: [whisper.cpp MIT](https://github.com/ggml-org/whisper.cpp/blob/v1.8.2/LICENSE),
[converted model card MIT](https://huggingface.co/ggerganov/whisper.cpp),
[OpenAI Whisper MIT](https://github.com/openai/whisper/blob/main/LICENSE),
[eSpeak NG GPLv3](https://github.com/espeak-ng/espeak-ng/blob/1.52.0/COPYING).
System voices follow the OS/vendor terms; model choice/licensing remains an
operator responsibility. The public human sample is the pinned upstream
`samples/jfk.wav`, clipped to 10 seconds; generated commands use real local TTS.
Quiet/noise variants are deterministic transformations, not new human recordings.
The measurement report records fixture/source hashes and ground truth.

Linux real probe (choose a **new** output directory outside Git):

```sh
uv run --no-sync python scripts/validate_real_audio.py \
  --whisper-root "$HOME/jarvis-audio/whisper.cpp" \
  --models "$HOME/jarvis-audio/whisper.cpp/models/ggml-tiny.en.bin" \
           "$HOME/jarvis-audio/whisper.cpp/models/ggml-base.en.bin" \
  --output "$HOME/jarvis-audio/validation-linux-1"
```

On macOS add `--tts-voice` with an exact installed voice from `say -v '?'`.
The probe uses actual STT/TTS, explicitly fake cognition, new private development
stores/credentials and owned ephemeral runtime ports. It never accesses hardware
or the operator's database. Recognition changes can fail the pipeline assertions:
that is evidence to investigate, not a reason to relax checks. Results already
measured remain in `report.json`. Reports include transcripts and local paths;
use only public/generated fixtures in this probe. The probe's local output also
contains private test credential files: do not publish the directory wholesale.

## MacBook hardware checklist

1. Complete the installation above. List device indices; select your actual
   microphone and speakers by name, input/output channel counts and index:

   ```sh
   uv run --no-sync jarvis audio devices
   printf 'Input device index: '; IFS= read -r JARVIS_INPUT_DEVICE
   printf 'Output device index: '; IFS= read -r JARVIS_OUTPUT_DEVICE
   printf 'Installed say voice name: '; IFS= read -r JARVIS_TTS_VOICE
   export JARVIS_INPUT_DEVICE JARVIS_OUTPUT_DEVICE JARVIS_TTS_VOICE
   ```

   No index is assumed. Bluetooth/device changes may change indices; list again
   after reconnecting. Prefer headphones to avoid feedback during initial testing.

2. With all participants' consent, test capture in the terminal you will use:

   ```sh
   uv run --no-sync jarvis audio mic-test --device "$JARVIS_INPUT_DEVICE" \
     --seconds 5 --consent-ref consent:explicit-local-user
   ```

   macOS may prompt for microphone access. In System Settings → Privacy &
   Security → Microphone, enable the actual hosting app (Terminal, iTerm or editor),
   then reopen that app if needed. Expect visible input levels, a bounded duration
   and `microphone: OFF`. This command discards captured samples; it does not save
   a WAV or prove STT quality. `microphone_unavailable_or_permission_denied` means
   investigate OS permission/device selection before proceeding.

3. Test real STT on a non-sensitive <=10-second mono 16 kHz PCM16 WAV. The optional
   probe above generates such files, including `clear_jfk.wav`:

   ```sh
   uv run --no-sync python scripts/validate_real_audio.py \
     --whisper-root "$HOME/jarvis-audio/whisper.cpp" \
     --models "$HOME/jarvis-audio/whisper.cpp/models/ggml-tiny.en.bin" \
     --tts-voice "$JARVIS_TTS_VOICE" \
     --output "$HOME/jarvis-audio/validation-mac-1"
   uv run --no-sync jarvis audio stt-test \
     --wav "$HOME/jarvis-audio/validation-mac-1/clear_jfk.wav" \
     --stt-binary "$HOME/jarvis-audio/whisper.cpp/build/bin/whisper-cli" \
     --stt-model "$HOME/jarvis-audio/whisper.cpp/models/ggml-tiny.en.bin"
   ```

   The probe has strict transcript/outcome assertions; a different system voice
   may fail them. Inspect its measured report and do not call a failed assertion
   successful. If you have not run the probe, substitute your existing correctly formatted
   WAV. Expect `local.whisper-cpp`, the selected model, `simulated: false`, actual
   transcript and processing time. Review proper nouns/numbers carefully.

4. Test synthesis without hardware first, then actual speaker output:

   ```sh
   uv run --no-sync jarvis audio tts-test --text 'JARVIS local speech test.' \
     --tts-voice "$JARVIS_TTS_VOICE" --tts-rate 175 --no-playback
   uv run --no-sync jarvis audio tts-test --text 'JARVIS local speech test.' \
     --tts-voice "$JARVIS_TTS_VOICE" --tts-rate 175 \
     --output-device "$JARVIS_OUTPUT_DEVICE"
   ```

   Verify the first generates audio and the second is audible through the chosen
   output. Test a different allowed rate (80–350). Real MacBook execution remains
   unverified in cloud; report `say`/WAV/device failures rather than selecting fake
   speech. Ctrl-C cancels owned work; Enter-to-cancel is supported during `voice`.

## Existing Gemma and safe runtime migration

The inspected cloud settings use fake cognition and the example name `qwen2.5:3b`.
They are not evidence of the MacBook's configuration. Inspect your existing
configuration and environment overrides before changing anything:

```sh
export JARVIS_RUNTIME_CONFIG=.jarvis/dev/runtime.json
uv run --no-sync python - <<'PY'
import os
from pathlib import Path
from jarvis.runtime_config import RuntimeSettings
s = RuntimeSettings.load(Path(os.environ['JARVIS_RUNTIME_CONFIG']))
print({'model_provider': s.model_provider, 'local_model_name': s.local_model_name,
       'local_model_endpoint': s.local_model_endpoint, 'runtime_port': s.runtime_port})
PY
ollama list
curl --fail-with-body http://127.0.0.1:11434/v1/models
printf 'Exact already-installed local Gemma tag: '; IFS= read -r JARVIS_GEMMA_MODEL
export JARVIS_GEMMA_MODEL
ollama show "$JARVIS_GEMMA_MODEL"
ollama show "$JARVIS_GEMMA_MODEL" --license
```

Use your exact existing local Gemma tag, including its suffix. **Do not pull a new
Gemma or Qwen model.** If the Ollama desktop app already runs, leave it running;
otherwise run `ollama serve` in its own terminal. Select locally downloaded
weights, not a cloud model tag. Ollama's official
[OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility) documents
`/v1/models`, `/v1/chat/completions` and JSON response mode used by this adapter.
Advertised availability is not proof that Gemma produces valid JARVIS decisions.

For an existing Phase 9/schema-8 database, first stop **your** JARVIS server in
its terminal with Ctrl-C and wait for shutdown. Do not run `runtime-init` on that
store, delete it, reuse demo fixtures or kill unrelated listeners. Resolve the
configured store, back it up to a new private destination, explicitly migrate,
then verify:

```sh
JARVIS_RUNTIME_DATABASE="$(uv run --no-sync python - <<'PY'
import os
from pathlib import Path
from jarvis.runtime_config import RuntimeSettings
print(RuntimeSettings.load(Path(os.environ['JARVIS_RUNTIME_CONFIG'])).resolved_runtime_database_path.resolve())
PY
)"
export JARVIS_RUNTIME_DATABASE
mkdir -p -m 700 .jarvis/backups
uv run --no-sync jarvis db-backup --database "$JARVIS_RUNTIME_DATABASE" \
  --destination ".jarvis/backups/before-audio-$(date +%Y%m%d-%H%M%S).sqlite3"
uv run --no-sync jarvis db-init --database "$JARVIS_RUNTIME_DATABASE"
uv run --no-sync jarvis db-verify --database "$JARVIS_RUNTIME_DATABASE"
```

Schema 9 adds only audio source/session metadata. Migration tests preserve
credential hashes, revocations, grants and existing tables; migration enrolls no
microphones and grants nothing. Protect the backup like the live database.
Do not restore schema-8 files over a running schema-9 service.

Inspect/validate the selected existing Gemma without changing fake regression
defaults, then serve with explicit overrides in a dedicated terminal:

```sh
uv run --no-sync jarvis doctor --config "$JARVIS_RUNTIME_CONFIG" \
  --model-provider local --local-endpoint http://127.0.0.1:11434/v1 \
  --local-model "$JARVIS_GEMMA_MODEL"
uv run --no-sync jarvis serve --config "$JARVIS_RUNTIME_CONFIG" \
  --model-provider local --local-endpoint http://127.0.0.1:11434/v1 \
  --local-model "$JARVIS_GEMMA_MODEL"
```

These commands retain your store, credentials and port. Cloud is disabled and
there is no automatic fallback. If you intentionally override the API port, the
voice client's settings must contain that same port (or `JARVIS_RUNTIME_PORT` must
be set consistently); its `--config` is the authority for the connection address.
Do not start a second JARVIS runtime against the same store. The voice client is
a separate process that connects to the running authenticated Phase 9 API.

For a persistent development configuration, copy the existing file to a **new**
private path and change only `model_provider` to `local`, `local_model_endpoint`
to `http://127.0.0.1:11434/v1`, and `local_model_name` to your exact installed
Gemma tag. Keep database, worker credential and port fields intact. These settings
are validated by the existing `RuntimeSettings`/local adapter; no particular
Gemma tag or successful Gemma decision was verifiable from this cloud container.

## Explicit permissions and authenticated voice

In a client terminal, choose your existing private, current owner client file.
Do not dump it, share it, replace it or grant guest access implicitly:

```sh
export JARVIS_OWNER_CREDENTIAL=.jarvis/dev/credentials/owner.json
JARVIS_OWNER_ID="$(uv run --no-sync python - <<'PY'
import os
from pathlib import Path
from jarvis.runtime_config import RuntimeSettings
from jarvis.storage.sqlite import SQLiteDatabase
from jarvis.local_credentials import LocalDevelopmentIdentityRepository
s = RuntimeSettings.load(Path(os.environ['JARVIS_RUNTIME_CONFIG']))
print(LocalDevelopmentIdentityRepository(SQLiteDatabase(s.resolved_runtime_database_path)).owner_id)
PY
)"
export JARVIS_OWNER_ID
uv run --no-sync jarvis permissions grant --config "$JARVIS_RUNTIME_CONFIG" \
  --credential-file "$JARVIS_OWNER_CREDENTIAL" --principal "$JARVIS_OWNER_ID" \
  --permission audio.sources.manage
uv run --no-sync jarvis permissions grant --config "$JARVIS_RUNTIME_CONFIG" \
  --credential-file "$JARVIS_OWNER_CREDENTIAL" --principal "$JARVIS_OWNER_ID" \
  --permission audio.process
uv run --no-sync jarvis audio register --config "$JARVIS_RUNTIME_CONFIG" \
  --credential-file "$JARVIS_OWNER_CREDENTIAL" --source local.microphone \
  --location computer --consent-ref consent:explicit-local-user
```

These are deliberate owner administration commands. `audio.sources.manage` only
manages metadata; `audio.process` only permits processing owned enabled sources.
Neither grants lighting, memory writes or cloud access. Omit continuous capture
and memory-candidate options. Existing capability permissions remain decisive.
Expired/revoked client credentials require explicit operator provisioning through
the [credentials guide](operations/security.md); no automatic reissue occurs.

Start push-to-talk using actual indices and installed voice:

```sh
uv run --no-sync jarvis voice --config "$JARVIS_RUNTIME_CONFIG" \
  --credential-file "$JARVIS_OWNER_CREDENTIAL" --source local.microphone \
  --device "$JARVIS_INPUT_DEVICE" --output-device "$JARVIS_OUTPUT_DEVICE" \
  --seconds 10 --tts-voice "$JARVIS_TTS_VOICE" --tts-rate 175 \
  --stt-binary "$HOME/jarvis-audio/whisper.cpp/build/bin/whisper-cli" \
  --stt-model "$HOME/jarvis-audio/whisper.cpp/models/ggml-tiny.en.bin"
```

Enter starts capture; Enter stops it. Maximum capture is 10 seconds. Ask about an
**existing authorized** Slipstream memory (none is automatically added to an
existing store), then ask a follow-up. Verify actual transcript, memory citation,
model provenance (`local.openai` and your Gemma tag), audible response and history.
Do not interpret the ability to hear your voice as authentication of every person
near the microphone. Unconsenting background recording is out of scope.

Request “Please adjust the lights to 25” only against the existing simulated
lighting capability. A successful response must show `COMPLETED` and observed
simulated output. Pending work must say it was submitted and carry a task reference,
not claim completion. Invalid model JSON, unknown capability, argument errors and
policy refusal must cause no handler invocation. Real Gemma may need a smaller
context or a different installed local model if it cannot follow the versioned
decision schema; do not weaken validation to accommodate it.

Exit with `/exit`, then rerun the same command plus `--conversation UUID` using the
printed owned ID. Restart your server normally and resume again. Verify permitted
history continuity. Test Enter during playback and Ctrl-C during capture/STT/TTS,
then ensure devices are released. Mute the source while testing revocation:

```sh
uv run --no-sync jarvis audio mute --config "$JARVIS_RUNTIME_CONFIG" \
  --credential-file "$JARVIS_OWNER_CREDENTIAL" --source local.microphone
uv run --no-sync jarvis audio sessions --config "$JARVIS_RUNTIME_CONFIG" \
  --credential-file "$JARVIS_OWNER_CREDENTIAL"
```

Further capture must fail; deliberately register again only to reactivate with
consent. To test denial with a guest credential, an owner must explicitly enroll a
**guest-owned** source and grant only needed processing permission; the owner
microphone remains inaccessible. The offline `voice-demo` already exercises
guest denial without modifying your store or requiring such enrollment.

For model failure handling, stop only your own Ollama server (or quit your own
desktop app), submit another request and expect a structured unavailable/failure
response without any tool success or cloud fallback. Restart Ollama with its
already installed model, then resume the conversation. Do not repeatedly submit
an action whose outcome is unknown; inspect its task and recovery state first.

## Audio-file integration and acceptance record

The same voice client supports a one-shot `--wav` input with real STT/TTS. It
requires the same current client/source/session permissions and authenticated API;
it opens no microphone. Example after explicit source registration:

```sh
uv run --no-sync jarvis voice --config "$JARVIS_RUNTIME_CONFIG" \
  --credential-file "$JARVIS_OWNER_CREDENTIAL" --source local.microphone \
  --wav /private/path/authorized-speech.wav --no-playback \
  --tts-voice "$JARVIS_TTS_VOICE" \
  --stt-binary "$HOME/jarvis-audio/whisper.cpp/build/bin/whisper-cli" \
  --stt-model "$HOME/jarvis-audio/whisper.cpp/models/ggml-tiny.en.bin"
```

It prints actual STT provenance/latency, coordinator text/task/correlation/audit
references and actual TTS format/latency. `--no-playback` explicitly prevents a
hardware claim. `--wav` cannot be combined with `--fake`. Non-speech markers,
digital silence, malformed/truncated audio and invalid TTS text are rejected.
Low-volume/noisy speech can still hallucinate or lose proper nouns/numbers; these
checks are not a reliable real-world VAD or acoustic security boundary.

Record completion per step: OS/architecture, input/output names and indices,
whisper commit/model checksum, installed Gemma tag, transcript versus intent,
actual model provenance, STT/TTS and full response latency, observed task outcome,
audible playback, cancellation cleanup, restart continuity and failures. Do not
mark Phase 10 real-audio acceptance complete until the microphone → real STT →
real Gemma → current-authorized core → real TTS → audible speaker workflow passes.

Continuous contextual processing remains the intended later direction. Current
`ambient-demo` tests multiple sources/rooms, turn labels, direct addressing,
follow-up, duplicate suppression, expiring context, candidates and policy denial.
These are bounded heuristic simulations, not reliable human voice identification,
simultaneous-room understanding, diarization, localization or acoustic deduplication.
Automatic profiling and unauthorized background recording remain out of scope.
