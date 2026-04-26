#!/usr/bin/env bash
# One-shot init for the sandbox container.  All long-lived processes
# (Xvfb, dbus, x11vnc, xfce4-*, websockify, clipboard-bridge, chromium,
# socat, opencode) are managed by supervisord — see supervisord.conf.
# This script just prepares the on-disk state those daemons need, then
# hands off to supervisord as PID 1.

set -e

# ── Persistent browser profile ──────────────────────────────────────
# Stored under /root (volume-mounted) so sessions / cookies / localStorage
# survive container rebuilds.
PROFILE_DIR=/root/.browser_profiles/default
mkdir -p "$PROFILE_DIR"
rm -f "$PROFILE_DIR"/SingletonLock "$PROFILE_DIR"/SingletonCookie "$PROFILE_DIR"/SingletonSocket

# Stale X locks from a hard kill prevent Xvfb from binding :99 — wipe them.
rm -f /tmp/.X99-lock 2>/dev/null
rm -f /tmp/.X11-unix/X99 2>/dev/null

# Seed OpenCode config if the mounted volume is empty (first run).
OPENCODE_CFG=/root/.config/opencode/opencode.json
if [ ! -f "$OPENCODE_CFG" ]; then
  mkdir -p "$(dirname "$OPENCODE_CFG")"
  cp /opt/opencode.json "$OPENCODE_CFG"
fi

# Persist Claude Code config across container rebuilds.
# Claude stores its main config at ~/.claude.json (a file outside ~/.claude/).
# We symlink it into the persisted ~/.claude/ directory so it survives.
CLAUDE_JSON=/root/.claude.json
CLAUDE_DIR=/root/.claude
mkdir -p "$CLAUDE_DIR"
if [ -f "$CLAUDE_JSON" ] && [ ! -L "$CLAUDE_JSON" ]; then
  mv "$CLAUDE_JSON" "$CLAUDE_DIR/claude.json"
  ln -s "$CLAUDE_DIR/claude.json" "$CLAUDE_JSON"
elif [ -f "$CLAUDE_DIR/claude.json" ] && [ ! -e "$CLAUDE_JSON" ]; then
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

# ── XFCE config seed (first run only — don't overwrite customizations) ──
mkdir -p /root/.config/xfce4/helpers \
         /root/.config/xfce4/xfconf/xfce-perchannel-xml \
         /root/.local/share/applications

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

[ ! -f /root/.config/xfce4/helpers.rc ] && echo "TerminalEmulator=xterm" > /root/.config/xfce4/helpers.rc

[ ! -f /root/.config/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml ] && cat > /root/.config/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml <<'XFWM'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfwm4" version="1.0">
  <property name="general" type="empty">
    <property name="theme" type="string" value="Default"/>
    <property name="button_layout" type="string" value="O|HMC"/>
  </property>
</channel>
XFWM

# ── Prax Tab Cast extension setup ──
# Rewrite the placeholder signaling host in the bundled extension so it
# points at TeamWork inside the compose network.  Defaults to the compose
# service name `prax:8000` — override with PRAX_CAST_SIGNALING_HOST.
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

# Pin the prax-cast extension to Chrome's toolbar so the user can invoke
# it from the Desktop (noVNC) tab with a single click.
PREFS="$PROFILE_DIR/Default/Preferences"
CAST_EXT_ID="mlkmhebdodnjnpmhmfagcjokmijmembn"
mkdir -p "$PROFILE_DIR/Default"
python3 - "$PREFS" "$CAST_EXT_ID" <<'PY' || true
import json, sys
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

# ── PATH integrity smoke check ──
# Catches the recurring footgun where a Dockerfile installs a binary
# under /root/... and symlinks it into /usr/local/bin — the runtime
# bind-mount of /root then hides the target, leaving the symlink
# dangling.  We don't fail the container (the user might not need the
# missing tool), just log loudly so the next "X not found" report has
# a breadcrumb in `docker compose logs sandbox`.
DANGLING=$(find /usr/local/bin /usr/local/sbin /usr/bin /usr/sbin /sbin /bin \
  -maxdepth 1 -xtype l 2>/dev/null)
if [ -n "$DANGLING" ]; then
  echo "[sandbox] WARNING: dangling symlinks on PATH (target probably hidden by a bind-mount):" >&2
  while IFS= read -r link; do
    echo "[sandbox]   $link -> $(readlink "$link")" >&2
  done <<< "$DANGLING"
  echo "[sandbox] If a tool is missing, install it directly to /usr/local/bin in the Dockerfile" >&2
  echo "[sandbox] (NOT under /root, which is bind-mounted from the workspace)." >&2
fi

echo "[sandbox] init complete — handing off to supervisord"
exec /usr/bin/supervisord -n -c /etc/supervisor/supervisord.conf
