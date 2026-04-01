#!/usr/bin/env bash
# Start Chromium headless for browser screencast, then launch opencode-ai.
set -e

# Launch Chromium in the background — binds to 127.0.0.1 only
# (Debian Chromium ignores --remote-debugging-address in headless=new mode).
chromium \
  --headless=new \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --disable-blink-features=AutomationControlled \
  --user-agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36" \
  --remote-debugging-port=9222 \
  &>/dev/null &

# Give Chromium a moment to bind the port.
sleep 1

# Expose CDP on 0.0.0.0:9223 so other containers can reach it.
socat TCP-LISTEN:9223,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:9222 &

exec opencode serve --hostname 0.0.0.0 --port 4096
