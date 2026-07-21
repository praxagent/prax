# Troubleshooting

[← Guides](README.md)

- **Docker build "not enough free space":** Run `docker system prune -a` to remove unused images, containers, and build cache. Add `--volumes` if you also want to reclaim volume space (this deletes data in unnamed volumes). On macOS, Docker Desktop's disk image can also be resized in Settings → Resources.
- **403 from `/transcribe`:** Ensure the calling number exists in `PHONE_TO_NAME_MAP`.
- **ngrok 502 / Twilio timeout:** Confirm the Flask process is running and ngrok points to the correct port (5001 for Twilio webhooks).
- **Prax won't start / "Address already in use" on :5001 (local run):** a previous instance is still bound to the port — common when the pidfile-based `make restart-prax` killed the wrapper but left an orphaned `app.py` child. **Inspect what's holding the port + the Prax processes:**
  ```bash
  fuser 5001/tcp ; pgrep -af app.py
  ```
  Then free the port and stop stragglers, confirm clean, and start **one** instance:
  ```bash
  fuser -k 5001/tcp                     # free the port
  pkill -9 -f "python3 app.py"          # stop any lingering/reloader-thrashing app.py
  pgrep -af app.py ; ss -ltnp | grep 5001   # both should print nothing
  cd /home/ubuntu/PRAX/prax && nohup uv run --python 3.13 python app.py > .local-run/prax.log 2>&1 &
  ```
  `pkill -f "app.py"` is safe from an interactive shell (your shell's command line doesn't contain `app.py`) but **not** from `bash -c "…app.py…"`, where the pattern matches the wrapper itself. Prefer this plain start over `make restart-prax` while actively editing code — the latter runs `DEBUG=true`, whose reloader restarts on every file change.
- **Course/note URL 404 over ngrok:** Course/note pages no longer auto-publish to ngrok — they're served by TeamWork at `TEAMWORK_BASE_URL` by default. To make a specific page publicly reachable, the user must explicitly opt in (e.g. `course_publish(course_id, public=True)`), which adds an entry to `workspaces/{user}/.shares.json`. Use `workspace_list_shares` to inspect the registry.
- **Tailscale sidecar doesn't start:** Confirm both `TS_AUTHKEY` and `COMPOSE_PROFILES=tailscale` are set in `.env` — without `COMPOSE_PROFILES`, Compose silently skips the service. If it starts but doesn't appear in the tailnet, check `docker compose logs tailscale` for an auth error (most common cause: ephemeral or one-off key — must be reusable + non-ephemeral + pre-approved).
- **PDF extraction fails:** Ensure Java 11+ is installed (`java -version`).
- **LangChain provider errors:** `prax/agent/llm_factory.py` validates missing API keys; double-check `.env`.
- **Sandbox won't start:** In Docker Compose mode, check `docker compose logs sandbox` — the app waits for the sandbox health check. In local mode, verify Docker Desktop is running (`docker info`). Build the sandbox image: `docker build -t prax-sandbox:latest sandbox/`.
- **sandbox_install fails:** Only works in Docker Compose mode (`RUNNING_IN_DOCKER=true`). In local mode, install packages on your machine directly.
- **Shared file link returns 404:** The file may have been deleted or the share token revoked. Re-publish with `workspace_share_file`.
- **Schedule fires at wrong time:** Check the `timezone` field in `schedules.yaml`. Use IANA names like `America/Los_Angeles`, not abbreviations like `PST`.
- **vLLM connection refused:** Ensure vLLM is running with `--enable-lora` and `VLLM_BASE_URL` points to it.
- **Training OOM:** Reduce `FINETUNE_LORA_RANK` (8 instead of 16) or `FINETUNE_MAX_STEPS`. QLoRA should fit in 6GB VRAM.
- **Browser login fails:** Check `sites.yaml` credentials. For sites with CAPTCHAs or 2FA, use `browser_request_login` for VNC-based manual login instead.
- **VNC won't connect:** Ensure `Xvfb` and `x11vnc` are installed. Check the SSH tunnel: `ssh -NL 5901:localhost:5901 server`. Verify `BROWSER_VNC_ENABLED=true` and `BROWSER_PROFILE_DIR` is set.
- **Self-improve stuck in a loop:** Prax is limited to 3 deploy attempts per branch. If it keeps failing, it will stop automatically. To manually clear the state: delete `.self-improve-state.yaml` from the project root and restart the app. To rollback a broken deploy: tell Prax "rollback" or manually run `git revert HEAD` (if the last commit starts with `self-improve deploy:`).
- **Self-improve PR fails:** Ensure `gh` CLI is authenticated (`gh auth status`) and the repo has a remote origin.
- **Plugin sandbox fails:** The sandbox runs plugins in a subprocess of the same Python environment. If `langchain_core` or other dependencies aren't installed, plugin tests will fail. Run `uv sync` to ensure all dependencies are available.
- **Plugin auto-rollback triggers unexpectedly:** Check `plugin_status("name")` to see the failure count and threshold. Adjust `max_failures_before_rollback` in the registry if needed.
- **Workspace push fails:** Verify `PRAX_SSH_KEY_B64` in `.env` and that a remote is set via `workspace_set_remote`. The key must be base64-encoded: `cat ~/.ssh/prax_deploy_key | base64 | tr -d '\n'`. Check that the deploy key has write access to the repo. The repo must be **private** — Prax refuses to push to public repos.
- **CATALOG.md not updating:** The catalog regenerates on every `load_all()` call (startup and after any hot-swap). Check `prax/plugins/tools/CATALOG.md` or the plugin repo's `CATALOG.md`.
- **Discord bot not responding:** Verify `DISCORD_BOT_TOKEN` is set and valid. Check that **Message Content Intent** is enabled in the Developer Portal. Ensure the user's Discord ID is in `DISCORD_ALLOWED_USERS`.
- **Discord "Privileged intent" error:** Go to Developer Portal → Bot tab → enable **Message Content Intent** under Privileged Gateway Intents.

