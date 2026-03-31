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

### Docker Compose (recommended)

```bash
git clone https://github.com/praxagent/prax.git && cd prax
git clone https://github.com/praxagent/teamwork.git ../teamwork  # web UI
cp .env-example .env                      # configure (at minimum: OPENAI_KEY)
docker compose up --build                 # builds app + sandbox + TeamWork, starts everything
```

This brings up Prax, the always-on sandbox (with LaTeX, ffmpeg, poppler, pandoc, headless Chrome), [TeamWork](https://github.com/praxagent/teamwork) web UI, and ngrok — all wired together. Open **http://localhost:3000** to access TeamWork.

For developer mode (bind-mounts source code, Werkzeug auto-reloads on file changes):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build app
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
| **Memory** | SQLite conversations with auto-summarization, git-backed per-user workspaces, dynamic user notes, link history, to-do lists, task plans |
| **Notes** | Conversation-to-note publishing (Hugo pages with KaTeX, mermaid, syntax highlighting), iterative updates, searchable index, shareable URLs, bidirectional knowledge graph links |
| **Documents** | PDF extraction (arXiv, URLs, attachments), web page summaries, YouTube transcripts, LaTeX compilation, URL-to-note / PDF-to-note / arXiv-to-note pipelines |
| **Research** | Research projects (group notes, links, sources), RSS/Atom feed subscriptions, conversation history search across sessions |
| **Code** | Always-on Docker sandbox with [OpenCode](https://opencode.ai/), auto-installs packages, multi-model support, round-based budget control |
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

Docker sandbox with OpenCode, Playwright browser automation (CDP + Playwright, VNC login, persistent profiles), Grafana observability stack (traces, metrics, logs), and Docker Compose configuration.

### [Security](docs/security/README.md)

Plugin trust tiers, subprocess isolation for imported plugins, capabilities proxy, tool risk classification, supply chain hardening, and full configuration reference.

### [Guides](docs/guides/README.md)

Setup and prerequisites, extending the agent (plugin system, manual tool registration), testing (unit, e2e, integration, A/B), channel setup (TeamWork, Discord, Twilio), and troubleshooting.

### [Research](docs/research/README.md)

Academic foundations for agentic workflow design — 19 sections covering planning, reflexion, orchestration, anti-hallucination, tool overload, content pipelines, plugin sandboxing, Thompson Sampling, Active Inference, and external benchmarking. Each finding is empirically validated and mapped to Prax's implementation.

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
