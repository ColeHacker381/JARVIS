# Testing and packaging

Run from a frozen checkout with Python 3.12 or 3.13:

```sh
uv sync --locked --python 3.12
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync jarvis docs-check
uv build
```

Generate references deliberately after interface changes, then review the diff:

```sh
uv run --no-sync python -m jarvis.documentation --write
```

`docs-check` checks internal file links, required guide files, and exact generated
configuration/CLI/OpenAPI references against current code. It does not certify
external links, language quality or every Markdown fragment. The quickstart smoke
test runs fresh private initialization, actual authenticated HTTP, bounded
scheduler work, shutdown and same-store restart; runtime-demo uses that workflow.
CI runs Python 3.12/3.13, docs checks, all nine demos and build. Never replace
measured subprocess outcomes with predetermined success output.

```sh
uv run --no-sync jarvis smoke
uv run --no-sync jarvis demo
uv run --no-sync jarvis recovery-demo
uv run --no-sync jarvis memory-demo
uv run --no-sync jarvis cognition-demo
uv run --no-sync jarvis world-demo
uv run --no-sync jarvis initiative-demo
uv run --no-sync jarvis notification-demo
uv run --no-sync jarvis runtime-demo
```

Security tests verify actual non-invocation, inaccessible records absent from model
context and current authority after revocation. Runtime tests exercise missing/
corrupt/future stores, strict private credential files, active-worker revocation,
finite queue/cursor behavior, failure isolation and real killed processes.
Cancellation tests retain pending thread futures until drained; ambiguity tests
kill an actual task after the durable RUNNING attempt checkpoint and before its
provider. Safe pure recovery completes; non-idempotent lighting becomes
NEEDS_REVIEW without another invocation.

The local-model test uses a real loopback HTTP protocol server advertising one
synthetic model and answering the same structured contract; it is not genuine AI.
The ordinary baseline before Phase 9 was **724 passed in 159.41 seconds** on
Python 3.12.14. Final acceptance results are in the [release report](../releases/v0.9.md).
Starlette's AnyIO BlockingPortal alias emits a known upstream deprecation warning;
tests retain the warning rather than suppressing it.

For installed-wheel acceptance, create a separate temporary virtual environment,
install `dist/jarvis_kernel-0.9.0-py3-none-any.whl`, change outside the checkout, and
run its `jarvis` executable through all nine demos. Also run private runtime init/
doctor and schema inspect/verify/online backup. Documentation checking requires
the documentation-bearing checkout; runtime and demos do not. Confirm no token,
database, model weight or generated runtime log enters Git.

## Audio and occupied-port regressions

`uv run pytest tests/test_audio.py tests/test_runtime.py` runs offline enforcement,
provider protocol subprocesses, source/context isolation, revocation, candidate policy,
resource cleanup, actual voice-client HTTP and restart demos. Runtime signal/recovery
regressions occupy 8765 with an unrelated process while testing owned ephemeral sockets.
Existing listeners are left untouched. Fakes do not establish real inference/hardware.
See [Phase 10](../PHASE10.md) for baseline, boundaries and manual acceptance.