---

## TeamWork & Agent Status

- **Agent stuck as "working" in TeamWork UI / Agent World:**
  The agent's status in TeamWork didn't get reset — usually because the app crashed or restarted mid-turn. The app resets all agents to idle on startup, but if the status is already stuck, restart the app: `docker compose restart prax`. To manually reset a stuck agent via the API:
  ```bash
  # Find the project ID and agent ID
  curl -s http://localhost:8000/api/projects | python3 -m json.tool
  curl -s "http://localhost:8000/api/agents?project_id=PROJECT_ID" | python3 -m json.tool

  # Reset the stuck agent
  curl -X PATCH "http://localhost:8000/api/external/projects/PROJECT_ID/agents/AGENT_ID/status" \
    -H "Content-Type: application/json" -d '{"status":"idle"}'
  ```

- **Work Logs always empty (Total Logs: 0):**
  Activity logs require both the TeamWork external API endpoint (`POST /api/external/projects/{id}/activity`) and Prax pushing log entries. Ensure the `prax` container (which bundles TeamWork) is rebuilt after the latest changes: `docker compose up --build prax`.

- **Live output says "Agent is idle" even when working:**
  Prax pushes live output via `push_live_output()` during tool execution. If the agent shows as working but live output is empty, the orchestrator's callback handler may not be firing. Check app logs: `docker compose logs prax --tail 50`.

- **Agent World constellation not updating:**
  The constellation reads agent status from the `useAgents()` hook which polls every 10 seconds. If an agent's status changed but the visualization hasn't updated, wait a few seconds. If it's permanently stuck, see "Agent stuck as working" above.

- **TeamWork shows "Claude Code" in output labels:**
  This was a hardcoded label. After the fix, it shows the agent's actual name (e.g., "Live Prax Output"). Rebuild the `prax` container (which bundles TeamWork) to pick up the change: `docker compose up --build prax`.

- **Stale/renamed agent still visible in TeamWork:**
  If you renamed or removed an agent role from the code but it still shows in chat, the constellation, or the sidebar, it's because the old agent entry persists in TeamWork's database. Delete it via the API:
  ```bash
  # List agents to find the stale one
  PROJECT_ID="your-project-id"  # from /api/projects
  curl -s "http://localhost:8000/api/agents?project_id=$PROJECT_ID" | python3 -m json.tool

  # Delete the stale agent by ID
  curl -X DELETE "http://localhost:8000/api/agents/AGENT_ID"
  ```

## Docker & Infrastructure

- **`dependency failed to start: container is unhealthy`:**
  Check which container is unhealthy: `docker inspect --format='{{json .State.Health}}' prax-CONTAINER-1 | python3 -m json.tool`. Common causes:
  - **Ollama:** The image doesn't have `curl` or `wget`. The healthcheck uses bash's `/dev/tcp`. Ensure the compose file has the correct healthcheck.
  - **Sandbox:** the image is a pure-execution environment (no OpenCode/coding-agent server). If it reports unhealthy, check the container's own healthcheck (shell/exec reachability, desktop processes) — the coding-agent server is no longer part of the image.

