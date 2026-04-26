#!/bin/bash
# One-shot init for the combined Prax container.  All long-lived
# processes (Qdrant, Neo4j, TeamWork, ngrok, watchdog/Flask) are managed
# by supervisord — see scripts/supervisord-prax.conf.  This script just
# prepares directories, configures git/Neo4j auth, then hands off to
# supervisord as PID 1.

set -e

# ── Data directories ────────────────────────────────────────────────
mkdir -p /data/qdrant /data/neo4j /data/teamwork /app/logs

# ── Neo4j auth (one-shot — set initial password if first boot) ──────
# Set the initial password.  On a fresh DB this creates the admin user.
# On an existing DB this is a no-op.  If auth is mismatched (e.g. user
# changed NEO4J_PASSWORD without wiping data) we reset ONLY the system
# database — knowledge-graph data in the "neo4j" database is untouched.
export NEO4J_HOME=/opt/neo4j
export NEO4J_AUTH="neo4j/${NEO4J_PASSWORD:-prax-memory}"
export NEO4J_PLUGINS='["apoc"]'
export NEO4J_server_directories_data=/data/neo4j

$NEO4J_HOME/bin/neo4j-admin dbms set-initial-password "${NEO4J_PASSWORD:-prax-memory}" 2>/dev/null || {
  echo "[prax] Neo4j password mismatch — resetting auth (system DB only, user data is safe)..."
  rm -rf /data/neo4j/databases/system /data/neo4j/transactions/system 2>/dev/null
  $NEO4J_HOME/bin/neo4j-admin dbms set-initial-password "${NEO4J_PASSWORD:-prax-memory}" 2>/dev/null || true
}

# ── git identity ────────────────────────────────────────────────────
git config --global user.email "${GIT_AUTHOR_EMAIL:-prax@localhost}"
git config --global user.name  "${GIT_AUTHOR_NAME:-Prax}"

echo "[prax] init complete — handing off to supervisord"
exec /usr/bin/supervisord -n -c /app/scripts/supervisord-prax.conf
