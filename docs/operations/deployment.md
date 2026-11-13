# Supported development deployment

The supported method is a foreground unprivileged process on macOS/Linux:

```sh
uv sync --locked --python 3.12
uv run jarvis serve --config /absolute/private/path/runtime.json
```

Keep database, WAL/SHM, claims, explicit credentials and ordinary configuration in
persistent private directories. `runtime-init` is a one-time **explicit development
fixture command**, never a service startup step or recovery mechanism. Existing
stores require backup and explicit migration, not fixture bootstrap. A missing
database/authority should fail closed. No root capability is needed to bind a
loopback port above 1024.

For Linux development, an optional systemd unit template is provided at
[jarvis-dev.service](../../deploy/jarvis-dev.service). Replace the example paths
and `jarvis` OS account with an existing unprivileged account that owns those
directories. `WorkingDirectory` points at the installed checkout; `ExecStart`
uses the frozen environment's `.venv/bin/jarvis`. Update credentials/config paths
and verify `doctor` and the foreground quickstart as that account first.

The template uses an explicit config, loopback listener, private umask, no-new-
privileges, protected system/home mounts, persistent writable data path,
restart-on-failure and bounded signal shutdown. It does not provision authority or
rotate credentials automatically. `ReadOnlyPaths`/`ReadWritePaths` must match your
actual installation; test filesystem restrictions before enabling it. Readiness
is HTTP-based, not systemd socket activation or notify protocol.

```sh
systemd-analyze verify deploy/jarvis-dev.service
# After editing/installing the template through your host's normal admin workflow:
systemctl status jarvis-dev
curl --fail-with-body http://127.0.0.1:8765/health/ready
```

The provided template is a **system** service with `User=jarvis`. For a user unit,
adapt/remove `User/Group` and use your host's user-service administration commands.
`ProtectHome=true` hides home-installed uv/Python binaries; use a Python installation
accessible to the service or deliberately adapt that restriction for your deployment.
Do not install/enable the template blindly. This environment validates the
foreground process and unit syntax where the tool is available, not a running
systemd service deployment. The security hardening template is provisionally
supported, not tested production containment.

No container platform is required or shipped; Docker/Compose, Kubernetes,
multi-host stores and public reverse-proxy deployment are deferred. A remote
homelab model requires an explicitly designed trusted inference/egress boundary;
do not change the loopback validator to make it connect.

## Explicit voice clients

The core starts without speech hardware or weights. Install optional `voice`
dependencies only on an explicitly authorized local client; register consent/source
policy and select microphone/playback devices. No ambient capture starts on boot.
Schema 8 stores require an explicit backed-up schema 9 migration before serve.
See [Phase 10 deployment/setup](../PHASE10.md); public streaming, production speaker
identity and unattended room-wide capture are deferred.