- **Desktop / Browser tabs stuck or `Connection closed (code: 1006)` in the console:**
  The sandbox's `websockify` (port 6080) and CDP socat bridge (9223) stay listening even when the underlying Xvfb / x11vnc / Chromium processes die at startup — the sandbox entrypoint backgrounds them with `&>/dev/null`, so failures are silent and the container still reports healthy. Verify the desktop processes are actually running:
  ```bash
  docker compose exec sandbox ps -ef | grep -E 'Xvfb|x11vnc|chromium' | grep -v grep
  ```
  If any are missing, restart the sandbox — most commonly a stale X11 lockfile (`/tmp/.X11-unix/X99`) from an unclean shutdown blocks Xvfb:
  ```bash
  docker compose restart sandbox
  ```
  To confirm the upstream WS is actually working after a restart, test from inside the `prax` container:
  ```bash
  docker compose exec prax python3 -c "
  import asyncio, websockets
  async def t():
      async with websockets.connect('ws://sandbox:6080/websockify', subprotocols=['binary']) as ws:
          print('recv:', await asyncio.wait_for(ws.recv(), 3))
  asyncio.run(t())"
  ```
  A healthy response starts with the RFB protocol version (`RFB 003.008`). `ConnectionClosedError ... 1011 Failed to connect to downstream server` means websockify is up but x11vnc (port 5900) is not.

- **`.env` visible inside sandbox at `/source/.env`:**
  The full repo is bind-mounted at `/source/` **only** during the gated, HIGH-risk
  self-improvement flow (Prax editing its own code natively). The everyday
  persistent sandbox mounts only `/workspace`. During self-improvement the `.env`
  file is visible inside the sandbox — see
  [sandbox-execution-boundary](../security/sandbox-execution-boundary.md) for the
  `/source`-mount exposure and its mitigations.

- **Neo4j telemetry warning:**
  Set `NEO4J_dbms_usage__report_enabled=false` in the Neo4j environment in `docker-compose.yml` (already done in the default config).

- **Data lost after `docker compose down`:**
  All persistent data (memory, models, agent configs) is stored in `WORKSPACE_DIR` (default: `../workspaces/`), not Docker volumes. Only observability data (Tempo, Loki, Prometheus, Grafana) uses Docker volumes. Copy the workspace directory to migrate to a new machine.

## Coding (native — no external coding agent)

Prax codes **natively**: `run_python`, `workspace_save`/`workspace_patch`
(syntax-linted), `source_read`/`source_grep`, and `sandbox_shell`. The
self-improvement flow is likewise native (`self_improve_read/write/patch/test/
verify/deploy`). **The sandbox image no longer ships the OpenCode / Claude-Code /
Codex CLIs or a coding-agent server**, and takes no model API keys.

- **Where did the coding-session tools go?**
  The multi-round OpenCode coding-session tools (`sandbox_start`/`message`/
  `review`/`finish`/`abort`/`search`/`execute`) and the `SANDBOX_CODING_AGENT_ENABLED`
  flag were **removed** (2026-07). `delegate_sandbox` is still here, but it's now a
  headless sub-agent that writes and runs code **directly** in the container via
  `sandbox_shell` — no session lifecycle, no rounds, no archive/replay. It's
  registered whenever `SANDBOX_ENABLED`. Prax also codes natively on the host
  (`run_python`, `workspace_save`/`workspace_patch`, `source_read`/`source_grep`).

- **`SELF_IMPROVE_ENABLED` is set but the self-improve tools don't appear:**
  Tools are registered at import time. Restart the app after changing `.env`:
  `docker compose restart prax`.

## Memory System

- **Qdrant/Neo4j connection refused:**
  Ensure the services are running: `docker compose ps`. Data is stored in `WORKSPACE_DIR/<PRAX_USER_ID>/.services/qdrant/` and `WORKSPACE_DIR/<PRAX_USER_ID>/.services/neo4j/`. If the workspace directory doesn't exist, create it: `mkdir -p ../workspaces`.

- **Memory search returns no results:**
  Check that `MEMORY_ENABLED=true` and that the embedding provider is configured. For Ollama embeddings, verify the model was pulled: `docker compose logs ollama-init`.

- **Consolidation not running:**
  Consolidation runs every `MEMORY_CONSOLIDATION_INTERVAL` seconds (default: 3600 = hourly). Check logs: `docker compose logs prax | grep consolidation`.
