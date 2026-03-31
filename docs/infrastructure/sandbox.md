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

### Alternatives Evaluated

| Option | Verdict |
|--------|---------|
| **NVIDIA OpenShell** | Wraps the agent (security sandbox), doesn't provide code execution as a tool. Wrong direction of control. |
| **E2B** | Cloud-only, pay-per-second, no self-hosting. Good API but sends user data to third party. |
| **Daytona** | Self-hostable, 90ms sandbox creation, built-in Git/LSP/MCP. Strong runner-up — upgrade path if Docker management gets unwieldy. |
| **Docker SDK + custom sub-agent** | Full control but requires building everything OpenCode already has. |
| **Docker SDK + OpenCode** | **Selected.** Best balance of capability, simplicity, and self-hosting. |
