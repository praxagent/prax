#!/bin/bash
# Conditional wrapper for ngrok under supervisord.
#
# ngrok only runs if the user has set NGROK_AUTHTOKEN — otherwise we
# sleep forever (supervisord requires a long-running process; a quick
# exit + autorestart=true would burn CPU in a tight loop).  Sleeping
# also lets supervisorctl status correctly report "RUNNING" when the
# token is intentionally absent.

set -e

if [ -z "${NGROK_AUTHTOKEN:-}" ]; then
  echo "[ngrok-launch] NGROK_AUTHTOKEN not set — ngrok disabled."
  echo "[ngrok-launch] Set NGROK_AUTHTOKEN in .env to enable Twilio webhooks"
  echo "[ngrok-launch] and the gated /shared/<token> route."
  exec sleep infinity
fi

exec ngrok http 5001 --log=stdout --log-level=warn
