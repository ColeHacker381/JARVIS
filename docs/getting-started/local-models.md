# Local models: Ollama and compatible endpoints

Install Ollama using the official [download instructions](https://ollama.com/download)
for your macOS/Linux machine. Do not pipe unverified third-party scripts or bypass
TLS. Ollama is optional; fake mode needs no model weights, account or network.

In separate terminals:

```sh
ollama serve
ollama pull qwen2.5:3b
ollama list
curl --fail-with-body http://127.0.0.1:11434/v1/models
uv run jarvis serve --config .jarvis/dev/runtime.json --model-provider local --local-endpoint http://127.0.0.1:11434/v1 --local-model qwen2.5:3b
```

If the desktop app already runs Ollama, do not start a second server on port 11434.
The OpenAI-compatible endpoint ends in `/v1`; the model name must match `/models`.
This is an example compatible backend/model, not the permanently selected stack.
Memory/hardware requirements and model licensing depend on your chosen weights.

The existing `OpenAILocalModel` sends normalized messages/context and a bounded
JSON CognitiveDecision schema to `/chat/completions`, requests JSON output,
records provider/model/timing provenance and validates the response. A model must
produce the explicit v1 outer/nested contracts, including existing ActionProposal
session/correlation references when proposing an action. Invalid output fails
before execution; prompts cannot waive deterministic policy.

`jarvis doctor --config <file> --model-provider local` probes configured model
availability without transmitting conversation/memory content or executing tools.
Availability means the model ID is advertised, **not** verified structured reasoning.
Submit an actual conversation through the API to test inference. On outage the
runtime is DEGRADED: authorized deterministic task/initiative/notification work
continues; cognition returns a structured failure with no cloud fallback.

The endpoint must be credential-free loopback HTTP (literal loopback or localhost,
canonicalized by the existing adapter), with no query/fragment. Redirects and
environment proxies are disabled. Arbitrary remote inference is rejected; there
is no new SSRF or egress exception. Cloud remains disabled by default and no cloud
backend is installed.

Automated tests use fake models and an actual loopback HTTP protocol fixture.
They prove transport/contracts/provenance, not genuine AI inference. No Ollama
installation or genuine inference is claimed in the cloud validation environment.
