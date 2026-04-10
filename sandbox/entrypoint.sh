#!/usr/bin/env bash
# Start Chromium headless for browser screencast, then launch opencode-ai.
set -e

# Persistent browser profile — stored in /root (persisted via volume mount)
# so sessions, cookies, and localStorage survive container rebuilds.
PROFILE_DIR=/root/.browser_profiles/default
mkdir -p "$PROFILE_DIR"
rm -f "$PROFILE_DIR"/SingletonLock "$PROFILE_DIR"/SingletonCookie "$PROFILE_DIR"/SingletonSocket

# Seed OpenCode config if the mounted volume is empty (first run).
OPENCODE_CFG=/root/.config/opencode/opencode.json
if [ ! -f "$OPENCODE_CFG" ]; then
  cp /opt/opencode.json "$OPENCODE_CFG"
fi

# Persist Claude Code config across container rebuilds.
# Claude stores its main config at ~/.claude.json (a file outside ~/.claude/).
# We symlink it into the persisted ~/.claude/ directory so it survives.
CLAUDE_JSON=/root/.claude.json
CLAUDE_DIR=/root/.claude
mkdir -p "$CLAUDE_DIR"
if [ -f "$CLAUDE_JSON" ] && [ ! -L "$CLAUDE_JSON" ]; then
  # First run after rebuild — move existing config into persisted dir
  mv "$CLAUDE_JSON" "$CLAUDE_DIR/claude.json"
  ln -s "$CLAUDE_DIR/claude.json" "$CLAUDE_JSON"
elif [ -f "$CLAUDE_DIR/claude.json" ] && [ ! -e "$CLAUDE_JSON" ]; then
  # Config exists in persisted dir but symlink is missing (fresh container)
  ln -s "$CLAUDE_DIR/claude.json" "$CLAUDE_JSON"
fi

# ── Package install manifests ──
# Packages installed via Prax (sandbox_install, sandbox_shell, run_python)
# are tracked in /root/.installed_packages, .installed_pip_packages,
# and .installed_npm_packages.  These are NOT auto-reinstalled on rebuild
# (a bad package could break the desktop in a loop).  Instead, review the
# manifests and add proven packages to the Dockerfile manually.
if [ -f /root/.installed_packages ] || [ -f /root/.installed_pip_packages ] || [ -f /root/.installed_npm_packages ]; then
  echo "Package manifests found in /root/ — review and add to Dockerfile for persistence:"
  [ -f /root/.installed_packages ] && echo "  apt: $(sort -u /root/.installed_packages | tr '\n' ' ')"
  [ -f /root/.installed_pip_packages ] && echo "  pip: $(sort -u /root/.installed_pip_packages | tr '\n' ' ')"
  [ -f /root/.installed_npm_packages ] && echo "  npm: $(sort -u /root/.installed_npm_packages | tr '\n' ' ')"
fi

# ── Persistent tmux session ──
# Terminal state survives WebSocket reconnects (page refresh, device switch).
if command -v tmux &>/dev/null; then
  tmux has-session -t prax 2>/dev/null || \
    tmux new-session -d -s prax -c /source
fi

# ── code-server (web-based VS Code on port 8443) ──
if command -v code-server &>/dev/null; then
  code-server --bind-addr 0.0.0.0:8443 --auth none --disable-telemetry /workspace &>/dev/null &
  echo "code-server available at http://0.0.0.0:8443"
fi

# ── Linux Desktop (Xvfb + Fluxbox + noVNC) ──
# ONE Chrome instance serves both:
#   - The "Browser" tab in TeamWork (via CDP screenshare on port 9222)
#   - The "Desktop" tab in TeamWork (via noVNC on port 6080)
# Same browser, two ways to view it.  OAuth popups, GUI apps, and
# everything else is visible in both views.
if command -v Xvfb &>/dev/null; then
  export DISPLAY=:99
  Xvfb :99 -screen 0 1920x1080x24 &>/dev/null &
  sleep 0.5
  startxfce4 &>/dev/null &
  x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -q &>/dev/null &

  # noVNC — web-based VNC client on port 6080
  NOVNC_DIR=$(find /usr -name "vnc.html" -printf "%h" -quit 2>/dev/null || echo "")
  [ -z "$NOVNC_DIR" ] && NOVNC_DIR="/usr/share/novnc"
  if [ -d "$NOVNC_DIR" ]; then
    websockify --web="$NOVNC_DIR" 6080 localhost:5900 &>/dev/null &
    echo "Desktop + noVNC available at http://0.0.0.0:6080/vnc.html"
  fi

  # Single Chrome — non-headless (visible on desktop) + CDP enabled
  # (accessible from the Browser tab).  Both views see the same browser.
  chromium-browser \
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
  sleep 1
else
  # Fallback: headless Chrome if desktop deps aren't available
  chromium-browser \
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
  sleep 1
fi

# Expose CDP on 0.0.0.0:9223 so other containers can reach it.
socat TCP-LISTEN:9223,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:9222 &

exec opencode serve --hostname 0.0.0.0 --port 4096
