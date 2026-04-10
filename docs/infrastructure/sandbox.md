# Sandbox Code Execution

[← Infrastructure](README.md)

### The Problem

Instead of adding infinite specialized tools (one for LaTeX, one for ffmpeg, one for data transforms...), give the agent a sandbox where it can write and execute its own code. The hardest or most common operations stay as dedicated tools; everything else the agent codes up itself.

### The Solution: Docker + OpenCode

[OpenCode](https://opencode.ai/) is an open-source coding agent (MIT, 126k+ stars) with a headless HTTP server mode (`opencode serve`). It has 15 built-in tools (bash, file edit, read, write, grep, glob, etc.), supports every major LLM provider, and has first-class session management (create, resume, fork, export).

**Always-on sandbox:** In Docker Compose deployment, the sandbox runs 24/7 alongside the app. Prax can install system packages on the fly with `sandbox_install("poppler-utils")` — no user intervention needed. For permanent additions, Prax can edit the sandbox Dockerfile and rebuild with `sandbox_rebuild()`. In local development, ephemeral containers are spun up per session instead.

**Interactive feedback loop:** The main agent and coding agent converse. If the result isn't satisfactory, the main agent can send follow-up instructions, switch models mid-session (e.g., from Claude to GPT-5), or abort and try a different approach.

**Solution reuse:** Every `sandbox_finish()` commits code to the workspace git with a `SOLUTION.md`. When a similar task comes up, the agent searches the archive and re-executes the existing solution — zero tokens burned re-solving a solved problem.

**Budget control:** Each session has a configurable round limit (`SANDBOX_MAX_ROUNDS`, default 10). The agent sees `rounds_remaining` in every response so it knows when to wrap up. After hitting the limit, only `sandbox_finish` or `sandbox_abort` are available. Timed-out messages do *not* consume a round — only successful responses count against the budget.

**Stuck-session protection:** If the coding agent inside the sandbox stops responding (e.g. infinite loop, package install hang, OOM), `send_message` tracks consecutive failures. After 3 consecutive timeouts the session is **auto-aborted** and the agent is told to start fresh. The `sandbox_message` tool also returns explicit guidance to abort on individual timeouts, preventing the main agent from looping endlessly on a stuck session.

**File sharing:** When the sandbox produces large files (videos, PDFs), Prax can publish them with `workspace_share_file()` to generate a public ngrok URL. Only explicitly published files are accessible — the rest of the workspace stays private. Links can be revoked with `workspace_unshare_file()`.

> **Security note:** Ngrok URLs are publicly reachable — anyone with the link can download the file. However, shared file URLs are protected by two layers of randomization: a 32-character hex token in the path and a UUID-randomized filename (only the file extension is preserved). This makes URLs unguessable and reveals nothing about the original file name or contents. Still, treat shared links as semi-public: share them only with intended recipients, and revoke them with `workspace_unshare_file()` when no longer needed.

### Sandbox Docker Image

Pre-built with common tools:

```dockerfile
FROM node:22-slim
RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-venv \
    texlive-latex-base texlive-latex-extra texlive-fonts-recommended latexmk \
    ffmpeg poppler-utils pandoc \
    git curl wget jq \
    && npm install -g opencode
WORKDIR /workspace
EXPOSE 4096
CMD ["opencode", "serve", "--hostname", "0.0.0.0", "--port", "4096"]
```

### VS Code

VS Code is installed in the sandbox via the Microsoft apt repository. It runs on the VNC desktop (Xvfb + Fluxbox) alongside Chromium and any other GUI apps. Prax can launch it with `desktop_open("code /workspace")` and interact with it programmatically via the desktop tools (screenshot, click, type). Users can also open VS Code directly from the noVNC iframe in TeamWork's Desktop tab.

This makes four coding environments available in the sandbox: **VS Code**, **Claude Code**, **Codex**, and **OpenCode**.

### Desktop Interaction Tools

Prax has 6 tools for computer-use — programmatic control of the sandbox's graphical desktop via `xdotool` and `scrot`:

| Tool | What It Does |
|------|-------------|
| `desktop_screenshot` | Capture the current desktop as a PNG. Returns the file path. |
| `desktop_click` | Click at (x, y) coordinates. Supports left/right/middle button and double-click. |
| `desktop_type` | Type text via simulated keystrokes with configurable delay. |
| `desktop_key` | Press key combinations (e.g., `ctrl+s`, `alt+F4`, `Return`, `Tab`). |
| `desktop_list_windows` | List all open windows with their titles and positions. |
| `desktop_open` | Launch a GUI application in the background on DISPLAY :99. |

These tools let Prax interact with any GUI application — VS Code, Chromium, file managers, or anything installed via `sandbox_install`. The typical pattern is a **screenshot-analyze-act loop**: take a screenshot, analyze what's on screen, click or type to interact, then screenshot again to verify the result.

See [Desktop](desktop.md) for a deep dive on the VNC desktop architecture and computer-use patterns.

### Package Tracking

When Prax installs packages via `sandbox_install()`, each package name is logged to `/root/.installed_packages`. This manifest is a simple newline-delimited list of apt package names.

On container rebuild, the entrypoint script reads this manifest and reinstalls any packages that aren't already present in the base image. This means user-installed packages survive `docker compose up --build` — no manual intervention needed. The manifest itself persists because `/root` is volume-mounted to the user's `.sandbox/home/` directory.

### User-Scoped Mounts

The sandbox mounts only the current user's workspace folder, not the entire workspaces directory:

```yaml
# docker-compose.yml (sandbox service)
volumes:
  - ${WORKSPACE_DIR}/${PRAX_USER_ID}:/workspace     # user's workspace files
  - ${WORKSPACE_DIR}/${PRAX_USER_ID}/.sandbox/home:/root  # persistent home dir
```

Key points:

- **`/workspace`** (singular) is the user's workspace root inside the sandbox. This is different from the app container's `/app/workspaces` which holds all users.
- **`PRAX_USER_ID`** in `.env` controls which user's workspace is mounted. Must be set before `docker compose up`.
- **`.sandbox/`** lives inside the user's workspace directory at `{workspace}/{user_id}/.sandbox/`. It holds persistent home directory contents — browser profiles, shell history, installed package manifests, coding agent configs, and desktop customizations.
- Sub-mounts pin specific config directories: `.sandbox/claude` for Claude Code, `.sandbox/codex` for Codex, `.sandbox/opencode` for OpenCode.

### tmux Persistence

The sandbox sets `$SHELL` to `tmux-shell.sh`, a wrapper that attaches to (or creates) a persistent tmux session named `prax`. This means:

- **Terminal state survives WebSocket reconnects.** Refreshing TeamWork's terminal tab, switching devices, or losing connection doesn't lose your shell history or running processes.
- **The entrypoint creates the session** on container start (`tmux new-session -d -s prax`). The tmux-shell wrapper attaches to it on each new terminal connection.
- **All terminal connections share the same session.** Multiple TeamWork tabs see the same terminal. This is intentional — the sandbox is single-user.

### Alternatives Evaluated

| Option | Verdict |
|--------|---------|
| **NVIDIA OpenShell** | Wraps the agent (security sandbox), doesn't provide code execution as a tool. Wrong direction of control. |
| **E2B** | Cloud-only, pay-per-second, no self-hosting. Good API but sends user data to third party. |
| **Daytona** | Self-hostable, 90ms sandbox creation, built-in Git/LSP/MCP. Strong runner-up — upgrade path if Docker management gets unwieldy. |
| **Docker SDK + custom sub-agent** | Full control but requires building everything OpenCode already has. |
| **Docker SDK + OpenCode** | **Selected.** Best balance of capability, simplicity, and self-hosting. |
