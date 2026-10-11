# CLI reference

Generated help below is verified against the installed Python entry point.
Prefix commands with `uv run --no-sync jarvis` after `uv sync --locked`.
Root help includes legacy options; runtime commands have the separate operator
parser below. An accepted option still needs an applicable operation.

| Command | Purpose / authority |
| --- | --- |
| smoke | Offline contract/metadata check, no effects |
| demo | Phase 2 fake capability and permission checks |
| recovery-demo | Phase 3 actual process-crash recovery |
| memory-demo | Phase 4 persistent scoped memory |
| cognition-demo | Phase 5 fake cognition/conversation restart |
| world-demo | Phase 6 simulated scoped observations |
| initiative-demo | Phase 7 simulated approval/autonomy |
| notification-demo | Phase 8 durable simulated notifications |
| runtime-demo | Phase 9 two-process integrated HTTP runtime; `--verbose` for details |
| voice | Explicit local push-to-talk client of authenticated conversation API |
| audio | Trusted local source/device/STT/TTS/session diagnostics and consent controls |
| voice-demo | Offline speech pipeline plus real authenticated HTTP and restart |
| ambient-demo | Offline multi-source attention, isolated context and unconfirmed candidates |
| runtime-init | Explicit new development fixture store only; never reset an existing store |
| serve | Foreground loopback runtime, explicit config/current credentials |
| doctor | Read-only storage/authority/model/worker diagnosis |
| credentials issue/revoke/curl-config | Trusted OS credential administration, new private files |
| permissions grant/revoke | Current owner client file; host grants/revokes an exact permission |
| docs-check | Read-only documentation/reference consistency check, repository cwd |
| chat | Development fixture text CLI; explicit fake or configured local model |
| initiatives | Legacy fixture inbox/policy interface; no HTTP role selection |
| notifications | Legacy fixture inbox/preference/policy interface |
| db-init | Explicit schema migration, no identity/grant bootstrap |
| db-inspect | Read-only schema/table inspection |
| db-backup | Online SQLite backup to a new destination |
| db-verify | Read-only SQLite integrity/foreign-key/schema validation |

Runtime examples are in [quickstart](../getting-started/quickstart.md), credentials
and permissions in the [subsystem manual](../guides/subsystems.md), and database
commands in [backup/restore](../operations/backup-restore.md).

`initiatives`: list/show/approve/deny/cancel/execute/process/recover/configure/policies/pause.
`notifications`: list/show/read/unread/acknowledge/dismiss/expire/approve/deny/bridge/
process/recover/preferences/set-preferences/policies/configure. Use `--database`
explicitly; legacy `--identity` requires trusted host fixture code. Configuration
and inbox changes require exact expected revisions where supported.

Exit status 0 means the operation finished; it does not make every domain result
successful. Inspect structured result/state. Runtime startup/config/authority
failure exits 1. Forced or timed-out shutdown exits 2 with persisted attempts
recoverable under normal ambiguity rules. Missing local model is explicit
DEGRADED; cognitive failures have typed error codes. argparse misuse exits 2.
Raw secrets/provider exceptions are excluded from operator error output.

## Root help

```text
usage: jarvis [-h] [--json] [--after AFTER] [--text TEXT]
                   [--policy-file POLICY_FILE]
                   [--expected-revision EXPECTED_REVISION]
                   [--destination DESTINATION] [--database DATABASE]
                   [--identity {owner,friend,guest}]
                   [--conversation CONVERSATION]
                   [--model-provider {local,fake}]
                   [--local-endpoint LOCAL_ENDPOINT]
                   [--local-model LOCAL_MODEL]
                   {credentials,docs-check,doctor,permissions,runtime-demo,runtime-init,serve,ambient-demo,audio,voice,voice-demo,smoke,demo,recovery-demo,memory-demo,cognition-demo,world-demo,initiative-demo,notification-demo,notifications,initiatives,chat,db-init,db-inspect,db-backup,db-verify}
                   [{list,show,approve,deny,cancel,execute,process,recover,configure,policies,pause,read,unread,acknowledge,dismiss,expire,preferences,set-preferences,bridge}]
                   [initiative_id]

JARVIS development foundation

positional arguments:
  {credentials,docs-check,doctor,permissions,runtime-demo,runtime-init,serve,ambient-demo,audio,voice,voice-demo,smoke,demo,recovery-demo,memory-demo,cognition-demo,world-demo,initiative-demo,notification-demo,notifications,initiatives,chat,db-init,db-inspect,db-backup,db-verify}
  {list,show,approve,deny,cancel,execute,process,recover,configure,policies,pause,read,unread,acknowledge,dismiss,expire,preferences,set-preferences,bridge}
  initiative_id

options:
  -h, --help            show this help message and exit
  --json                structured notification output
  --after AFTER         recipient inbox cursor
  --text TEXT           literal recipient inbox search
  --policy-file POLICY_FILE
  --expected-revision EXPECTED_REVISION
  --destination DESTINATION
                        new backup file (db-backup only)
  --database DATABASE   development conversation/initiative database
  --identity {owner,friend,guest}
                        DEVELOPMENT trusted host identity (chat/inbox)
  --conversation CONVERSATION
                        resume owned conversation (chat only)
  --model-provider {local,fake}
                        chat provider
  --local-endpoint LOCAL_ENDPOINT
                        loopback OpenAI-compatible /v1 URL
  --local-model LOCAL_MODEL
                        local server model name
```

## Runtime operator help

