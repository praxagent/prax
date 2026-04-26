#!/bin/bash
# Wrapper that launches the long-lived Chrome instance under supervisord.
# Kept separate from supervisord.conf because the flag list is long and
# would be unreadable inline.
#
# This is the SAME Chrome serving both the Desktop tab (rendered via
# Xvfb → x11vnc → noVNC) and the Browser tab (CDP on :9222).  exec'd so
# supervisord supervises chromium directly (no wrapping shell PID in
# the way of stopasgroup signal propagation).

set -e

PROFILE_DIR=/root/.browser_profiles/default

exec /usr/bin/chromium-browser \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --disable-blink-features=AutomationControlled \
  --disable-popup-blocking \
  --disable-features=BlockThirdPartyCookies \
  --load-extension=/opt/prax-cast-ext \
  --remote-allow-origins=* \
  --user-agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36" \
  --remote-debugging-port=9222 \
  --user-data-dir="$PROFILE_DIR" \
  --window-size=1920,1080
