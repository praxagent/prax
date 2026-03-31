# Docker

[← Infrastructure](README.md)

### Docker Compose (recommended)

```bash
cp .env-example .env    # configure API keys
docker compose up --build
```

**Day-to-day usage** — once images are built, skip the rebuild to start in seconds:

```bash
docker compose up                         # start with existing images (fast)
docker compose up --build                 # rebuild ALL images then start
docker compose up --build app             # rebuild only the app image, start everything
docker compose up --build sandbox         # rebuild only the sandbox image, start everything
docker compose build app && docker compose up   # same idea, explicit two-step
```

Use `--build` when you've changed a Dockerfile or its dependencies (e.g. added a package). For code-only changes in dev mode, plain `docker compose up` is enough.

**Dev mode** — mount local source code so changes auto-reload without rebuilding:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

This bind-mounts `prax/`, `app.py`, `config.py`, and `scripts/` into the container and sets `DEBUG=true`, which enables Flask's Werkzeug reloader. Edit code locally, save, and the app restarts automatically. You still need `--build` if you change the Dockerfile, `pyproject.toml`, or system-level dependencies.

This starts three services:

| Service | Description |
|---------|-------------|
| **app** | Flask app (port 5001), `.env` injected, Docker socket for sandbox management |
| **sandbox** | Always-on OpenCode sandbox with Python, LaTeX, ffmpeg, poppler, pandoc. Shares `./workspaces` volume. Restarts automatically. |
| **ngrok** | Tunnel to app:5001. Prax can serve files via public URLs. |

The app waits for the sandbox health check before starting. Environment detection is automatic — `RUNNING_IN_DOCKER=true` and `SANDBOX_HOST=sandbox` are set by compose.

**Runtime capabilities in Docker mode:**
- `sandbox_install("package")` — apt-get install inside the running sandbox
- `sandbox_rebuild()` — Prax edits the Dockerfile, rebuilds the image, and restarts the container
- `workspace_share_file("path/to/file.mp4")` — publish a file with a public ngrok URL

### Standalone (without compose)

```bash
docker build -t prax .

# Ensure the database file exists (Docker will create a directory otherwise)
touch "$HOME/conversations.db"

docker run -d -p 5001:5001 --restart always \
  -v "$HOME/workspaces:/app/workspaces" \
  -v "$HOME/conversations.db:/app/conversations.db" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  prax
```

Build the sandbox image separately:
```bash
docker build -t prax-sandbox:latest sandbox/
```
