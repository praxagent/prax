# Setup

[← Guides](README.md)

## Prerequisites

- Python 3.13 (managed via [uv](https://github.com/astral-sh/uv)).
- **At least one messaging channel:**
  - **Twilio** (Voice + SMS) — requires a Twilio account, verified phone number, and ngrok for webhooks. Paid per message/minute.
  - **Discord** (text + attachments) — requires a Discord bot token (free). No ngrok needed — connects via WebSocket.
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
