# Installation

Supported development systems are **macOS and Linux**, Python **3.12 or 3.13**.
The package declares Python >=3.12; later Python releases are not validated here.
SQLite and POSIX file locking require a private local filesystem. Windows and
network filesystems are unsupported. No Docker, cloud account or hardware is needed.

Install Git, curl and uv. Follow the verified HTTPS installer instructions at
[uv installation](https://docs.astral.sh/uv/getting-started/installation/).
For macOS, `brew install uv` is also available when Homebrew is already installed.
Do not bypass package/TLS verification. Then:

```sh
git clone https://github.com/ColeHacker381/JARVIS.git
cd JARVIS
uv python install 3.12
uv sync --locked --python 3.12
uv run --no-sync jarvis smoke
uv run --no-sync jarvis --help
```

The lockfile controls dependencies, including Pydantic, jsonschema, httpx,
FastAPI and Uvicorn. pytest and Ruff are development dependencies. `uv run` uses
this checkout's executable; avoid invoking an older globally installed `jarvis`.
The package release is 0.9.0; task/memory/observation wire v2 and other wire v1
contracts have independent versions.

Proceed to [quickstart](quickstart.md). Model installation is optional for offline
development; the fake model is deterministic keyword behavior, not AI reasoning.
Existing databases are preserved. Do not use `runtime-init` on an existing store.
