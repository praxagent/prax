<div align="center">

# Prax

**Your personal AI assistant — on the web, Discord, SMS, and voice.**

97+ built-in tools, extensible via self-modifying plugins. Git-backed memory. Runs on your own server.

Includes [**TeamWork**](https://github.com/praxagent/teamwork) — a Slack-like web UI with real-time chat, Kanban board, file browser, terminal, and browser screencast.

<img src="assets/prax_discord_example.jpg" alt="Prax on Discord" width="360">

*Prax remembering your timezone, fetching NPR news, and managing your workspace — all from Discord.*

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
```

**2 containers.** Bundles Prax + TeamWork + Qdrant + Neo4j + ngrok into a single image alongside the sandbox. Uses ~2-3GB RAM total. Best for local development and resource-constrained machines.

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

Two easy fixes:

**Tailscale Serve** — front Prax with HTTPS via a MagicDNS cert (recommended):

```bash
# One-time: enable HTTPS on your tailnet (admin console → DNS → HTTPS Certificates)
sudo tailscale serve --bg --https=443 http://localhost:3000
# Visit: https://<machine>.<tailnet>.ts.net/
```

Point the proxy at `3000` (TeamWork UI), not `5001` — the Desktop/Browser WS endpoints are served by TeamWork via same-origin `/api/desktop/...` and `/api/browser/...` paths, so a single HTTPS mapping covers everything. To tear it down: `sudo tailscale serve --https=443 off`.

If you've also started the **observability** profile, add a second mapping so dashboard links from the Observability tab resolve from the laptop. Note that Grafana binds the **host** port `3002` — it's the **tailnet** port that's `3001`, proxied to `localhost:3002`:

```bash
sudo tailscale serve --bg --https=3001 http://localhost:3002
```

The Observability panel derives Grafana's host from `window.location`, so `https://<machine>.<tailnet>.ts.net:3001/` is what it'll link to automatically — no env var needed. Binding Grafana to a *different* host port than the one Tailscale serves on avoids a `0.0.0.0:3001` vs tailnet-IP `:3001` conflict that would otherwise block `docker compose up grafana`.

**SSH tunnel** — works without touching Tailscale config, since browsers treat `localhost` as a secure context even over plain HTTP:

```bash
ssh -L 3000:localhost:3000 <remote-host>
# Visit: http://localhost:3000/
```

### Local development

```bash
git clone https://github.com/praxagent/prax.git && cd prax
uv sync --python 3.13                    # install deps (requires uv)
cp .env-example .env                      # configure (at minimum: OPENAI_KEY)
mkdir -p static/temp
docker build -t prax-sandbox:latest sandbox/   # optional: code execution
uv run python app.py                      # start Prax
```

Set up a channel in `.env`:
- **TeamWork web UI (included):** runs automatically via Docker Compose — open http://localhost:3000
- **Discord (free):** `DISCORD_BOT_TOKEN` + `DISCORD_ALLOWED_USERS`
- **Twilio (paid):** `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` + `NGROK_URL`

---

## Features

Prax is a multi-channel AI assistant powered by a LangGraph ReAct agent. It connects to **Discord** and/or **Twilio** (voice + SMS), remembers everything in SQLite, and can modify its own tools at runtime.

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

Docker sandbox with OpenCode, VNC desktop environment with computer-use tools (xdotool + scrot), Playwright browser automation (CDP + Playwright, VNC login, persistent profiles), Grafana observability stack (Tempo traces, Prometheus metrics, Loki logs — `--profile observability`), two-layer memory system (STM + LTM with Qdrant, Neo4j, hybrid retrieval), and Docker Compose configuration.

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

The sandbox container has three coding agents installed: **Claude Code** (Anthropic), **Codex** (OpenAI), and **OpenCode** (multi-provider). **VS Code** is also installed on the sandbox desktop for interactive editing via the VNC display. Prax uses these for self-improvement tasks — bug fixes, refactors, new features.

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

- [x] LangGraph ReAct agent with tool calling
- [x] PDF extraction pipeline (opendataloader-pdf)
- [x] Git-backed per-user workspace with agent tools
- [x] Workspace context injection in system prompt
- [x] Structural refactor (shared Twilio client, Pydantic settings, modular services)
- [x] Sandbox code execution (Docker + OpenCode)
- [x] Solution archiving and reuse
- [x] Interactive sandbox sessions (main agent <-> coding agent feedback loop)
- [x] Multi-model support with mid-session switching
- [x] Round-based budget control for sandbox sessions
- [x] Scheduled recurring messages (APScheduler + YAML + timezone-aware cron)
- [x] Self-improving fine-tuning (vLLM + Unsloth QLoRA + hot-swap)
- [x] Self-modification via staging clone + verify-then-deploy + PR workflow
- [x] Browser automation (Playwright + credential store + persistent profiles + VNC login)
- [x] Discord bot (free alternative to Twilio for text messaging)
- [x] Local model support (vLLM with OpenAI-compatible API)
- [x] Configurable agent name (AGENT_NAME)
- [x] One-time reminders (APScheduler DateTrigger)
- [x] User to-do list (natural language task management)
- [x] Link history (URL logging to workspace)
- [x] Dynamic user notes (per-user preferences and personality)
- [x] Agent task planning (multi-step decomposition)
- [x] Lightweight URL fetching (fetch_url_content with oEmbed support)
- [x] Instruction persistence (reread_instructions tool)
- [x] Plugin system (hot-swappable tools, prompts, and LLM routing with subprocess sandbox + auto-rollback)
- [x] Folder-per-plugin layout with auto-generated CATALOG.md
- [x] Reader-to-plugin migration (NPR, web summary, PDF, YouTube, arXiv, Deutschlandfunk)
- [x] Plugin repository support (separate private git repo with SSH deploy key)
- [x] Subprocess isolation for imported plugins (Phase 2 — JSON-RPC bridge, stripped env, capabilities proxy)
- [x] Adaptive tier selection via Thompson Sampling bandit
- [x] Difficulty-driven routing (signal-fused complexity estimation)
- [x] Multi-perspective error recovery (4-angle failure analysis before retry)
- [x] Metacognitive failure profiles (per-component pattern learning with confidence decay)
- [x] Self-verification (workspace file and delegation output checks before delivery)
- [x] Two-layer memory system (STM scratchpad + LTM with Qdrant vectors, Neo4j graph, weighted RRF, dual decay, validation gate, multi-graph, bi-temporal edges)
- [ ] Apple Silicon support (MLX backend as alternative to vLLM/CUDA)
- [ ] Sandbox Docker image build + integration test with live OpenCode
- [ ] Voice-triggered sandbox sessions
- [ ] Schedule firing with workspace file attachments (e.g., daily PDF digest)
- [ ] MCP server integration for sandbox tooling
- [ ] Discord voice channel support (join, listen, speak)
- [ ] Multi-step browser workflows (e.g., "check my Twitter DMs every morning")
- [ ] Adapter A/B testing (serve two adapters, compare quality metrics)

## License

[Apache License 2.0](LICENSE)
