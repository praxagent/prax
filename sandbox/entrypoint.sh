#!/usr/bin/env bash
# Start Chromium headless for browser screencast, then launch opencode-ai.
set -e

# Persistent browser profile — without this Chrome starts in an ephemeral
# (incognito-like) context with no cookies, history, or localStorage.
PROFILE_DIR=/workspaces/browser_profiles/default
mkdir -p "$PROFILE_DIR"
rm -f "$PROFILE_DIR"/SingletonLock "$PROFILE_DIR"/SingletonCookie "$PROFILE_DIR"/SingletonSocket

# Launch Chromium in the background — binds to 127.0.0.1 only
# (Debian Chromium ignores --remote-debugging-address in headless=new mode).
chromium \
  --headless=new \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --disable-blink-features=AutomationControlled \
  --disable-popup-blocking \
  --disable-features=BlockThirdPartyCookies \
  --user-agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36" \
  --remote-debugging-port=9222 \
  --user-data-dir="$PROFILE_DIR" \
  --window-size=1920,1080 \
  &>/dev/null &

# Give Chromium a moment to bind the port.
sleep 1

# Expose CDP on 0.0.0.0:9223 so other containers can reach it.
socat TCP-LISTEN:9223,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:9222 &

exec opencode serve --hostname 0.0.0.0 --port 4096
