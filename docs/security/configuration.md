# Configuration

[← Security](README.md)

All runtime config is centralized in `.env` and validated via Pydantic (`prax/settings.py`). Copy `.env-example` and fill in your values:

```bash
cp .env-example .env
```

Key fields:

| Variable | Purpose | Default |
|----------|---------|---------|
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | Twilio console credentials (not needed if Discord-only) | `None` |
| `OPENAI_KEY` | OpenAI API key | *(required unless using other provider)* |
| `ANTHROPIC_KEY` | Anthropic API key (for sandbox coding agent) | `None` |
| `LLM_PROVIDER` | LLM provider: `openai`, `anthropic`, `google_vertex`, `ollama`, `vllm` | `openai` |
| `BASE_MODEL` | Model name for the main agent | `gpt-4o` |
| `AGENT_NAME` | Display name for the agent across all channels, greetings, and prompts | `Prax` |
| `PHONE_TO_NAME_MAP` | JSON: `{"+15551234567": "Alice"}` — whitelists callers | `None` |
| `PHONE_TO_EMAIL_MAP` | JSON: `{"+15551234567": "alice@example.com"}` | `None` |
| `NGROK_URL` | HTTPS base URL from ngrok | `None` |
| `WORKSPACE_DIR` | Path to workspace root | `./workspaces` |
| **Sandbox** | | |
| `SANDBOX_IMAGE` | Docker image for sandbox | `prax-sandbox:latest` |
| `SANDBOX_TIMEOUT` | Max sandbox session duration (seconds) | `1800` |
| `SANDBOX_MAX_CONCURRENT` | Max simultaneous sandbox sessions | `5` |
| `SANDBOX_DEFAULT_MODEL` | Default model for sandbox coding | `anthropic/claude-sonnet-4-5` |
| `SANDBOX_MAX_ROUNDS` | Max message rounds per sandbox session | `10` |
| `SANDBOX_MEM_LIMIT` | Container memory limit | `1g` |
| `SANDBOX_CPU_LIMIT` | Container CPU limit (nanocpus) | `2000000000` |
| **Fine-Tuning (optional)** | | |
| `FINETUNE_ENABLED` | Enable self-improving fine-tuning | `false` |
| `VLLM_BASE_URL` | vLLM server URL | `http://localhost:8000/v1` |
| `LOCAL_MODEL` | Local model name for vLLM inference | `Qwen/Qwen3-8B` |
| `FINETUNE_BASE_MODEL` | Unsloth model for QLoRA training | `unsloth/Qwen3-8B-unsloth-bnb-4bit` |
| `FINETUNE_OUTPUT_DIR` | Directory for LoRA adapters | `./adapters` |
| `FINETUNE_MAX_STEPS` | Training steps per run | `60` |
| `FINETUNE_LEARNING_RATE` | QLoRA learning rate | `2e-4` |
| `FINETUNE_LORA_RANK` | LoRA rank (higher = more capacity) | `16` |
| **Browser (optional)** | | |
| `BROWSER_HEADLESS` | Run Chromium in headless mode | `true` |
| `BROWSER_TIMEOUT` | Default page timeout (ms) | `30000` |
| `SITES_CREDENTIALS_PATH` | Path to `sites.yaml` credentials file | `None` |
| `BROWSER_PROFILE_DIR` | Directory for persistent browser profiles (cookies/sessions); recommended for x.com/Twitter support | `None` |
| `BROWSER_VNC_ENABLED` | Enable VNC-based manual login sessions | `false` |
| `BROWSER_VNC_BASE_PORT` | Base port for VNC servers | `5900` |
| **Self-Improvement (optional)** | | |
| `SELF_IMPROVE_ENABLED` | Enable self-modification via staging clone + verify + deploy | `false` |
| `SELF_IMPROVE_REPO_PATH` | Path to the repo (default: cwd) | `None` |
| **Discord (optional)** | | |
| `DISCORD_BOT_TOKEN` | Discord bot token from Developer Portal | `None` |
| `DISCORD_ALLOWED_USERS` | JSON: `{"123456789": "Alice"}` — maps Discord user IDs to names | `None` |
| `DISCORD_ALLOWED_CHANNELS` | Comma-separated channel IDs the bot responds in (empty = DMs + all visible) | `None` |
| `DISCORD_TO_PHONE_MAP` | JSON: `{"discord_id": "+phone"}` — link Discord to Twilio identity | `None` |

## Channel Setup

You need at least one messaging channel. You can run multiple simultaneously.

### Option A: TeamWork Web UI (Included)

