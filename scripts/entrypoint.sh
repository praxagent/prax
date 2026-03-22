#!/bin/bash
set -e

# Configure git identity so commits work inside the container.
# Uses env vars if set, otherwise falls back to sensible defaults.
git config --global user.email "${GIT_AUTHOR_EMAIL:-prax@localhost}"
git config --global user.name "${GIT_AUTHOR_NAME:-Prax}"

echo "[entrypoint] Waiting for ngrok tunnel and updating Twilio webhooks..."
NGROK_URL=$(uv run python scripts/update_twilio_webhooks.py)
export NGROK_URL

echo "[entrypoint] NGROK_URL=${NGROK_URL}"
echo -n "${NGROK_URL}" > /app/.ngrok_url
echo "[entrypoint] Starting Flask app (via watchdog)..."
exec uv run python scripts/watchdog.py
