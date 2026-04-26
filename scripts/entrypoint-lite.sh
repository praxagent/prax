#!/bin/bash
# One-shot init for the lite Prax container.  All long-lived processes
# (Qdrant, Neo4j, TeamWork, ngrok, watchdog/Flask) are managed by
# supervisord — see scripts/supervisord-prax.conf.

set -e

# ── Data directories (mounted as volume at /data) ───────────────────
mkdir -p /data/qdrant /data/neo4j /data/teamwork /app/logs

# ── Neo4j auth (one-shot — set initial password if first boot) ──────
export NEO4J_HOME=/opt/neo4j
export NEO4J_AUTH="neo4j/${NEO4J_PASSWORD:-prax-memory}"
export NEO4J_server_directories_data=/data/neo4j

$NEO4J_HOME/bin/neo4j-admin dbms set-initial-password "${NEO4J_PASSWORD:-prax-memory}" 2>/dev/null || {
  echo "[lite] Neo4j password mismatch — resetting auth (system DB only, user data is safe)..."
  rm -rf /data/neo4j/databases/system /data/neo4j/transactions/system 2>/dev/null
  $NEO4J_HOME/bin/neo4j-admin dbms set-initial-password "${NEO4J_PASSWORD:-prax-memory}" 2>/dev/null || true
}

# ── git identity ────────────────────────────────────────────────────
git config --global user.email "${GIT_AUTHOR_EMAIL:-prax@localhost}"
git config --global user.name "${GIT_AUTHOR_NAME:-Prax}"

echo "[lite] init complete — handing off to supervisord"
exec /usr/bin/supervisord -n -c /app/scripts/supervisord-prax.conf