[TeamWork](https://github.com/praxagent/teamwork) is included in `docker-compose.yml` and starts automatically. No extra configuration needed.

```bash
docker compose up --build    # TeamWork is at http://localhost:3000
```

TeamWork provides Slack-like chat channels, a Kanban board, an in-browser terminal, browser screencast, and a file browser. Prax connects to it automatically on startup via the `TEAMWORK_URL` environment variable.

To link TeamWork conversations with your SMS/Discord identity (shared workspace and memory), set `TEAMWORK_USER_PHONE` in `.env` to your phone number.

| Variable | Default | Description |
|----------|---------|-------------|
| `TEAMWORK_URL` | `http://teamwork:8000` | TeamWork API URL (set by docker-compose) |
| `TEAMWORK_API_KEY` | *(empty)* | API key for authentication (optional) |
| `TEAMWORK_USER_PHONE` | *(empty)* | Phone number to share workspace with SMS/Discord |

### Option B: Discord (Free)

No ngrok, no per-message costs. The bot connects to Discord via WebSocket.

#### Step 1: Create a Discord Application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application** (top right).
3. When asked "What brings you to the Developer Portal?", select **Build a Bot**.
4. Give it a name (e.g., "Prax") and click **Create**.

#### Step 2: Configure the Bot

1. In your application, go to the **Bot** tab (left sidebar).
2. Click **Reset Token** and **copy** the token. You'll only see it once — save it now.
3. Scroll down to **Privileged Gateway Intents** and enable:
   - **Message Content Intent** (required — the bot needs to read message text)
4. Under **Authorization Flow**, keep **Public Bot** checked (it just means anyone with the invite link can add it — you control who can actually talk to it via `DISCORD_ALLOWED_USERS`).

#### Step 3: Create a Discord Server

If you don't already have a server to add the bot to:

1. Open Discord (desktop app or browser).
2. Click the **+** button at the bottom of the server list (left sidebar).
3. Select **Create My Own** → **For me and my friends** (or any option).
4. Name it (e.g., "Prax AI") and click **Create**.

#### Step 4: Invite the Bot to Your Server

1. Back in the [Developer Portal](https://discord.com/developers/applications), open your application.
2. Go to the **Installation** tab (left sidebar).
2. Under **Installation Contexts**, uncheck **User Install** and keep **Guild Install** checked.
3. Under **Guild Install → Default Install Settings**, click the **Scopes** dropdown and add `bot`.
4. A **Permissions** dropdown appears — add:
   - Send Messages
   - Read Message History
   - Attach Files
   - Add Reactions
   - Embed Links
   - View Channels
5. Click **Save Changes**.
6. Now go to the **OAuth2** tab (left sidebar). Copy the **Install Link** (or use the URL Generator with `bot` scope if you prefer).
7. Open the link in your browser, select your server, and click **Authorize**.

#### Step 5: Find Your Discord User ID

You need your Discord user ID (a long number) for the allow list:

1. Open Discord → **User Settings** (gear icon) → **Advanced** → enable **Developer Mode**.
2. Close settings, then **right-click your own name** in any chat → **Copy User ID**.
3. It'll be something like `123456789012345678`.

#### Step 6: Configure `.env`

```bash
# Paste the bot token from Step 2
DISCORD_BOT_TOKEN=MTIz...your_token_here

# Map Discord user IDs to display names (JSON)
DISCORD_ALLOWED_USERS={"123456789012345678": "Alice"}

# Optional: restrict to specific channels (comma-separated channel IDs)
# If empty, the bot responds to DMs and all channels it can see
DISCORD_ALLOWED_CHANNELS=
```

#### Step 7: Identity Linking (automatic for single users)

If you have **one Discord user** and **one phone user** in your config, they are **automatically linked** — Discord messages share the same conversation history and workspace as SMS. You'll see this in the logs:

```
Auto-linking Discord user 123... → +1555... (single user on both channels).
```

**Multiple users?** Set the mapping explicitly:

```bash
# Maps Discord user IDs to PERSONAL phone numbers (from PHONE_TO_NAME_MAP).
# This is YOUR number that you text/call FROM — NOT the Twilio ROOT_PHONE_NUMBER.
DISCORD_TO_PHONE_MAP={"123456789012345678": "+15551234567", "987654321098765432": "+15559876543"}
```

**Don't want linking?** Opt out explicitly:

```bash
DISCORD_TO_PHONE_MAP=false
```

Without linking, Discord gets its own separate conversation history and workspace.

#### Step 8: Start

```bash
uv run python app.py
```

The Discord bot starts automatically if `DISCORD_BOT_TOKEN` is set. You'll see `Discord bot connected as Prax#1234` in the logs. DM the bot or message in a channel to start chatting.

> **Discord-only setup:** If you don't want Twilio at all, you can skip `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` entirely. The app works with just Discord.

### Option C: Twilio (Voice + SMS)

Requires a Twilio account and ngrok for webhook forwarding.

1. Start the Flask server locally (see Running below).
2. In another terminal, run ngrok against the Flask port (default 5001):
   ```bash
   ngrok http 5001
   ```
3. Copy the HTTPS forwarding URL from ngrok output and set `NGROK_URL` in `.env`.
4. In the Twilio console open **Phone Numbers → Active Numbers → [your number] → Voice & Fax**:
   - Set **A Call Comes In** to `Webhook` with URL `https://<ngrok-domain>/transcribe` using POST.
5. Under **Messaging** set **A Message Comes In** to `Webhook` with URL `https://<ngrok-domain>/sms` using POST.

> **Note:** US phone numbers require A2P 10DLC registration for SMS. Consider a toll-free number or use Discord to avoid this entirely.

## Database

By default the SQLite database lives at `conversations.db`. To start fresh:
```bash
rm -f conversations.db
uv run python -c "from prax.conversation_memory import init_database; init_database('conversations.db')"
```

## Running the App

```bash
uv run python app.py
```
The server listens on `0.0.0.0:5001` (configurable via `.env`). The scheduler starts automatically and loads any existing `schedules.yaml` files from user workspaces.

### Production / Deployment

- **Gunicorn**: `uv run gunicorn 'app:app' --bind 0.0.0.0:5001 --workers 2 --threads 4`
- **Environment**: copy `.env` to the server, point `LOG_PATH`/`DATABASE_NAME` to persistent volumes.
- **Docker**: see `Dockerfile` and `docker-compose.yml` in the repo root. The app container needs `/var/run/docker.sock` mounted for sandbox functionality.
- **TLS / DNS**: terminate TLS via ngrok (dev) or a reverse proxy (Nginx/Cloudflare/etc.).
