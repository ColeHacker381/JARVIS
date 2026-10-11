# Quickstart: local fake-mode runtime

Run from the cloned repository after [installation](installation.md). Use a new
ignored directory; this example uses `.jarvis/dev`. If it already exists, choose
another name and replace **every occurrence** below. This guide provisions
development owner/friend/guest credentials and explicit simulated fixtures. It
enables a garage approval-required fixture only because `--demo-fixtures` is given;
ordinary initialization and serving do not enable autonomy policies.

```sh
uv run jarvis runtime-init --data-dir .jarvis/dev --model-provider fake --demo-fixtures
uv run jarvis doctor --config .jarvis/dev/runtime.json
uv run jarvis credentials curl-config --credential-file .jarvis/dev/credentials/owner.json --output .jarvis/dev/credentials/owner.curl
uv run jarvis serve --config .jarvis/dev/runtime.json
```

`runtime-init` prints paths, credential IDs and schema version, never tokens.
Files are created privately (0600) in new private directories (0700). The client
credentials and separately typed owner worker credential expire after 24 hours;
use `--credential-hours <1..8760>` at initialization if a different development
lifetime is intentional. Keep these files private and outside Git. The default
listener is `127.0.0.1:8765`; use `--port` if needed and update URLs accordingly.

Keep `serve` in the foreground. In a **second terminal**, from the repository:

```sh
curl --fail-with-body --silent --show-error http://127.0.0.1:8765/health/ready
curl --fail-with-body --silent --show-error --config .jarvis/dev/credentials/owner.curl -H 'Content-Type: application/json' -d '{"schema_version":1}' http://127.0.0.1:8765/api/v1/conversations > .jarvis/dev/conversation.json
JARVIS_CONVERSATION_ID=$(uv run python -c 'import json; print(json.load(open(".jarvis/dev/conversation.json"))["data"]["conversation_id"])')
JARVIS_REQUEST_ID=$(uv run python -c 'from uuid import uuid4; print(uuid4())')
curl --fail-with-body --silent --show-error --config .jarvis/dev/credentials/owner.curl -H 'Content-Type: application/json' -d "{\"schema_version\":1,\"request_id\":\"$JARVIS_REQUEST_ID\",\"text\":\"What does Slipstream use?\"}" "http://127.0.0.1:8765/api/v1/conversations/$JARVIS_CONVERSATION_ID/messages"
curl --fail-with-body --silent --show-error --config .jarvis/dev/credentials/owner.curl http://127.0.0.1:8765/api/v1/tasks
curl --fail-with-body --silent --show-error --config .jarvis/dev/credentials/owner.curl http://127.0.0.1:8765/api/v1/notifications
```

Readiness reports READY, database/model availability and scheduler status in fake
mode. The memory answer has `data.kind="memory_grounded"`, current memory UUIDs,
`data.provenance.location="fake"`, and text containing
`Slipstream uses microcontroller B`. The top-level v1 response has `request_id`
and `data`; IDs/times vary. Empty task/inbox lists are normal before actions/events.
The curl configuration keeps the bearer token out of process arguments/history.

Send `Set lights to 25` with a **new UUID** using the same message route. This
executes a **simulated** capability through TaskManager and the real policy/executor;
inspect `data.task_id`, `data.task_state`, and `data.execution.status`. Completion
means the simulated handler's verified result, not physical lighting control.
Repeat the same UUID/body to verify idempotent replay; changed text with the same
UUID returns 409. Never infer execution success just from `data.text` or HTTP 200.

Use Ctrl-C in the server terminal, then run the same `serve` command again.
Retrieve `/api/v1/conversations/<conversation-id>/history` and the task by ID to
verify persistence. Current revoked/expired source context is omitted from history.
The original credentials remain usable until expired/revoked; a conversation ID
is not authentication.

The automated acceptance workflow executes initialization, a real server process,
memory/action requests, an enrolled simulated observation, automatic initiative/
notification processing, guest denial, graceful shutdown and separate-process
restart using these same CLI and API contracts:

```sh
uv run jarvis runtime-demo
uv run jarvis runtime-demo --verbose
uv run jarvis docs-check
```

Each demo uses a new UUID directory and a free loopback port. Default output is a
concise measured summary; verbose output includes actual IDs, audit references and
numeric diagnostics. Neither command claims real AI or device operation.
