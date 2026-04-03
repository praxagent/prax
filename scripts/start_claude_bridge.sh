#!/usr/bin/env bash
# Start the Claude Code Bridge — runs on the HOST, not in Docker.
#
# Prax (in Docker) connects to this bridge to have multi-turn conversations
# with Claude Code working on the live codebase.
#
# Usage:
#   ./scripts/start_claude_bridge.sh                    # default port 9819
#   CLAUDE_BRIDGE_PORT=9999 ./scripts/start_claude_bridge.sh
#   CLAUDE_BRIDGE_SECRET=my-secret ./scripts/start_claude_bridge.sh
#
# The bridge must be running for Prax to use the claude_code_* tools.
# If it's not running, Prax detects this and disables those tools gracefully.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

cd "$REPO_DIR"

# Check that claude CLI is available
if ! command -v claude &> /dev/null; then
    echo "Error: 'claude' CLI not found. Install Claude Code first:"
    echo "  npm install -g @anthropic-ai/claude-code"
    exit 1
fi

export CLAUDE_BRIDGE_REPO="$REPO_DIR"

echo "=== Claude Code Bridge ==="
echo "Repo:   $REPO_DIR"
echo "Port:   ${CLAUDE_BRIDGE_PORT:-9819}"
echo "Auth:   $([ -n "${CLAUDE_BRIDGE_SECRET:-}" ] && echo "enabled" || echo "disabled")"
echo "Claude: $(claude --version 2>/dev/null || echo 'unknown')"
echo ""
echo "Prax connects via: http://host.docker.internal:${CLAUDE_BRIDGE_PORT:-9819}"
echo "Press Ctrl+C to stop."
echo "=========================="
echo ""

exec uv run python scripts/claude_bridge.py
