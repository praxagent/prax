#!/bin/bash
# Wrapper that exports the runtime env Flask expects, then exec's the
# watchdog.  Kept separate from supervisord.conf so the env list stays
# readable and so we can do conditional setup (e.g. wait for a sibling)
# if it ever becomes necessary.

set -e

# All bundled services live on localhost inside this container.
export QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
export NEO4J_URI="${NEO4J_URI:-bolt://localhost:7687}"
export TEAMWORK_URL="${TEAMWORK_URL:-http://localhost:8000}"
export RUNNING_IN_DOCKER=true
export SANDBOX_HOST="${SANDBOX_HOST:-sandbox}"
export BROWSER_CDP_URL="${BROWSER_CDP_URL:-http://sandbox:9223}"

# If the user configured ngrok, the public URL is recorded in /app/.ngrok_url
# by scripts/update_twilio_webhooks.py at startup.  Pick it up here so the
# Twilio routes inside Flask see it on first request.
if [ -f /app/.ngrok_url ]; then
  export NGROK_URL="$(cat /app/.ngrok_url)"
fi

cd /app
exec uv run python scripts/watchdog.py
