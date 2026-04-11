#!/bin/bash
# Combined entrypoint for lite mode — starts Qdrant, Neo4j, TeamWork,
# and ngrok in the background, waits for them, then starts Prax.

# ── Data directories (mounted as volume at /data) ───────────────────
mkdir -p /data/qdrant /data/neo4j /data/teamwork /app/logs

# ── Qdrant ──────────────────────────────────────────────────────────
QDRANT__STORAGE__STORAGE_PATH=/data/qdrant \
QDRANT__SERVICE__HTTP_PORT=6333 \
QDRANT__SERVICE__GRPC_PORT=6334 \
qdrant 2>&1 | sed 's/^/[qdrant] /' &

# ── Neo4j ───────────────────────────────────────────────────────────
export NEO4J_HOME=/opt/neo4j
export NEO4J_AUTH="neo4j/${NEO4J_PASSWORD:-prax-memory}"
export NEO4J_server_memory_heap_initial__size=128m
export NEO4J_server_memory_heap_max__size=256m
export NEO4J_server_memory_pagecache_size=64m
export NEO4J_dbms_usage__report_enabled=false
export NEO4J_server_directories_data=/data/neo4j
# Set the initial password. On a fresh DB this creates the admin user.
# On an existing DB this is a no-op (password already set), but if auth
# is mismatched we reset ONLY the "system" database (which stores auth
# credentials and roles). The "neo4j" database (user data — entities,
# relations, knowledge graph) is NEVER touched by this reset.
$NEO4J_HOME/bin/neo4j-admin dbms set-initial-password "${NEO4J_PASSWORD:-prax-memory}" 2>/dev/null || {
  echo "Neo4j password mismatch — resetting auth (system DB only, user data is safe)..."
  rm -rf /data/neo4j/databases/system /data/neo4j/transactions/system 2>/dev/null
  $NEO4J_HOME/bin/neo4j-admin dbms set-initial-password "${NEO4J_PASSWORD:-prax-memory}" 2>/dev/null
}
$NEO4J_HOME/bin/neo4j console 2>&1 | sed 's/^/[neo4j] /' &

# ── TeamWork ────────────────────────────────────────────────────────
DATABASE_URL="sqlite+aiosqlite:////data/teamwork/vteam.db" \
DEBUG=false \
WORKSPACE_PATH=/app/workspaces \
HOST_WORKSPACE_PATH="${HOST_WORKSPACE_PATH:-}" \
EXTERNAL_API_KEY="${TEAMWORK_API_KEY:-}" \
SANDBOX_CONTAINER="${SANDBOX_CONTAINER:-prax-sandbox-1}" \
CHROME_CDP_HOST="${SANDBOX_HOST:-sandbox}" \
CHROME_CDP_PORT=9223 \
DESKTOP_VNC_URL="http://${SANDBOX_HOST:-sandbox}:6080" \
PRAX_URL=http://localhost:5001 \
CORS_ORIGINS='["http://localhost:3000","http://localhost:5173"]' \
python -m teamwork.cli 2>&1 | sed 's/^/[teamwork] /' &

# ── Wait for all services ──────────────────────────────────────────
wait_for() {
  local name=$1 url=$2 max=$3
  echo "[lite] Waiting for $name..."
  for i in $(seq 1 "$max"); do
    if curl -sf "$url" >/dev/null 2>&1; then
      echo "[lite] $name ready"
      return 0
    fi
    sleep 1
  done
  echo "[lite] WARNING: $name did not become ready in ${max}s (continuing anyway)"
  return 0
}

wait_for "Qdrant"   "http://localhost:6333/healthz" 30
wait_for "Neo4j"    "http://localhost:7474/"         60
wait_for "TeamWork" "http://localhost:8000/health"   30

# ── ngrok (optional) ────────────────────────────────────────────────
if [ -n "${NGROK_AUTHTOKEN:-}" ]; then
  ngrok http 5001 --log=stdout --log-level=warn 2>&1 | sed 's/^/[ngrok] /' &
  sleep 1
  NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['tunnels'][0]['public_url'])" 2>/dev/null || echo "")
  export NGROK_URL
  echo "[lite] ngrok tunnel: ${NGROK_URL}"
fi

# ── Git config ──────────────────────────────────────────────────────
git config --global user.email "${GIT_AUTHOR_EMAIL:-prax@localhost}"
git config --global user.name "${GIT_AUTHOR_NAME:-Prax}"

# ── Environment overrides (all services on localhost) ───────────────
export QDRANT_URL=http://localhost:6333
export NEO4J_URI=bolt://localhost:7687
export TEAMWORK_URL=http://localhost:8000
export RUNNING_IN_DOCKER=true
export SANDBOX_HOST=${SANDBOX_HOST:-sandbox}
export BROWSER_CDP_URL="http://${SANDBOX_HOST:-sandbox}:9223"
export WORKSPACE_DIR=/app/workspaces

echo "[lite] All services ready. Starting Prax..."

# ── Prax (foreground) ──────────────────────────────────────────────
exec uv run python scripts/watchdog.py
