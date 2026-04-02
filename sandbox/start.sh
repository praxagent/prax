#!/bin/bash
# Start Chromium with remote debugging for CDP screencast
mkdir -p /workspaces/browser_profiles/default

# Remove stale lock from previous container
rm -f /workspaces/browser_profiles/default/SingletonLock
rm -f /workspaces/browser_profiles/default/SingletonCookie
rm -f /workspaces/browser_profiles/default/SingletonSocket

chromium \
  --headless=new \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --disable-blink-features=AutomationControlled \
  --user-agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36" \
  --remote-debugging-address=0.0.0.0 \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* \
  --user-data-dir=/workspaces/browser_profiles/default \
  --window-size=1920,1080 \
  &

# Wait for Chrome to start
sleep 2

# Chrome often ignores --remote-debugging-address and binds 127.0.0.1 only.
# Node TCP proxy exposes it on 0.0.0.0:9223 for the Docker network.
node /cdp-proxy.js &

# Start opencode (original CMD)
exec opencode serve --hostname 0.0.0.0 --port 4096
