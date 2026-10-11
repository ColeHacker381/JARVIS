# Configuration

`RuntimeSettings` validates ordinary configuration. Precedence is defaults < an
explicit `--config file.json` < `JARVIS_<FIELD_NAME>` process variables < supplied
CLI overrides. Unknown file fields fail rather than being silently ignored. No
dotenv or unexpected credential/config paths are searched. Relative paths resolve
against the process working directory; `runtime-init` writes absolute paths.

The complete [configuration reference](../reference/configuration-reference.md)
is generated/checked against actual Pydantic fields. Common overrides:

```sh
uv run jarvis serve --config .jarvis/dev/runtime.json --port 8766 --model-provider fake
JARVIS_LOG_LEVEL=DEBUG uv run jarvis serve --config .jarvis/dev/runtime.json
```

An explicit runtime JSON can be copied to a **new** file and edited with ordinary
settings. Protect private paths; never put token values inside runtime settings.
The data directory defaults to `.jarvis`, and the runtime store to
`.jarvis/runtime/journal.sqlite3`; legacy ordinary/chat/inbox default stores remain
separate. `database_path` or `--database` overrides the runtime store. Core serve
does not migrate or bootstrap; perform explicit backup/migration beforehand.

Offline example (replace paths with absolute private paths on your machine):

```json
{"data_dir":"/path/to/private/jarvis","database_path":"/path/to/private/jarvis/runtime/journal.sqlite3","model_provider":"fake","worker_credentials_file":"/path/to/private/jarvis/credentials/workers.json","runtime_host":"127.0.0.1","runtime_port":8765,"worker_batch_size":20}
```

macOS development uses the same foreground workflow under `.jarvis/dev`; no root
account is needed. Linux homelab development may use `/home/jarvis/jarvis-data`
owned by an unprivileged service user; see [deployment](../operations/deployment.md).
For Ollama select `model_provider="local"`,
`local_model_endpoint="http://127.0.0.1:11434/v1"`, and the exact installed model
name. [Local model setup](local-models.md) explains the prerequisites.

Intervals bound finite batches; they are minimum spacing after a pass completes,
not deadlines or hard real-time guarantees. There is one scheduler worker and
up to `max_requests` active request workers, with bounded admission. Quiet hours,
notification queues/rates and initiative policies remain current domain policy.
Serving never enables or expands them.

Only `mode="development"` and loopback host literals are supported. Cloud AI
cannot be enabled by these settings. `simulated_notification_channels=false`
registers only the authoritative local inbox; policies requiring absent adapters
report failures without falling back. Set `worker_credentials_file=null` to run
API-only/degraded mode without granting background authority. Secret files are
explicit, regular, owned by the same OS user, 0600, bounded and not symlinked.
