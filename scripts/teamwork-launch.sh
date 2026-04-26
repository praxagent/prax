#!/bin/bash
# Wrapper that exports the runtime env TeamWork expects, then exec's it.
# Kept separate from supervisord-prax.conf so the env list stays readable
# and so we don't lose vars when adding new ones.
#
# IMPORTANT: SANDBOX_CONTAINER must be set — TeamWork's terminal router
# uses it to `docker exec` into the sandbox.  If it's missing or empty,
# the router falls through to a local bash inside the prax container,
# which has /app/workspaces (every user's folder) mounted.  That's a
# multi-tenant isolation break, not just a UX bug.

set -e

export DATABASE_URL="sqlite+aiosqlite:////data/teamwork/vteam.db"
export DEBUG=false
export WORKSPACE_PATH=/app/workspaces
export HOST_WORKSPACE_PATH="${HOST_WORKSPACE_PATH:-}"
export EXTERNAL_API_KEY="${TEAMWORK_API_KEY:-}"
export SANDBOX_CONTAINER="${SANDBOX_CONTAINER:-prax-sandbox-1}"
export CHROME_CDP_HOST="${SANDBOX_HOST:-sandbox}"
export CHROME_CDP_PORT=9223
export DESKTOP_VNC_URL="http://${SANDBOX_HOST:-sandbox}:6080"
export PRAX_URL=http://localhost:5001
export CORS_ORIGINS="${CORS_ORIGINS:-[\"http://localhost:3000\",\"http://localhost:5173\"]}"

# Fail-fast tenancy guard.  If SANDBOX_CONTAINER resolves to empty (e.g.
# someone sets `SANDBOX_CONTAINER=` explicitly or the default above gets
# accidentally removed), TeamWork's terminal router silently falls back
# to `_run_local_terminal` — which spawns bash in THIS container, where
# /app/workspaces holds every user's workspace.  That's a multi-tenant
# isolation break, not just a UX bug.  Refuse to start so supervisord
# logs FATAL and the operator notices, instead of letting a regression
# silently re-open the hole.
if [ -z "${SANDBOX_CONTAINER:-}" ]; then
  echo "[teamwork-launch] FATAL: SANDBOX_CONTAINER is empty." >&2
  echo "[teamwork-launch] TeamWork's terminal would fall back to a local shell" >&2
  echo "[teamwork-launch] inside the prax container, exposing every user's workspace." >&2
  echo "[teamwork-launch] Set SANDBOX_CONTAINER (default: prax-sandbox-1) and retry." >&2
  exit 78  # EX_CONFIG — supervisord won't busy-restart this
fi

exec /usr/local/bin/python -m teamwork.cli
