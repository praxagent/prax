<div align="center">

# Prax

**Your personal AI assistant — on the web, Discord, SMS, and voice.**

97+ built-in tools, extensible via self-modifying plugins. Git-backed memory. Runs on your own server.

Designed to work with the optional [**TeamWork**](https://github.com/praxagent/teamwork) — a Slack-like web UI with real-time chat, Kanban board, file browser, terminal, and browser screencast.

Designed to work with the optional [**prax-sandbox**](https://github.com/praxagent/prax-sandbox) — a plug-and-play code-execution sandbox: a long-running Docker container running the coding agents (OpenCode / Claude Code / Codex), a headless + desktop Chromium (CDP + noVNC), and a full toolchain (TeX, ffmpeg, pandoc, hugo, …).

<img src="assets/prax-header-image.png" alt="Prax">

</div>

> **Warning — Active Development & API Cost Risk**
>
> This project is under heavy, rapid development. Interfaces, tool names, and
> behaviors may change without notice. **Use at your own risk.**
>
> Prax runs agentic workflows that can chain many LLM calls per user message
> (tool calls, sub-agent delegation, revision loops, sandbox sessions, etc.).
> If you are using a non-local LLM provider (OpenAI, Anthropic, Google, etc.),
> **you must set up spending limits and cost monitoring on your provider
> account.** A stuck or misconfigured workflow can burn through API credits
> quickly. Built-in guardrails (recursion limits, round budgets, auto-abort on
> consecutive failures) reduce this risk but cannot eliminate it entirely.
> The maintainers are not responsible for any API charges incurred.

---

## Quick Start

### Docker Compose

```bash
git clone https://github.com/praxagent/prax.git && cd prax
git clone https://github.com/praxagent/teamwork.git ../teamwork  # web UI
cp .env-example .env                      # configure (see below)
```

**Required `.env` settings** (at minimum):

| Variable | What | Example |
|----------|------|---------|
| `OPENAI_KEY` | OpenAI API key (or set `ANTHROPIC_KEY` for Claude) | `sk-...` |
| `PRAX_USER_ID` | Your workspace directory name — the sandbox mounts only this user's folder for isolation. Pick any slug. | `usr_alice`, `myworkspace` |

```env
OPENAI_KEY=sk-...
PRAX_USER_ID=usr_alice
```

Prax will **refuse to start** without `PRAX_USER_ID` when running in Docker. On first run it creates the workspace directory and associates it with your identity automatically.

#### Lite mode (recommended for laptops)

```bash
docker compose -f docker-compose.lite.yml up --build

# …or with remote access over Tailscale (opt-in profile; set TS_AUTHKEY +
# TS_HOSTNAME in .env first — see "Remote access" below):
COMPOSE_PROFILES=tailscale docker compose -f docker-compose.lite.yml up --build
```

**2 containers.** Bundles Prax + TeamWork + Qdrant + Neo4j + ngrok into a single image alongside the sandbox. Uses ~2-3GB RAM total. Best for local development and resource-constrained machines. The Tailscale sidecar is opt-in: it only starts with `COMPOSE_PROFILES=tailscale` **and** `TS_AUTHKEY` set, never by default.

#### Full mode (recommended for servers)

```bash
docker compose up --build
```

**2 containers by default** (`prax` + `sandbox`). Uses the full `Dockerfile` (JDK 21, glibc Qdrant binary) — more memory headroom for Neo4j's JVM and faster GC, suited to servers. Same bundled layout as lite: Prax + TeamWork + Qdrant + Neo4j + ngrok all run inside the `prax` container. Opt-in profiles add services alongside: `--profile local-llm` starts Ollama, `--profile observability` starts Grafana + Tempo + Prometheus + Loki (see below).

Both modes expose the same UI at **http://localhost:3000** and publish the same host ports (3000, 5001, 8000, 4040). Qdrant and Neo4j run inside the `prax` container and are not published to the host by default — if you want direct access, add a `ports:` entry (`6333:6333`, `7474:7474`) to `docker-compose.yml` or use `docker compose exec prax ...`.

> **Older Ubuntu?** If `docker compose up` fails with `the classic builder doesn't support additional contexts, set DOCKER_BUILDKIT=1 to use BuildKit`, install the buildx plugin: `sudo apt install docker-buildx`. BuildKit then becomes the default and the build proceeds. As a one-shot alternative, prefix the command: `DOCKER_BUILDKIT=1 COMPOSE_DOCKER_CLI_BUILD=1 docker compose up --build`.

#### With observability (Grafana + Tempo + Prometheus + Loki)

```bash
docker compose --profile observability up --build
```

This adds the full observability suite alongside the core services:

| Service | Port | Purpose |
|---------|------|---------|
| **Grafana** | [localhost:3002](http://localhost:3002) | Dashboards — traces, logs, metrics (login: admin / prax) |
| **Tempo** | 4318 | Distributed tracing backend (receives OTLP spans from Prax) |
| **Prometheus** | [localhost:9090](http://localhost:9090) | Metrics scraping and storage |
| **Loki** | 3100 | Log aggregation (fed by Promtail) |
| **Promtail** | — | Ships Docker container logs to Loki |

`OBSERVABILITY_ENABLED=true` is the default in `.env`. If you run without `--profile observability`, Prax detects that Tempo is unreachable at startup and silently disables the OTEL exporter — no retries, no memory accumulation, no OOM risk.

Grafana comes pre-provisioned with Tempo, Loki, and Prometheus datasources plus two dashboards (Agent Overview, LLM Performance). Config lives in `observability/`.

#### GPU mode (NVIDIA)

If you have an NVIDIA GPU and want the sandbox to use it (local LLM inference via vLLM, faster Whisper transcription, ML/AI experimentation in `sandbox_shell`, etc.), layer in the GPU override:

```bash
make sandbox-gpu                                                # one-shot, with smoke test
# or persistently — add to .env so plain `docker compose` always uses it:
echo 'COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml' >> .env
docker compose up -d
```

Requires on the host:
- NVIDIA driver (verify: `nvidia-smi`)
- [`nvidia-container-toolkit`](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) installed and registered with Docker (verify: `docker info | grep -i nvidia` shows `nvidia` under Runtimes).

The override (`docker-compose.gpu.yml`) reserves all GPUs for the sandbox container and sets the env vars the toolkit needs to inject CUDA libraries at runtime — no CUDA install in the image. To pin to a specific GPU instead of all, change `count: all` to `device_ids: ["0"]` in the override file. Inside the sandbox, `nvidia-smi` works immediately and `pip install torch --index-url https://download.pytorch.org/whl/cu124` (or any cu12x wheel) picks up the GPU automatically.

Only the sandbox gets the GPU — Prax itself stays CPU-only by default. If you want CUDA in the `prax` container too (for embeddings etc.), add the same `deploy.resources` block to the `prax` service in the override.

#### Memory, Ollama, and core services

Qdrant and Neo4j are **bundled inside the `prax` container** and start automatically on `docker compose up` — data persists to `workspaces/<PRAX_USER_ID>/.services/{qdrant,neo4j,teamwork}` so it survives restarts and rebuilds, and is scoped per user. Prax talks to them over localhost inside the container; they're not published to the host by default. Prax is nothing without his memory.

Ollama is opt-in: start it with `docker compose --profile local-llm up` to run a separate Ollama container that Prax reaches at `http://ollama:11434` inside the Docker network.

| Service | How it runs | Host port | Purpose |
|---------|-------------|-----------|---------|
| **Qdrant** | embedded in `prax` container | — (internal) | Vector store for semantic memory retrieval (dense + sparse) |
| **Neo4j** | embedded in `prax` container | — (internal, login: neo4j / prax-memory) | Knowledge graph for entity/relation memory |
| **Ollama** | separate container, profile `local-llm` | [localhost:11434](http://localhost:11434) | Local LLM and embedding inference (model auto-pulled on first start) |

`MEMORY_ENABLED=true` is the default. Set to `false` to disable memory even when the services are running. See [Memory System](#memory-system) for details.

For local embeddings (no data sent to OpenAI), set in `.env`:
```env
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=nomic-embed-text
```

The configured embedding model is auto-pulled when the Ollama container starts. See [Embedding Providers](docs/infrastructure/memory.md#embedding-providers) for a comparison of OpenAI vs Ollama vs local options.

#### Developer mode

The default `docker-compose.yml` volume-mounts `./prax`, `./app.py`, and `./scripts` into the `prax` container. Python/prompt changes take effect with a container restart — no image rebuild needed:

```bash
# Edit prax/ or prompts locally, then:
docker compose restart prax
```

For **frontend (TeamWork)** changes, run the Vite dev server locally instead of rebuilding the TeamWork image:

```bash
cd ../teamwork/frontend   # or wherever your teamwork repo lives
npm run dev                # starts Vite on :5173 with hot reload
```

Open `http://localhost:5173` instead of `:3000`. Vite proxies API calls (`/api/*`, `/ws/*`) to the TeamWork backend at `localhost:8000`, which is already exposed by Docker Compose. The architecture:

```
Browser → Vite :5173 (HMR, serves React)
  ↓ /api/*, /ws/*
TeamWork :8000 (exposed to host via Docker)
  ↓ internal webhook
Prax app :5001 (Docker-internal)
```

The frontend never talks to Prax directly — TeamWork is the middleman. So Vite works with the full Docker stack with no extra config.

**When you need a full rebuild** (dependency changes in `pyproject.toml` or `package.json`):

```bash
docker compose up --build prax       # Prax image only (TeamWork, Qdrant, Neo4j are bundled into it)
docker compose up --build sandbox    # Sandbox image only
docker compose up --build            # both
```

#### Remote access (Tailscale / HTTPS)

Accessing Prax from another machine works fine, but the **Desktop** and **Browser** tabs in TeamWork need a WebSocket to noVNC / CDP, and browsers only allow those from a **secure context** — HTTPS or `localhost`. Opening `http://<remote-host>:3000` directly will fail with `noVNC requires a secure context (TLS)` in the console and `Connection closed (code: 1006)` from `rfb.js`.

Three easy fixes, in order of recommendation:

**Tailscale sidecar (Docker)** — recommended. Runs `tailscaled` inside the compose stack, so your server's host network never has to expose Prax. State is persisted in a Docker volume so the node keeps its identity across restarts:

```bash
# 1. Get a reusable, NON-ephemeral, pre-approved key from
#    https://login.tailscale.com/admin/settings/keys
#    (Ephemeral keys count against the free tier's 1,000-min/month
#    minute budget — non-ephemeral keys do not.)
# 2. Add to .env:
#       TS_AUTHKEY=tskey-auth-...
#       TS_HOSTNAME=prax            # whatever name you want on the tailnet
#       COMPOSE_PROFILES=tailscale  # without this the sidecar is skipped
# 3. Up:
docker compose up -d
# Visit: https://prax.<tailnet>.ts.net/         (TeamWork)
#        https://prax.<tailnet>.ts.net:3001/   (Grafana, if observability is up)
```

The sidecar uses Tailscale userspace mode (no `/dev/net/tun` on the host), reads its serve config from [`tailscale/serve-config.json`](tailscale/serve-config.json), and proxies `:443 → prax:8000` and `:3001 → grafana:3000` over the tailnet. HTTPS must be enabled on your tailnet (admin console → DNS → HTTPS Certificates). Compose treats the service as opt-in: with `COMPOSE_PROFILES` unset, the sidecar is silently skipped, so leaving the variables out is identical to not having Tailscale at all.

**Tailscale on the host** — fallback if you already run `tailscaled` on the server and don't want a sidecar. The Makefile keeps the original mappings:

```bash
make tailscale-up      # serves :443→:3000 and :3001→:3002 from the host
make tailscale-down    # tears them down
make tailscale-status  # show current serve config
```

**SSH tunnel** — works without touching Tailscale at all, since browsers treat `localhost` as a secure context even over plain HTTP:

```bash
ssh -L 3000:localhost:3000 <remote-host>
# Visit: http://localhost:3000/
```

### Run without Docker

The Docker image bundles Prax + TeamWork + Qdrant + Neo4j into one container. To run without Docker you start each of those pieces yourself. This is the path for local development and for machines where you'd rather not run Docker.

**Shortcut — `make` targets.** Once the prerequisites below are in place, you don't have to start each piece by hand:

```bash
make run-local-min     # Prax core only, foreground — memory/sandbox/TeamWork all OFF (Ctrl-C to stop)
make run-local-all     # full local stack in the background: Qdrant + Neo4j + TeamWork + sandbox + Prax
make run-local-all-dev # same as run-local-all but DEBUG=true — Prax restarts on code change (Werkzeug reloader)
make run-local-all-tail-dev # run-local-all-dev + a Tailscale serve exposing the TeamWork UI over HTTPS
make local-status      # probe each service's port, report up/down
make smoke             # connectivity smoke test — verify everything is CONNECTED, not just up
make integration       # FROM CLEAN: tear down, clear derived state, bring the stack up, run smoke (pre-PR)
make local-logs        # tail -F every .local-run/*.log
make shutdown          # stop everything run-local-all started (processes, containers, and the Tailscale serve)
```

> **Verify a fresh install.** `make local-status` only checks ports; **`make smoke`**
> (after `make run-local-all`) asserts the cross-service *wiring* a fresh clone needs —
> TeamWork serves its built SPA, TeamWork→Prax proxy works, the sandbox CDP/desktop
> WebSocket upgrades succeed, and Prax reaches memory + the sandbox. To prove this on
> *your own* box before opening a PR, run **`make integration`**: it tears the running
> stack down, deletes the derived state that masks fresh-download bugs (the built
> TeamWork SPA, the `.local-run` markers), brings everything up from scratch, and runs
> `make smoke` — its exit code is the pass/fail signal. (It's disruptive — it stops your
> live local stack — and heavy: it rebuilds the SPA and starts the Chrome+desktop
> sandbox, so budget ~4GB free RAM. `REBUILD_SANDBOX=1` also rebuilds the sandbox image
> from scratch; `SANDBOX_PATH=/nonexistent` skips it for a core-only run.) The
> `Fresh-install integration` GitHub workflow (`.github/workflows/fresh-install.yml`,
> nightly/manual) runs the *same* `make integration` on a **clean runner** that clones
> all three repos — so "works on a fresh download," not just on a machine you've been
> hacking on, is continuously proven.

> **Prerequisite — Node.js (for the TeamWork web UI).** The TeamWork UI is a React app that must
> be compiled (or run via the Vite dev server). Without **Node.js + npm** on the host, TeamWork's
> backend still runs but `/` returns `{"detail":"Not Found"}` (no UI). `make run-local-all` builds
> the UI automatically when `npm` is present (and `run-local-all-dev` runs it with hot-reload);
> if `npm` is missing it warns and serves API-only. Node **18+** is enough (TeamWork uses Vite 5).
> Install it:
> - **macOS:** `brew install node` — or download the LTS installer from [nodejs.org](https://nodejs.org/).
> - **Windows:** `winget install OpenJS.NodeJS.LTS` (or `choco install nodejs-lts`) — or the [nodejs.org](https://nodejs.org/) installer.
> - **Linux (Debian/Ubuntu):** `sudo apt install nodejs npm` (Ubuntu's `nodejs` package omits `npm`,
>   so install both) — or, for a newer Node, the [NodeSource](https://github.com/nodesource/distributions) repo / [nvm](https://github.com/nvm-sh/nvm).
> - **Any OS via nvm:** `nvm install --lts`.
>
> Verify with `node --version && npm --version`, then re-run the `make` target.

`run-local-all` brings up the whole stack — memory **on**, TeamWork **on**, sandbox **on**. Prax and TeamWork run as plain host processes; **Qdrant, Neo4j and the sandbox run in Docker** (Prax connects to their published localhost ports). Everything persists under the user's workspace (default `PRAX_USER=local`): Qdrant/Neo4j data in `workspaces/$PRAX_USER/.services/{qdrant,neo4j}`, and the sandbox's `/workspace` is bind-mounted to `workspaces/$PRAX_USER` — so memory **and** sandbox files survive restarts rather than vaporizing with the containers. The sandbox inherits Prax's API keys (`.env`'s `ANTHROPIC_KEY`/`OPENAI_KEY` → the sandbox's `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`). PIDs and logs land in `.local-run/`. If the sandbox is expected (Docker + checkout present) but fails to start, `run-local-all` hard-fails instead of silently disabling it.

Each backing service is skipped with an **actionable install hint** (never a hard failure) if it can't start — Qdrant and Neo4j prefer Docker (falling back to a native `qdrant`/`neo4j` binary), plus a sibling TeamWork checkout and a sibling `prax-sandbox` checkout (Docker-only). Override locations/owner with `make run-local-all TEAMWORK_PATH=/path/to/teamwork SANDBOX_PATH=/path/to/prax-sandbox PRAX_USER=alice`. By default the sibling repos are expected next to this one:

```bash
git clone https://github.com/praxagent/teamwork      ../teamwork
git clone https://github.com/praxagent/prax-sandbox  ../prax-sandbox
```

**Docker images.** The first `run-local-all` pulls `qdrant/qdrant` and `neo4j:5` automatically (via `docker run`) and builds the sandbox image from its checkout — the first run is therefore slow. To pre-warm the cache (or just to watch progress), pull them yourself first:

```bash
docker pull qdrant/qdrant
docker pull neo4j:5
```

`make` deliberately does **not** run `docker pull` for you: auto-pull happens through the Docker daemon, which honours its own proxy/registry settings. **Behind a proxy**, configure Docker itself (daemon `HTTP_PROXY`/`HTTPS_PROXY` or `~/.docker/config.json`, see [Docker's proxy docs](https://docs.docker.com/engine/cli/proxy/)) — not `make`. Neo4j takes ~20–40s to accept Bolt connections on a cold start; `run-local-all` waits for it (via `cypher-shell "RETURN 1"`) before starting Prax, so the reported status reflects reality.

For live-reload development use `make run-local-all-dev` — Prax runs under the Werkzeug reloader (`DEBUG=true`) and restarts when you edit its source. (Tailscale is a separate concern: `make tailscale-up`.) The manual steps below are exactly what those targets automate, in case you want to run a piece yourself.

**Prerequisites**

- **Python 3.13** and [**uv**](https://docs.astral.sh/uv/) (the package manager — not pip)
- For memory (on by default): **Qdrant** and **Neo4j**. `run-local-all` starts these in Docker for you (or uses native binaries if Docker is absent). You can skip memory entirely with `MEMORY_ENABLED=false` (STM still works; LTM degrades silently).
- **Docker** — used by `run-local-all` for Qdrant, Neo4j, and the code-execution sandbox. Not required if you run memory natively and don't need the sandbox.
  - Your user must be able to run `docker` **without `sudo`** (the Makefile calls plain `docker`). If `make run-local-all` / `make integration` reports `permission denied while trying to connect to the Docker API at unix:///var/run/docker.sock`, add yourself to the `docker` group, then start a **fresh login shell** (group changes only apply to new sessions):

    ```bash
    sudo usermod -aG docker "$USER"   # then log out and back in
    # …or activate it in the current shell without re-login:
    newgrp docker                      # (or run a single command: sg docker -c 'make integration')
    ```
- *Optional:* Ollama (only if you set `EMBEDDING_PROVIDER=ollama`).

**Installing the dev toolchain (Ubuntu)**

`uv` runs the app and tests; `actionlint` is needed by `make ci`. Both install into `~/.local/bin` — make sure it's on your `PATH`.

```bash
# uv — the package manager (run/sync/test)
curl -LsSf https://astral.sh/uv/install.sh | sh

# actionlint — GitHub-workflow linter, required by `make ci`
# Option A — build from source (needs Go ≥ 1.25):
GOBIN="$HOME/.local/bin" go install github.com/rhysd/actionlint/cmd/actionlint@latest
# Option B — no Go? grab a prebuilt binary (see https://github.com/rhysd/actionlint/blob/main/docs/install.md):
#   bash <(curl -s https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash) latest "$HOME/.local/bin"
```

With both on your `PATH`, **`make ci`** (actionlint + `ruff` + the layer linter + pytest) is the pre-commit gate — green locally means green in CI. To run the sandbox with or without it, see [`SANDBOX_ENABLED`](#sandbox-caveat).

#### 1. Install Prax

```bash
git clone https://github.com/praxagent/prax.git && cd prax
uv sync --python 3.13                     # install deps into a local venv
mkdir -p static/temp
cp .env-example .env                       # then edit — see step 3
```

#### 2. Start the backing services (memory)

Prax defaults already point at localhost (`QDRANT_URL=http://localhost:6333`, `NEO4J_URI=bolt://localhost:7687`), so you just need the two datastores listening on those ports.

**Qdrant** — single static binary, no dependencies ([releases](https://github.com/qdrant/qdrant/releases)):

```bash
./qdrant                                   # serves HTTP on :6333, gRPC on :6334
```

**Neo4j** — Community Edition 5.x, requires a JDK 21 on the host ([download](https://neo4j.com/deployment-center/)). Set the password Prax expects and enable the APOC plugin:

```bash
neo4j-admin dbms set-initial-password prax-memory   # matches NEO4J_PASSWORD default
# enable APOC: copy the bundled apoc jar from labs/ into plugins/, then:
neo4j console                              # Bolt on :7687, browser UI on :7474
```

> Prefer not to install these directly? You can run just the two datastores as standalone containers and still run Prax itself without Docker:
> ```bash
> docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant
> docker run -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/prax-memory -e NEO4J_PLUGINS='["apoc"]' neo4j:5
> ```
> Or skip memory altogether: set `MEMORY_ENABLED=false` in `.env` and skip this step.

#### 3. Configure `.env`

At minimum:

| Variable | Required? | Notes |
|----------|-----------|-------|
| `FLASK_SECRET_KEY` | **Yes** | Hard requirement — Prax won't import settings without it. Use any strong random string. |
| `OPENAI_KEY` | Yes (or `ANTHROPIC_KEY`) | LLM provider. `OPENAI_KEY` also covers the default embeddings. |
| `PRAX_USER_ID` | No (without Docker) | Only required in Docker. Without it, Prax defaults to a local workspace. |
| `RUNNING_IN_DOCKER` | **Leave unset** | Setting this flips on Docker-only code paths (the `PRAX_USER_ID` guard, the persistent-sandbox sidecar). Keep it out of your `.env`. |
| `NEO4J_PASSWORD` | No | Defaults to `prax-memory` — match whatever you set in step 2. |

#### 4. Run Prax

```bash
uv run python app.py                       # serves the Flask API on http://localhost:5001
```

That's a fully working Prax over **Discord / SMS / voice**. The TeamWork web UI is a separate process — see below.

#### 5. (Optional) Run TeamWork without Docker

TeamWork is **off by default** in this setup (`TEAMWORK_ENABLED=false`). To use the web UI without Docker, run its repo separately:

```bash
git clone https://github.com/praxagent/teamwork.git ../teamwork && cd ../teamwork
# build the frontend (Node 22+):
cd frontend && npm ci && npm run build && cd ..
# start the backend (FastAPI/uvicorn on :8000):
DATABASE_URL="sqlite+aiosqlite:///./vteam.db" \
WORKSPACE_PATH="$(pwd)/../prax/workspaces" \
PRAX_URL="http://localhost:5001" \
CORS_ORIGINS='["http://localhost:3000","http://localhost:5173"]' \
python -m teamwork.cli
```

Then point Prax at it — add to **Prax's** `.env` and restart `app.py`:

```env
TEAMWORK_ENABLED=true
TEAMWORK_URL=http://localhost:8000
TEAMWORK_API_KEY=<the shared key — see below>
```

**The shared key is required.** TeamWork's external API refuses requests (503)
when no credential is configured, so Prax can't post without it. Generate one:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put **the same value in both repos** — Prax sends it, TeamWork checks it:

| File | Variable |
|---|---|
| `prax/.env` | `TEAMWORK_API_KEY=<value>` |
| `teamwork/.env` | `EXTERNAL_API_KEY=<value>` |

If they drift apart Prax gets `401 Invalid API key`; if neither is set, `503`.
(TeamWork also supports a per-agent credential registry — one token bound to one
agent identity plus its own capability set — which is what you want once more
than one agent is posting. See TeamWork's README.)

For frontend hot-reload during development, run `npm run dev` in `teamwork/frontend` (Vite on **:5173**, proxies `/api` and `/ws` to the backend on :8000) instead of building static assets.

> **Note:** TeamWork's in-browser terminal `docker exec`s into the sandbox container, so it expects Docker. Running TeamWork without Docker gives you chat, Kanban, file browser, and execution graphs; the terminal/desktop/browser tabs need the sandbox (next caveat).

#### Sandbox caveat

The code-execution sandbox is itself a Docker container — and it now lives in its own repo, [**prax-sandbox**](https://github.com/praxagent/prax-sandbox) (a sibling directory; Prax depends on it as `prax_sandbox_client`). So "fully Docker-free" means **no sandbox**: set `SANDBOX_ENABLED=false` (or simply don't run the sandbox container) and Prax runs as a pure harness — sandbox features (package auto-install, the coding agents OpenCode / Claude Code / Codex, the in-browser terminal, the noVNC desktop, the Chrome screencast, `run_python`, and the `delegate_sandbox` / `delegate_desktop` spokes) are unavailable, and no sandbox tools are registered. Core Prax (chat, memory, notes, scheduling, channels) runs fine without it. To run a sandbox locally, build its image from the sibling repo (`docker compose up` does this automatically; or `cd ../prax-sandbox && make build`). To run it on a **remote box**, see [providing Prax a sandbox](docs/infrastructure/sandbox.md) and the prax-sandbox repo's `docs/remote.md`.

#### Ports

| Port | Service | Started by |
|------|---------|-----------|
| **5001** | Prax Flask API | `uv run python app.py` |
| **6333 / 6334** | Qdrant HTTP / gRPC | you (step 2) |
| **7687 / 7474** | Neo4j Bolt / browser UI | you (step 2) |
| **8000** | TeamWork API (+ Swagger at `/docs`) | TeamWork backend (step 5) |
| **3000** | TeamWork web UI | TeamWork (served from :8000 build, or Vite proxy) |
| **5173** | TeamWork Vite dev server | `npm run dev` (frontend dev only) |
| **11434** | Ollama | you (only if `EMBEDDING_PROVIDER=ollama`) |

#### Channels

Set up a channel in Prax's `.env`:
- **TeamWork web UI:** run it separately (step 5), then open http://localhost:3000
- **Discord (free):** `DISCORD_BOT_TOKEN` + `DISCORD_ALLOWED_USERS`
- **Twilio (paid):** `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` + `NGROK_URL`

#### Remote access

The [Tailscale / HTTPS](#remote-access-tailscale--https) options below also apply without Docker, with one adjustment: the Docker-oriented commands assume the container's host ports (`:3000`, `:3002`), but here Prax serves on `:5001` and TeamWork on `:8000`. Substitute accordingly.

- **SSH tunnel** — works unchanged; just forward those ports:
  ```bash
  ssh -L 8000:localhost:8000 -L 5001:localhost:5001 <remote-host>
  # then open http://localhost:8000 (TeamWork) — localhost is a secure context, so the Desktop/Browser tabs work
  ```
- **Tailscale** — the `make tailscale-*` targets and the sidecar's [`serve-config.json`](tailscale/serve-config.json) map to the Docker ports; point them at `:8000`/`:5001` instead before using them without Docker.

---

## Features

Prax is a multi-channel AI assistant powered by a LangGraph ReAct agent. It connects through the **TeamWork** web UI and/or **Discord** and/or **Twilio** (voice + SMS), remembers everything in SQLite, and can modify its own tools at runtime.

| Category | Highlights |
|----------|-----------|
| **Channels** | [TeamWork](https://github.com/praxagent/teamwork) web UI (Slack-like chat, Kanban, terminal, browser, file browser, execution graphs, live agent output), Discord bot (free, WebSocket), Twilio voice + SMS (webhooks), cross-channel mirroring (Discord/SMS → TeamWork #discord/#sms channels) |
| **Agent** | LangGraph ReAct loop, 97+ built-in tools (extensible via plugins), dedicated sub-agents (self-improvement, plugin engineering, content authoring, research, coding), watchdog supervisor, automatic checkpoint & retry on failures |
| **Memory** | Two-layer human-like memory: STM scratchpad + LTM with Qdrant vector store (semantic recall) and Neo4j knowledge graph (entity relations, multi-hop reasoning). Hybrid retrieval via RRF fusion, Ebbinghaus-inspired forgetting curve, automatic consolidation. Plus: SQLite conversations, git-backed workspaces, user notes, to-do lists |
| **Notes** | Conversation-to-note publishing (Hugo pages with KaTeX, mermaid, syntax highlighting), iterative updates, searchable index, shareable URLs, bidirectional knowledge graph links |
| **Documents** | PDF extraction (arXiv, URLs, attachments), web page summaries, YouTube transcripts, LaTeX compilation, URL-to-note / PDF-to-note / arXiv-to-note pipelines |
| **Research** | Research projects (group notes, links, sources), RSS/Atom feed subscriptions, conversation history search across sessions |
| **Code** | Always-on Docker sandbox with [OpenCode](https://opencode.ai/), auto-installs packages, multi-model support, round-based budget control |
| **Linux Desktop** | Full graphical desktop (Xvfb + Fluxbox + noVNC) accessible via VNC. Prax can launch GUI apps (VS Code, Chromium), interact with them programmatically (screenshot, click, type), and install software. Users see everything through TeamWork's Desktop tab |
| **Self-Upgrading** | Prax auto-escalates his intelligence tier when stuck. If he doesn't have a tool, he writes Python. If that fails, he uses the desktop. Never gives up |
| **Scheduling** | Cron jobs (YAML), one-time reminders, timezone-aware delivery |
| **Browser** | Playwright automation, persistent login profiles, VNC for manual login, credential management |
| **Self-improvement** | Hot-swappable plugin system (sandbox + auto-rollback), self-modifying code via PRs, QLoRA fine-tuning on conversation history |
| **Models** | OpenAI, Anthropic, Google Vertex, Ollama, local vLLM — per-component routing |
| **Plugins** | Folder-per-plugin, git submodule imports from public repos, security scanning, workspace push to private remote, auto-generated catalog |
| **File sharing** | Opt-in file publishing via ngrok (shareable links for videos, PDFs), Twilio media serving |

## TeamWork Web UI

[**TeamWork**](https://github.com/praxagent/teamwork) is an agent-agnostic collaboration shell — a Slack-like web interface that gives Prax a visual frontend. It runs as a separate container alongside Prax and connects via the External Agent API.

**What you get:**

- **Real-time chat** — public channels (#general, #engineering, #research) and private DMs with Prax, with typing indicators and WebSocket updates
- **Channel mirroring** — Discord and SMS conversations are mirrored to dedicated #discord and #sms channels in TeamWork, so you can follow cross-channel conversations in one place
- **Kanban board** — task management with drag-and-drop columns (pending / in progress / review / completed). Prax creates, assigns, and completes tasks automatically as it works through plans
- **Execution graphs** — real-time visualization of agent delegation trees. Watch LangGraph execution as it happens: see which spokes are running, tool call counts, timing, and status. Click any node to inspect its details and live output
- **Live agent output** — terminal-style view of each agent's real-time execution output (Observability > Live Agents tab). Select any agent to watch its work stream
- **In-browser terminal** — full PTY shell into the sandbox container, or launch Claude Code directly from the UI
- **Browser screencast** — live view of the headless Chrome running in the sandbox, with mouse/keyboard passthrough
- **File browser** — browse and manage workspace files
- **Multi-agent status** — see which role agents (Planner, Executor, Researcher, etc.) are active

TeamWork is included in the default `docker-compose.yml`. After `docker compose up --build`, open **http://localhost:3000**. API docs (Swagger) are at **http://localhost:8000/docs**.

For standalone use or integration with other agents, see the [TeamWork repository](https://github.com/praxagent/teamwork).

---

## Documentation

Detailed documentation is organized in a hub-and-spoke structure under [`docs/`](docs/):

### [Architecture](docs/architecture/README.md)

System overview, five-layer design, request flows (SMS, Discord, TeamWork), workspace layout, and the hub-and-spoke orchestrator pattern.

### [Agents](docs/agents/README.md)

Agent delegation (spoke agents, sub-hubs), self-improving fine-tuning (vLLM + Unsloth + LoRA), self-modification via PRs, and LangGraph checkpointing.

### [Infrastructure](docs/infrastructure/README.md)

How to provide Prax a sandbox (local or remote — the sandbox itself lives in the separate [prax-sandbox](https://github.com/praxagent/prax-sandbox) repo), Playwright browser automation, Grafana observability stack (Tempo traces, Prometheus metrics, Loki logs — `--profile observability`), two-layer memory system (STM + LTM with Qdrant, Neo4j, hybrid retrieval), and Docker Compose configuration.

### [Security](docs/security/README.md)

Plugin trust tiers, subprocess isolation for imported plugins, capabilities proxy, tool risk classification, supply chain hardening, and full configuration reference.

### [Guides](docs/guides/README.md)

Setup and prerequisites, extending the agent (plugin system, manual tool registration), testing (unit, e2e, integration, A/B), channel setup (TeamWork, Discord, Twilio), and troubleshooting.

### [Research](docs/research/README.md)

Academic foundations for agentic workflow design — 19 sections covering planning, reflexion, orchestration, anti-hallucination, tool overload, content pipelines, plugin sandboxing, Thompson Sampling, Active Inference, and external benchmarking. Each finding is empirically validated and mapped to Prax's implementation.

---

## Memory System

Prax has a two-layer, research-grounded memory system inspired by human cognition: a fast **short-term memory** (STM) for immediate context, and a scalable **long-term memory** (LTM) for durable recall across conversations.

- **STM** — per-user JSON scratchpad, always available (no infra needed), auto-injected into context
- **LTM** — Qdrant vector store (dense + sparse embeddings) + Neo4j knowledge graph (entities, relations, temporal events, causal links), fused via query-adaptive weighted RRF
- **Consolidation** — scheduled pipeline extracts entities/relations/facts via LLM, validates with confidence gate (≥0.6 → LTM, below → STM pending review), applies dual decay (Ebbinghaus time + interaction-based)
- **Embedding providers** — OpenAI (default), Ollama (local/offline), or fastembed (in-process) — no data leaves your machine if you don't want it to
- **10 agent tools** — STM read/write/delete, LTM remember/recall/forget, entity lookup, graph query, consolidation, stats

`MEMORY_ENABLED=true` is the default. Memory infrastructure (Qdrant, Neo4j) runs as first-class services. Set `MEMORY_ENABLED=false` to disable. Without memory, STM still works — LTM degrades silently.

[Full documentation →](docs/infrastructure/memory.md)

---

## Agent Improvement Loop

Prax implements a trace-centered feedback loop that turns production failures into permanent regression guards:

```
feedback → failure journal → eval runner → verified fix
```

- **Feedback capture** — Users rate agent messages (thumbs up/down) via TeamWork. Negative feedback automatically creates a failure journal entry with the full execution trajectory.
- **Failure journal** — Stores observed failures in JSONL (always available) + Neo4j (graph queries, tool relationships) + Qdrant (semantic similarity search). Auto-classifies failures: wrong tool, hallucination, incomplete, too slow, asked instead of acting.
- **Eval runner** — Replays failure cases through the current agent and uses an LLM judge to score whether the failure has been fixed. Produces pass/fail verdicts with 0.0–1.0 scores.
- **Self-improve integration** — The self-improve agent reads the failure journal, proposes targeted fixes, and runs the eval suite to verify before deploying.

Resolved failures stay as permanent regression guards — every fix adds a test case.

[Full documentation →](docs/guides/feedback-loop.md)

---

## Coding Agents

The sandbox image (in the separate [prax-sandbox](https://github.com/praxagent/prax-sandbox) repo) ships three coding agents — **Claude Code** (Anthropic), **Codex** (OpenAI), and **OpenCode** (multi-provider) — plus **VS Code** on the desktop. Prax uses these for self-improvement tasks (bug fixes, refactors, new features); the settings below are the Prax-side wiring.

### Setup

1. Set `SELF_IMPROVE_ENABLED=true` in `.env` (required — global gate for all self-modification)
2. Choose your preferred agent: `SELF_IMPROVE_AGENT=claude-code` (or `codex` / `opencode`)
3. Rebuild the sandbox: `docker compose up --build sandbox`
4. Configure via TeamWork terminal: `cd /source && claude login` (or `codex login`)

All agents have full read-write access to the codebase at `/source/` in the sandbox. Changes appear on your host machine immediately (bind mount). Agent configs persist across container rebuilds via workspace volumes.

> **Warning:** All three agents use provider API tokens and cost money per invocation. Monitor your API spend when self-improvement is enabled.

---

## Agent Autonomy

Control how independently Prax operates via `PRAX_AUTONOMY` in `.env`:

| Level | Behavior |
|-------|----------|
| `guided` | **(default)** All safety gates active. HIGH-risk tools require user confirmation. Prescriptive workflow rules enforced. Most conservative. |
| `balanced` | **(recommended)** Removes prescriptive workflow rules — Prax uses judgment. HIGH-risk tools still gated but smart auto-approve kicks in when intent is clear. Agent decides its own approach. |
| `autonomous` | Also relaxes recursion limits, allows self-tier-upgrade (agent can switch to a more capable model mid-task), and earned trust can downgrade browser tool risk levels. Most independent. |

```env
PRAX_AUTONOMY=balanced
```

If Prax keeps asking "should I proceed?" or "do you want me to...?" when the answer is obviously yes — switch from `guided` to `balanced`. The `guided` mode is designed for initial setup and untrusted environments. For daily use, `balanced` is the right default.

---

## Roadmap

> The code-execution sandbox is now its own repo — its roadmap lives in
> [**prax-sandbox**](https://github.com/praxagent/prax-sandbox#roadmap). This list covers the
> harness itself. The harness keeps only thin bridges to the sandbox
> (`prax/services/sandbox_bridge.py`, `prax/agent/sandbox_tools.py`).

### Shipped

**Agent core & models**
- [x] LangGraph hub-and-spoke ReAct agent (orchestrator + domain spokes)
- [x] Multi-provider LLM factory with mid-session switching, local vLLM, and multi-model consensus
- [x] Agent task planning (multi-step decomposition) + instruction persistence

**Reliability & resilience**
_(flag-gated; recommended settings are pre-flipped in `.env-example` per the
[2026-07-08 eval-gate run](docs/research/flag-eval-campaign-2026-07-08.md) —
every flag A/B'd against baseline, including the ones that measured WORSE and
stay off)_
- [x] Cross-provider LLM failover (rate-limit / overload / breaker-aware)
- [x] Per-dependency circuit breakers for external services
- [x] Durable checkpoints (in-memory / SQLite) with automatic retry-from-last-good-state
- [x] User-initiated resume of a failed/timed-out turn from saved checkpoints
- [x] Within-turn recovery-context injection on retry after a tool-chain failure
- [x] Multi-perspective (4-angle) error recovery + tool-call loop detection
- [x] Health watchdog + append-only telemetry with self-repair advisories

**Security & governance**
- [x] Single governance choke point — risk classify + arg scrub + confirm gate + audit + budget
- [x] Deny-by-default tool boundaries (unknown tools → HIGH) + scoped HIGH-risk confirmation
- [x] SSRF egress guard (blocks internal/metadata addresses, per-redirect-hop revalidation)
- [x] Hard plugin-activation gate for security-flagged imports
- [x] Earned-trust relaxation + configurable autonomy profiles (guided / balanced / autonomous)
- [x] Deterministic claim-auditor (numeric + grounding checks) over final responses

**Memory, knowledge & retrieval**
- [x] Two-layer memory: bounded auditable STM + Qdrant/Neo4j LTM with bi-temporal consolidation + dual decay
- [x] Hybrid retrieval (weighted RRF over dense + sparse + graph) with neighbourhood expansion
- [x] Retrieval precision: LLM relevance rerank + query expansion (paraphrase/HyDE variants)
- [x] Hybrid (dense+sparse) semantic search over knowledge-graph concepts
- [x] OKF (Open Knowledge Format) export/import bridge for portable interchange
- [x] Durable structured-memory ledger that works without vector/graph infra
- [x] Dynamic user notes (per-user preferences and personality)

**Evaluation & observability**
- [x] Reference-free live-traffic eval, decomposed into grounding / relevancy / correctness axes
- [x] Failure-replay eval runner + regression suite (LLM judge); `make eval` quality gate
- [x] Nightly continuous-eval job publishing `prax_eval_quality` gauges to Prometheus
- [x] OpenTelemetry / Prometheus metrics + tracing; observability-as-tools (agent queries its own health)
- [x] Semantic search over past execution traces ("have I solved this before?")

**Prompting & routing**
- [x] Selective system-prompt assembly (drops unneeded topic sections on simple turns)
- [x] Intent-clarification gate (asks one question on ambiguous-and-irreversible requests)
- [x] Thompson Sampling tier bandit + difficulty-driven routing
- [x] Metacognitive failure profiles injected as prompt warnings + self-verification of outputs

**Plugins & extensibility**
- [x] Plugin system — folder-per-plugin, hot-swap, subprocess isolation, capabilities proxy, auto-rollback
- [x] Auto-generated plugin catalog (import-free metadata parse)
- [x] Separate git repo for agent-authored plugins (push + cherry-pick workflow)
- [x] Self-authored-tool registry tracking rationale / state / performance

**Interop (MCP)**
- [x] MCP server exposing a curated, governed tool subset to other agents
- [x] Per-caller identity + per-caller allowlist; fail-closed bearer endpoint

**Channels, identity & sharing**
- [x] Unified UUID identity with provider linking across SMS / Discord / TeamWork
- [x] Twilio SMS + voice, Discord bot, configurable agent name
- [x] TeamWork web-UI integration; public share registry (per-file/course/note via ngrok URL)

**Tasks, scheduling & Library**
- [x] Background task runner — auto-executes Kanban + todo items assigned to the agent
- [x] Library: hierarchical Project → Notebook → Note knowledge base with Kanban
- [x] Per-space rolling progress log (hard char cap + LLM compaction)
- [x] Recurring schedules (timezone-aware cron) + one-time reminders + user to-do list

**Workspace, documents & readers**
- [x] Git-backed per-user workspace with context injection
- [x] PDF extraction + document pipelines (LaTeX render, Mermaid validate, Hugo publishing)
- [x] Content readers (arXiv, YouTube, audio transcription, news) + lightweight URL fetch
- [x] Browser automation (Patchright: navigate, forms, stored-credential login)

**Teaching & self-improvement**
- [x] Faculty of professor personas teaching adaptive one-lesson-at-a-time courses
- [x] Self-improving fine-tuning (trajectory export + QLoRA/Unsloth + vLLM adapter hot-swap)
- [x] Self-modification (staging clone → verify → deploy / PR) — executed via the sandbox

**Deployment**
- [x] Docker Compose (lite / full / GPU) + Kubernetes Helm chart + operator CRDs (`PraxInstance` / `PraxWorkspace`)

### Planned

- [ ] Apple Silicon (MLX) local-inference backend
- [ ] Schedule firing with workspace file attachments (e.g. daily PDF digest)
- [ ] Discord voice channel support (join, listen, speak)
- [ ] Multi-step browser workflows (recorded / replayable recipes)
- [ ] Adapter A/B testing (champion / challenger LoRA evaluation)
- [ ] SSRF guard DNS-rebinding hardening (pin the resolved IP into the socket)
- [ ] MCP Streamable-HTTP streaming / SSE responses
- [ ] Durable cross-restart checkpoints by default + automatic "resume?" prompt

## License

[Apache License 2.0](LICENSE)
