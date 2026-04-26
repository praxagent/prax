# Setup

[← Guides](README.md)

## Prerequisites

- Python 3.13 (managed via [uv](https://github.com/astral-sh/uv)).
- **At least one messaging channel:**
  - **Twilio** (Voice + SMS) — requires a Twilio account, verified phone number, and ngrok for webhooks (Twilio's servers must reach Prax from the public internet, which the local Tailscale sidecar can't provide). Paid per message/minute.
  - **Discord** (text + attachments) — requires a Discord bot token (free). No ngrok needed — connects via WebSocket.
- **Optional, recommended for remote access:** Tailscale account + a reusable, non-ephemeral auth key. Lets you reach TeamWork over HTTPS from your laptop without exposing the host's network or installing `tailscaled` on the server. See [Configuration → Remote access](../security/configuration.md#remote-access-tailscale-sidecar).
- OpenAI (or alternate LLM) credentials.
- Java 11+ (for `opendataloader-pdf` PDF extraction).
- Docker (for sandbox code execution).
- **Optional:** NVIDIA GPU with 8GB+ VRAM + vLLM + Unsloth (for self-improving fine-tuning).
- **Optional:** Playwright (`pip install playwright && playwright install chromium`) for browser automation.
- **Optional:** `gh` CLI (for self-modification PR creation).

## Installation Details

See [Quick Start](#quick-start) at the top of this README for the fast path.

**Sandbox code execution** requires both Docker Desktop running **and** the sandbox image built (`docker build -t prax-sandbox:latest sandbox/`). Without the image, sandbox tools will fail with a "pull access denied" error — it's a local image, not on Docker Hub. If Docker itself isn't running, the agent falls back to saving source files to the workspace.

**Browser automation** requires Playwright: `uv run playwright install chromium`

On startup you'll see:
```
Starting Prax — provider=openai model=gpt-4o temperature=0.7 encoding=o200k_base
```

## Required Configuration

These `.env` settings are **required** for Docker Compose deployments:

| Variable | Purpose |
|----------|---------|
| `OPENAI_KEY` or `ANTHROPIC_KEY` | At least one LLM provider API key |
| `PRAX_USER_ID` | Your workspace directory name (e.g. `usr_alice`). The sandbox mounts only this folder for user isolation. Pick any slug — Prax creates the directory and associates it with your identity on first run. **Prax refuses to start without this.** |

## Memory System Setup

Prax has a two-layer memory system. **Short-term memory (STM)** works out of the box with no extra infrastructure. **Long-term memory (LTM)** requires Qdrant and Neo4j.

### Enabling Long-Term Memory

```bash
# Start core services + memory infrastructure
docker compose --profile memory up --build
```

Then set in `.env`:
```env
MEMORY_ENABLED=true
```

This starts Qdrant (vector store, port 6333) and Neo4j (knowledge graph, port 7474/7687).

### Choosing an Embedding Provider

Memory search quality depends on embeddings — numerical representations of text used for similarity matching. You have three choices:

**Option 1: OpenAI (default)** — Highest quality, easiest setup. Your memory text is sent to OpenAI's API for embedding. If you're already using OpenAI for LLM calls, this is the simplest path.

```env
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
```

**Option 2: Ollama (local)** — No data leaves your machine. Good quality. Requires running Ollama (included in Docker Compose).

```bash
# Start with Ollama
docker compose --profile memory --profile ollama up --build

# Pull the embedding model (one-time)
docker compose exec ollama ollama pull nomic-embed-text
```

```env
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=nomic-embed-text
OLLAMA_BASE_URL=http://localhost:11434
```

**Option 3: fastembed (in-process)** — Zero infrastructure, runs inside the Prax process. Lower quality but simplest possible setup.

```env
EMBEDDING_PROVIDER=local
```

See [Memory documentation](../infrastructure/memory.md#embedding-providers) for a detailed comparison of quality, speed, cost, and privacy trade-offs.

### Verifying Memory

After starting with memory enabled, you can verify it's working:

- **Qdrant dashboard:** [http://localhost:6333/dashboard](http://localhost:6333/dashboard)
- **Neo4j browser:** [http://localhost:7474](http://localhost:7474) (login: `neo4j` / `prax-memory`)
- **In chat:** Ask Prax "what's your memory status?" — it will use the `memory_stats` tool
