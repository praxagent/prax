# Docker

[← Infrastructure](README.md)

## Docker Compose

Start with the [setup guide](../guides/setup.md), which includes the required
Prax, TeamWork, and prax-sandbox sibling checkouts and private configuration.

```bash
docker compose up --build
```

The stock configuration is for a trusted owner or shared development environment.
It mounts the host Docker socket into Prax, which is administrative access to the
host daemon and not a hardened boundary against a compromised agent. Read
[Deployment topology](../security/deployment-topology.md) before using an exposed
server or relying on credential isolation.

> **Closed 2026-09 — the sandbox service.** The `sandbox` service carries **no
> compose-level `healthcheck`**: the image's own `HEALTHCHECK` (`pgrep -x supervisord`)
> is what `prax`'s `depends_on: sandbox: condition: service_healthy` waits on. Until
> 2026-09 both `docker-compose.yml` and `docker-compose.lite.yml` overrode it with a
> curl of `http://localhost:4096/global/health` — the OpenCode server removed from the
> image in 2026-07, prax-sandbox #4 — so the sandbox never reported healthy and `prax`
> never started from this path. The same change dropped the sandbox's
> `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` injection and its read-write `/source` checkout
> mount (leftovers from the removed coding agents). `tests/test_compose_sandbox_service.py`
> now fails CI if a `:4096` probe, a `service_healthy` dependency with no health
> source, a credential in the sandbox environment, or a host mount other than the
> workspace comes back.

### Day-to-day commands

```bash
docker compose up                         # start using existing images
docker compose up --build                 # build images and start the stack
docker compose up --build prax            # start Prax and its dependencies
docker compose up --build sandbox         # start only the sandbox service
docker compose build prax                 # build without starting services
docker compose restart prax               # restart after code-only changes
```

Selecting `sandbox` does not start its dependent Prax service. For code-only
changes, the default source mounts avoid an image rebuild, but the process needs
a restart to load the changes unless a development reloader is active.

### Development reloader

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

**Known gap (2026-09): the overlay is stale.** `docker-compose.dev.yml` overrides
services named `app` and `teamwork`, but `docker-compose.yml` defines `prax` and
`sandbox` (TeamWork is bundled inside the `prax` image; there is no `teamwork`
service). Its `DEBUG=true` / live-mount settings never reach `prax`, and `app` has
neither an image nor a build context. Only its `sandbox` block takes effect — and
that one re-adds the read-write `./prax`, `./app.py`, `./config.py`, `./tests`
mounts at `/source/*` that the base files removed. The base compose already
bind-mounts `./prax`, `./app.py` and `./scripts` into `prax`; for a working
live-reload loop use `make run-local-all-dev` (host processes). Dependency or
Dockerfile changes still require rebuilding the image.

### Services and ports

| Service | What runs | Addresses |
|---------|-----------|-----------|
| `prax` | Flask, TeamWork, Qdrant, Neo4j, and optional ngrok | Host `3000` and `8000` map to TeamWork's container `8000`; host `5001` is Prax; `4040` is ngrok's dashboard. Qdrant/Neo4j ports stay internal. `.env` is injected and the Docker socket is mounted for sandbox management. |
| `sandbox` | Pure-execution environment from the sibling prax-sandbox image: Python + scientific stack, DuckDB, Lean, LaTeX, ffmpeg, poppler, pandoc, headless Chromium/CDP, and desktop. The image ships **no coding-agent server** (no OpenCode/Claude Code/Codex) — Prax codes natively. | Mounts `${WORKSPACE_DIR}/${PRAX_USER_ID}` at `/workspace` and nothing else from the host; no model API keys are passed in (closed 2026-09, pinned by `tests/test_compose_sandbox_service.py`; same shape as prax-sandbox's own compose). Only the `docker-compose.dev.yml` overlay adds `/source/*` mounts — see [sandbox-execution-boundary.md](../security/sandbox-execution-boundary.md). |
| `tailscale` (opt-in) | Tailscale sidecar using kernel TUN mode, `NET_ADMIN`, and `/dev/net/tun` | Tailnet HTTPS routes to TeamWork (`:443`) and, in the full observability profile, Grafana (`:3001`). Activated by `TS_AUTHKEY` + `COMPOSE_PROFILES=tailscale` in `.env`; silently skipped otherwise. Node state persists in a volume so the identity survives restarts. |

Prax waits for the sandbox healthcheck before starting (`condition: service_healthy`,
satisfied by the image `HEALTHCHECK` — see the note at the top). Environment
detection is automatic — compose sets `RUNNING_IN_DOCKER=true` and
`SANDBOX_HOST=sandbox`. The bundled entrypoint starts its internal services. A
healthy container is an availability signal, not proof that every UI panel, model
credential, or tool path works.

> **ngrok is a port tunnel, not a webhook tunnel.** When `NGROK_AUTHTOKEN` is set,
> `scripts/ngrok-launch.sh` runs `ngrok http 5001`, which publishes **every Flask
> route** to the public internet — not only the Twilio webhooks (`/transcribe`,
> `/sms`) and `/shared/<token>`; the share registry (`workspaces/{user}/.shares.json`)
> gates only what `/shared/<token>` will serve. The `/teamwork/*`, `/plugins/*` and
> `/api/users/*` routes behind it have no inbound authentication unless
> `PRAX_API_KEY` is set (`prax/blueprints/inbound_auth.py`) — see
> [network-exposure.md](../security/network-exposure.md) and the Twilio section of
> [configuration.md](../security/configuration.md) before enabling it.

Full Compose profiles are `local-llm`, `observability`, `tailscale`, and
`secrets-proxy`. Lite Compose has the bundled memory services and the `tailscale`
profile, but does not define those other optional services. There is no `memory`
or `ollama` profile. See [Setup](../guides/setup.md#full-and-lite-modes).

### Observability

```bash
docker compose --profile observability up --build
```

Full Compose adds Tempo, Loki, Prometheus, Promtail, and Grafana. Grafana is
published at host port `3002`. The example configuration enables anonymous
Grafana admin access, so restrict its reachability or configure authentication
before exposing it. See [Observability](observability.md).

### Access and credential boundaries

Adding Tailscale does not close the existing host-published ports. Bind or firewall
those ports for your deployment. Keep sandbox control interfaces private.

The sandbox is credential-free because the stock Compose files pass it no
provider values and mount only the workspace (closed 2026-09; pinned by
`tests/test_compose_sandbox_service.py`) — not by virtue of being a separate
container. That holds only while the files keep that shape: the
`docker-compose.dev.yml` overlay re-adds the read-write `/source` checkout mount,
and any coding client you install in the sandbox yourself brings its own
credential. Review mounts and environment when using a
[secrets proxy](../security/secrets-proxy.md).

For an alternative container arrangement, derive it from the checked-in Compose
and Dockerfiles, including their sibling build contexts, configuration, workspace
mounts, and service dependencies. A bare `docker build .` / `docker run prax`
example omits those requirements and is not an equivalent deployment.
