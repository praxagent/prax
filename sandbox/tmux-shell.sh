#!/usr/bin/env bash
# Wrapper shell that attaches to the persistent "prax" tmux session.
# Used as the login shell so terminal WebSocket reconnects restore
# the previous session state instead of spawning a fresh shell.
#
# If the tmux session doesn't exist yet, create it.
# If it does, attach to it.

SESSION_NAME="prax"

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  exec tmux attach-session -t "$SESSION_NAME"
else
  exec tmux new-session -s "$SESSION_NAME" -c /source
fi
