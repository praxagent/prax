#!/bin/bash
set -e

# ── Data directories ─────────────────────────────────────────────────
mkdir -p /data/qdrant /data/neo4j /data/teamwork /app/logs

# ── Start Qdrant in background ───────────────────────────────────────
qdrant --storage-path /data/qdrant --http-port 6333 --grpc-port 6334 &
echo "[prax] Qdrant starting on :6333"

# ── Start Neo4j in background ────────────────────────────────────────
export NEO4J_HOME=/opt/neo4j
export NEO4J_AUTH="neo4j/${NEO4J_PASSWORD:-prax-memory}"
export NEO4J_PLUGINS='["apoc"]'
# Memory caps — keep total Neo4j footprint under ~450 MB.
export NEO4J_server_memory_heap_initial__size=128m
export NEO4J_server_memory_heap_max__size=256m
export NEO4J_server_memory_pagecache_size=64m
$NEO4J_HOME/bin/neo4j console &
echo "[prax] Neo4j starting on :7474/:7687"

# ── Start TeamWork in background ─────────────────────────────────────
DATABASE_URL="sqlite+aiosqlite:///data/teamwork/vteam.db" \
WORKSPACE_PATH=/app/workspaces \
EXTERNAL_API_KEY="${TEAMWORK_API_KEY:-}" \
SANDBOX_CONTAINER="${SANDBOX_CONTAINER:-prax-sandbox-1}" \
CHROME_CDP_HOST=sandbox \
CHROME_CDP_PORT=9223 \
DESKTOP_VNC_URL=http://sandbox:6080 \
PRAX_URL=http://localhost:5001 \
python -m teamwork.cli &
echo "[prax] TeamWork starting on :8000"

# ── Wait for Qdrant to become healthy ────────────────────────────────
for i in $(seq 1 30); do
  curl -sf http://localhost:6333/healthz >/dev/null 2>&1 && break
  sleep 1
done
echo "[prax] Qdrant ready"

# ── Configure git identity ───────────────────────────────────────────
git config --global user.email "${GIT_AUTHOR_EMAIL:-prax@localhost}"
git config --global user.name  "${GIT_AUTHOR_NAME:-Prax}"

# ── Start ngrok if configured ────────────────────────────────────────
if [ -n "${NGROK_AUTHTOKEN:-}" ]; then
  ngrok http 5001 --log=stdout --log-level=warn &
  sleep 2
  NGROK_URL=$(curl -s http://localhost:4040/api/tunnels \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['tunnels'][0]['public_url'])" \
    2>/dev/null || echo "")
  export NGROK_URL
  echo "[prax] ngrok tunnel: ${NGROK_URL}"
fi

# ── Override env vars for localhost services ─────────────────────────
export QDRANT_URL=http://localhost:6333
export NEO4J_URI=bolt://localhost:7687
export TEAMWORK_URL=http://localhost:8000
export RUNNING_IN_DOCKER=true
export SANDBOX_HOST=sandbox
export BROWSER_CDP_URL=http://sandbox:9223

# ── Start Prax (foreground — watchdog supervises Flask) ──────────────
exec uv run python scripts/watchdog.py
