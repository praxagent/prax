# Troubleshooting

[← Guides](README.md)

- **Docker build "not enough free space":** Run `docker system prune -a` to remove unused images, containers, and build cache. Add `--volumes` if you also want to reclaim volume space (this deletes data in unnamed volumes). On macOS, Docker Desktop's disk image can also be resized in Settings → Resources.
- **403 from `/transcribe`:** Ensure the calling number exists in `PHONE_TO_NAME_MAP`.
- **ngrok 502 / Twilio timeout:** Confirm the Flask process is running and ngrok points to the correct port.
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