```text
usage: jarvis [-h] [--config CONFIG] [--data-dir DATA_DIR]
                   [--database DATABASE] [--host {127.0.0.1,::1}]
                   [--port PORT] [--worker-credentials WORKER_CREDENTIALS]
                   [--model-provider {fake,local}]
                   [--local-endpoint LOCAL_ENDPOINT]
                   [--local-model LOCAL_MODEL] [--demo-fixtures]
                   [--identity {owner,friend,guest}] [--principal PRINCIPAL]
                   [--permission PERMISSION]
                   [--credential-file CREDENTIAL_FILE]
                   [--kind {client,worker}]
                   [--credential-hours CREDENTIAL_HOURS] [--output OUTPUT]
                   [--credential-id CREDENTIAL_ID] [--verbose]
                   {credentials,docs-check,doctor,permissions,runtime-demo,runtime-init,serve}
                   [{issue,revoke,grant,curl-config}]

JARVIS v0.9 LOCAL DEVELOPMENT runtime

positional arguments:
  {credentials,docs-check,doctor,permissions,runtime-demo,runtime-init,serve}
  {issue,revoke,grant,curl-config}

options:
  -h, --help            show this help message and exit
  --config CONFIG       explicit JSON settings file
  --data-dir DATA_DIR
  --database DATABASE
  --host {127.0.0.1,::1}
  --port PORT
  --worker-credentials WORKER_CREDENTIALS
  --model-provider {fake,local}
  --local-endpoint LOCAL_ENDPOINT
  --local-model LOCAL_MODEL
  --demo-fixtures       explicit simulated source and approval policy (init
                        only)
  --identity {owner,friend,guest}
                        TRUSTED OS provisioning selector; never accepted by
                        HTTP
  --principal PRINCIPAL
  --permission PERMISSION
                        exact permission name (host administration)
  --credential-file CREDENTIAL_FILE
                        explicit owner client credential file
  --kind {client,worker}
  --credential-hours CREDENTIAL_HOURS
  --output OUTPUT       new private credential file
  --credential-id CREDENTIAL_ID
  --verbose             structured runtime-demo diagnostics
```

## Voice client and audio controls

```text
usage: jarvis [-h] [--config CONFIG] [--credential-file CREDENTIAL_FILE]
                   [--source SOURCE] [--location LOCATION]
                   [--consent-ref CONSENT_REF] [--continuous]
                   [--memory-candidates] [--device DEVICE]
                   [--output-device OUTPUT_DEVICE] [--seconds SECONDS]
                   [--stt-binary STT_BINARY] [--stt-model STT_MODEL]
                   [--language LANGUAGE] [--tts-voice TTS_VOICE]
                   [--tts-rate TTS_RATE] [--conversation CONVERSATION]
                   [--fake] [--text TEXT] [--wav WAV] [--no-playback]
                   {ambient-demo,audio,voice,voice-demo}
                   [{devices,register,mute,sessions,mic-test,stt-test,tts-test}]

JARVIS local voice; explicit microphone consent only

positional arguments:
  {ambient-demo,audio,voice,voice-demo}
  {devices,register,mute,sessions,mic-test,stt-test,tts-test}

options:
  -h, --help            show this help message and exit
  --config CONFIG
  --credential-file CREDENTIAL_FILE
  --source SOURCE
  --location LOCATION
  --consent-ref CONSENT_REF
  --continuous
  --memory-candidates
  --device DEVICE       explicit input device index (voice/mic-test)
  --output-device OUTPUT_DEVICE
  --seconds SECONDS
  --stt-binary STT_BINARY
  --stt-model STT_MODEL
  --language LANGUAGE
  --tts-voice TTS_VOICE
  --tts-rate TTS_RATE
  --conversation CONVERSATION
  --fake                DEVELOPMENT simulation, no real speech reasoning
  --text TEXT           literal fake transcript or standalone TTS text
  --wav WAV             <=10s mono PCM WAV for stt-test or one-shot voice
  --no-playback
```

```text
usage: jarvis [-h] [--config CONFIG] [--credential-file CREDENTIAL_FILE]
                   [--source SOURCE] [--location LOCATION]
                   [--consent-ref CONSENT_REF] [--continuous]
                   [--memory-candidates] [--device DEVICE]
                   [--output-device OUTPUT_DEVICE] [--seconds SECONDS]
                   [--stt-binary STT_BINARY] [--stt-model STT_MODEL]
                   [--language LANGUAGE] [--tts-voice TTS_VOICE]
                   [--tts-rate TTS_RATE] [--conversation CONVERSATION]
                   [--fake] [--text TEXT] [--wav WAV] [--no-playback]
                   {ambient-demo,audio,voice,voice-demo}
                   [{devices,register,mute,sessions,mic-test,stt-test,tts-test}]

JARVIS local voice; explicit microphone consent only

positional arguments:
  {ambient-demo,audio,voice,voice-demo}
  {devices,register,mute,sessions,mic-test,stt-test,tts-test}

options:
  -h, --help            show this help message and exit
  --config CONFIG
  --credential-file CREDENTIAL_FILE
  --source SOURCE
  --location LOCATION
  --consent-ref CONSENT_REF
  --continuous
  --memory-candidates
  --device DEVICE       explicit input device index (voice/mic-test)
  --output-device OUTPUT_DEVICE
  --seconds SECONDS
  --stt-binary STT_BINARY
  --stt-model STT_MODEL
  --language LANGUAGE
  --tts-voice TTS_VOICE
  --tts-rate TTS_RATE
  --conversation CONVERSATION
  --fake                DEVELOPMENT simulation, no real speech reasoning
  --text TEXT           literal fake transcript or standalone TTS text
  --wav WAV             <=10s mono PCM WAV for stt-test or one-shot voice
  --no-playback
```
