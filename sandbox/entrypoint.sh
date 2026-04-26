#!/usr/bin/env bash
# Start Chromium headless for browser screencast, then launch opencode-ai.

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

# ── Scratch Python venv (for Prax to pip install into freely) ──
if [ ! -d /opt/prax-venv ]; then
  uv venv /opt/prax-venv --python python3 2>/dev/null
  echo "Created scratch venv at /opt/prax-venv"
fi
export PATH="/opt/prax-venv/bin:$PATH"

# ── code-server (web-based VS Code on port 8443) ──
# Disabled by default to save ~200MB RAM. Start manually:
#   code-server --bind-addr 0.0.0.0:8443 --auth none --disable-telemetry /workspace &
# Or ask Prax: "launch code-server"

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
  # XFCE4 desktop — dbus session + individual components
  eval "$(dbus-launch --sh-syntax)" 2>/dev/null
  export DBUS_SESSION_BUS_ADDRESS

  # Seed default configs on first run only — don't overwrite user customizations.
  mkdir -p /root/.config/xfce4/helpers /root/.config/xfce4/xfconf/xfce-perchannel-xml /root/.local/share/applications

  [ ! -f /root/.local/share/applications/defaults.list ] && cat > /root/.local/share/applications/defaults.list <<'DEFAULTS'
[Default Applications]
x-scheme-handler/http=chromium-browser.desktop
x-scheme-handler/https=chromium-browser.desktop
text/html=chromium-browser.desktop
DEFAULTS

  [ ! -f /root/.Xresources ] && cat > /root/.Xresources <<'XRES'
xterm*faceName: DejaVu Sans Mono
xterm*faceSize: 14
xterm*background: #1e1e2e
xterm*foreground: #cdd6f4
xterm*cursorColor: #f5e0dc
xterm*scrollBar: false
xterm*saveLines: 10000
XRES

  xrdb -merge /root/.Xresources 2>/dev/null || true

  XFCE_TERM=xterm
  [ ! -f /root/.config/xfce4/helpers.rc ] && echo "TerminalEmulator=$XFCE_TERM" > /root/.config/xfce4/helpers.rc

  [ ! -f /root/.config/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml ] && cat > /root/.config/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml <<'XFWM'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfwm4" version="1.0">
  <property name="general" type="empty">
    <property name="theme" type="string" value="Default"/>
    <property name="button_layout" type="string" value="O|HMC"/>
  </property>
</channel>
XFWM

  xfwm4 &>/dev/null &
  sleep 0.3
  xfce4-panel &>/dev/null &
  xfdesktop &>/dev/null &
  x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -q &>/dev/null &

  # Clipboard bridge — WebSocket server syncing X11 clipboard with browser
  python3 /usr/local/bin/clipboard-bridge.py &>/dev/null &

  # noVNC — web-based VNC client on port 6080
  NOVNC_DIR=$(find /usr -name "vnc.html" -printf "%h" -quit 2>/dev/null || echo "")
  [ -z "$NOVNC_DIR" ] && NOVNC_DIR="/usr/share/novnc"
  if [ -d "$NOVNC_DIR" ]; then
    websockify --web="$NOVNC_DIR" 6080 localhost:5900 &>/dev/null &
    echo "Desktop + noVNC available at http://0.0.0.0:6080/vnc.html"
  fi

  # ── Prax Tab Cast extension ──
  # Rewrite the placeholder signaling host in the bundled extension so
  # it points at TeamWork inside the compose network.  Defaults to the
  # compose service name `prax:8000` — override with PRAX_CAST_SIGNALING_HOST.
  CAST_HOST="${PRAX_CAST_SIGNALING_HOST:-prax:8000}"
  if [ -f /opt/prax-cast-ext/offscreen.js ]; then
    sed -i "s|__PRAX_CAST_SIGNALING_HOST__|${CAST_HOST}|g" /opt/prax-cast-ext/offscreen.js
  fi

  # Chromium caches the compiled service worker script for unpacked
  # extensions.  When the extension source changes on disk (i.e. we
  # rebuild the sandbox image), Chrome keeps using the stale cached
  # script, which leaves the offscreen document in a broken "partial"
  # extension context (missing chrome.tabCapture, chrome.tabs, etc.).
  # Wipe only the SW scratch state — cookies, storage, profile prefs
  # in $PROFILE_DIR/Default all stay intact.
  rm -rf "$PROFILE_DIR/Default/Service Worker" 2>/dev/null || true

  # Pin the prax-cast extension to Chrome's toolbar so the user can
  # invoke it from the Desktop (noVNC) tab with a single click — the
  # only reliable path for invocation, since keyboard shortcuts get
  # intercepted by the host browser before reaching noVNC.  The
  # extension ID is the SHA-derived ID for an unpacked extension
  # loaded from /opt/prax-cast-ext (deterministic for that path).
  PREFS="$PROFILE_DIR/Default/Preferences"
  CAST_EXT_ID="mlkmhebdodnjnpmhmfagcjokmijmembn"
  mkdir -p "$PROFILE_DIR/Default"
  python3 - "$PREFS" "$CAST_EXT_ID" <<'PY' || true
import json, os, sys
prefs_path, ext_id = sys.argv[1], sys.argv[2]
try:
    with open(prefs_path) as f:
        prefs = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    prefs = {}
exts = prefs.setdefault('extensions', {})
pinned = exts.setdefault('pinned_extensions', [])
if ext_id not in pinned:
    pinned.append(ext_id)
    with open(prefs_path, 'w') as f:
        json.dump(prefs, f)
    print(f"[prax-cast] pinned {ext_id} in {prefs_path}")
else:
    print(f"[prax-cast] already pinned in {prefs_path}")
PY

  # Single Chrome — non-headless (visible on desktop) + CDP enabled
  # (accessible from the Browser tab).  Both views see the same browser.
  chromium-browser \
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
