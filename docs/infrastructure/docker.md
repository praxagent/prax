# Docker

[← Infrastructure](README.md)

### Docker Compose (recommended)

```bash
cp .env-example .env    # configure API keys
docker compose up --build
```

> The `sandbox` service carries **no compose-level `healthcheck`**: the image's
> own `HEALTHCHECK` (`pgrep -x supervisord`) is what `prax`'s
> `depends_on: sandbox: condition: service_healthy` waits on. (Until 2026-09 both
> `docker-compose.yml` and `docker-compose.lite.yml` overrode it with a curl of
> `http://localhost:4096/global/health` — the OpenCode server removed from the
> image in 2026-07, prax-sandbox #4 — so the sandbox never reported healthy and
> `prax` never started from this path. `tests/test_compose_sandbox_service.py`
> now fails CI if a `:4096` probe, or a `service_healthy` dependency with no
> health source, comes back.)

**Day-to-day usage** — once images are built, skip the rebuild to start in seconds:

```bash
docker compose up                         # start with existing images (fast)
docker compose up --build                 # rebuild ALL images then start
docker compose up --build prax            # rebuild only the prax image, start everything
docker compose up --build sandbox         # rebuild only the sandbox image, start everything
docker compose build prax && docker compose up   # same idea, explicit two-step
```

Use `--build` when you've changed a Dockerfile or its dependencies (e.g. added a package). For code-only changes in dev mode, plain `docker compose up` is enough.

**Dev mode** — the intent is to mount local source code so changes auto-reload without rebuilding:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

**Known gap (2026-09): the overlay is stale.** `docker-compose.dev.yml` overrides services named `app` and `teamwork`, but `docker-compose.yml` defines `prax` and `sandbox` (TeamWork is bundled inside the `prax` image; there is no `teamwork` service). The `DEBUG=true` / live-mount settings never reach `prax`, and `app` has neither an image nor a build context. The base compose already bind-mounts `./prax`, `./app.py` and `./scripts` into `prax`; for a working live-reload dev loop use `make run-local-all-dev` (host processes) instead. You still need `--build` if you change the Dockerfile, `pyproject.toml`, or system-level dependencies.

This starts two core services (the `prax` container is all-in-one):

| Service | Description |
|---------|-------------|
| **prax** | All-in-one container that bundles the Flask app (port 5001), the TeamWork web UI (port 3000) + API (port 8000), Qdrant, Neo4j, and ngrok (dashboard on 4040). `.env` injected, Docker socket for sandbox management. When `NGROK_AUTHTOKEN` is set, `scripts/ngrok-launch.sh` runs `ngrok http 5001` — a **port tunnel that publishes every Flask route** to the public internet, not only the Twilio webhooks (`/transcribe`, `/sms`) and `/shared/<token>`; the share registry (`workspaces/{user}/.shares.json`) gates only what `/shared/<token>` will serve. The `/teamwork/*`, `/plugins/*` and `/api/users/*` routes behind it have no inbound authentication unless `PRAX_API_KEY` is set — see [network-exposure.md](../security/network-exposure.md) and the Twilio section of [configuration.md](../security/configuration.md) before enabling it. |
| **sandbox** | Always-on **pure-execution** sandbox with Python + scientific stack, DuckDB, Lean, LaTeX, ffmpeg, poppler, pandoc, headless Chrome, and desktop. The image ships **no coding-agent server** (no OpenCode/Claude-Code/Codex) — Prax codes natively. Mounts `${WORKSPACE_DIR}/${PRAX_USER_ID}` at `/workspace` and nothing else from the host; no model API keys are passed in (the `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` injection, the read-write `/source` repo mount and the `.sandbox/*` homes at `/root/…` — leftovers from the removed coding agents — were dropped in 2026-09; `tests/test_compose_sandbox_service.py` pins this). Same shape as prax-sandbox's own compose. The `docker-compose.dev.yml` overlay still mounts `./prax`, `./app.py`, `./config.py`, `./tests` at `/source/*` — see [sandbox-execution-boundary.md](../security/sandbox-execution-boundary.md). |
| **tailscale** *(opt-in)* | Userspace `tailscaled` sidecar that joins your tailnet and serves TeamWork (`:443`) + Grafana (`:3001`) over MagicDNS HTTPS. Activated by setting `TS_AUTHKEY` + `COMPOSE_PROFILES=tailscale` in `.env`; silently skipped otherwise. State persists in a Docker volume so the node identity survives restarts. |

The `prax` service depends on the sandbox being healthy (`condition: service_healthy`, satisfied by the image `HEALTHCHECK` — see the note at the top). Environment detection is automatic — `RUNNING_IN_DOCKER=true` and `SANDBOX_HOST=sandbox` are set by compose.

**With observability** — add `--profile observability` to start Tempo, Loki, Prometheus, Promtail, and Grafana alongside the core services:

```bash
docker compose --profile observability up --build
```

This adds five services (Tempo :4318, Loki :3100, Promtail, Prometheus :9090, Grafana :3002). See [Observability](observability.md) for details. Safe to leave `OBSERVABILITY_ENABLED=true` in `.env` — Prax probes Tempo at startup and silently disables tracing if it's unreachable.

**Runtime capabilities in Docker mode:**
- `sandbox_install("package")` — apt-get install inside the running sandbox
- `sandbox_rebuild()` — Prax edits the Dockerfile, rebuilds the image, and restarts the container
- `workspace_share_file("path/to/file.mp4")` — publish a single file at a public ngrok URL (explicit user consent only — file is added to the share registry; revoke via `workspace_unshare_file(token)`, audit via `workspace_list_shares()`)

### Standalone (without compose)

```bash
docker build -t prax .

docker run -d -p 5001:5001 --restart always \
  -v "$HOME/workspaces:/app/workspaces" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  prax
```

Build the sandbox image separately (it lives in the sibling **prax-sandbox** repo):
```bash
cd ../prax-sandbox && make build          # -> prax-sandbox:latest
```
