#!/bin/bash
# Wrapper that re-spawns bash if the user types `exit`.
#
# Used as the entrypoint for terminal WebSocket sessions in TeamWork.
# Without this, `exit` would close the docker exec PTY → TeamWork's
# drain task sees the process die → "[Session ended]" → next reconnect
# spawns a brand-new shell with no `cd` / `export` state.
#
# With this, `exit` just spawns a fresh bash in the same PTY.  The
# shell's environment (cwd, exported vars) does reset between
# respawns — bash exited.  But the PTY and TeamWork session stay
# alive, and the user keeps their xterm.js scrollback.
#
# Tiny sleep prevents a tight respawn loop if bash itself crashes
# immediately on startup.
set -u

while true; do
  bash -l
  sleep 0.1
done
