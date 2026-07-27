#!/usr/bin/env bash
# Bring a Prax suite deployment up to date — all of it, in one shot.
#
#   ./deploy/update.sh              # pull, wire, start everything, verify
#   ./deploy/update.sh --frontend   # also rebuild the TeamWork UI (slow)
#   ./deploy/update.sh --check      # verify only; change nothing
#
# Run it ON the deployment. It is idempotent, so it is equally the first-run
# installer and the update command — there is no separate "install" path to keep
# in sync, which is how the two drift apart and one of them stops working.
#
# It starts the SANDBOX too. That was the gap that made this suite hard to
# deploy: update.sh handled prax, teamwork and the secrets proxy, and simply
# never mentioned the sandbox — so a box could look completely healthy (every
# service active, every health check 200) while the desktop, terminal and
# browser panels had nothing behind them. The failure surfaced to the user as an
# HTTP 403 in a browser console, which points at authentication and is nowhere
# near the truth.
set -euo pipefail

PRAX_ROOT="${PRAX_ROOT:-$HOME/PRAX}"
BUILD_FRONTEND=false
CHECK_ONLY=false
case "${1:-}" in
  --frontend) BUILD_FRONTEND=true ;;
  --check)    CHECK_ONLY=true ;;
esac

export PATH="$HOME/.local/bin:$PATH"

# Ports the sandbox publishes on loopback. Fixed by its compose file.
SANDBOX_NOVNC=6080
SANDBOX_CLIPBOARD=6090
SANDBOX_CDP=9223

fail=0
note() { printf '    %-22s %s\n' "$1" "$2"; }

# ── Config preflight ────────────────────────────────────────────────────────
# Cross-service settings that fail SILENTLY when unset are the suite's sharpest
# edge: each service starts fine and reports healthy, and you discover the gap
# from an empty panel or a console error. Check them where the answer is cheap.
check_env() {
  local file="$1" var="$2" why="$3"
  if [[ -f "$file" ]] && grep -qE "^${var}=.+" "$file"; then
    note "$var" "set"
  else
    note "$var" "MISSING — $why"
    fail=1
  fi
}

echo "==> config"
check_env "$PRAX_ROOT/teamwork/.env" PRAX_URL \
  "TeamWork cannot reach Prax; panels render blank with fieldless responses"
check_env "$PRAX_ROOT/prax/.env" TEAMWORK_URL \
  "Prax skips its TeamWork bootstrap and you get an empty workspace"
check_env "$PRAX_ROOT/teamwork/.env" DESKTOP_VNC_URL \
  "desktop panel proxies nowhere (http://127.0.0.1:$SANDBOX_NOVNC)"

if ! $CHECK_ONLY; then
  echo "==> pulling"
  for repo in prax teamwork prax-sandbox prax-secrets-proxy; do
    [[ -d "$PRAX_ROOT/$repo/.git" ]] || continue
    git -C "$PRAX_ROOT/$repo" fetch -q origin
    git -C "$PRAX_ROOT/$repo" checkout -q main
    git -C "$PRAX_ROOT/$repo" pull -q origin main
    printf '    %-20s %s\n' "$repo" "$(git -C "$PRAX_ROOT/$repo" log --oneline -1)"
  done

  echo "==> python deps"
  (cd "$PRAX_ROOT/prax" && uv sync --python 3.13 2>&1 | tail -1)

  if $BUILD_FRONTEND; then
    echo "==> rebuilding the TeamWork UI (slow)"
    (cd "$PRAX_ROOT/teamwork/frontend" && npm ci --no-audit --no-fund >/dev/null && npx vite build 2>&1 | tail -2)
  fi

  # The sandbox: terminal, browser and desktop all live here. `up -d` is a no-op
  # when it is already running and current, so this is safe to repeat.
  if [[ -f "$PRAX_ROOT/prax-sandbox/docker-compose.yml" ]]; then
    echo "==> sandbox"
    # WORKSPACE_DIR must match what Prax hands out, or the agent writes files
    # into a directory the container cannot see and the failure looks like a
    # tool bug rather than a mount.
    (cd "$PRAX_ROOT/prax-sandbox" \
      && WORKSPACE_DIR="${WORKSPACE_DIR:-$PRAX_ROOT/workspaces}" \
         docker compose up -d 2>&1 | tail -2)
  fi

  # A .env change to the proxy needs --force-recreate: compose reads it at
  # container start, so a plain `up -d` leaves the old values in place and the
  # symptom looks exactly like a bad key.
  if [[ -f "$PRAX_ROOT/prax-secrets-proxy/docker-compose.yml" ]]; then
    echo "==> secrets proxy"
    (cd "$PRAX_ROOT/prax-secrets-proxy" && docker compose --profile forward up -d --force-recreate 2>&1 | tail -2)
  fi

  echo "==> restarting services"
  # TeamWork first: Prax's startup reconnects to its TeamWork project and silently
  # skips if the UI is unreachable, leaving a confusingly empty workspace.
  sudo systemctl restart teamwork
  sleep 15
  sudo systemctl restart prax

  echo "==> waiting for health"
  for _ in $(seq 1 40); do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:5001/health || true)
    [[ "$code" == "200" ]] && break
    sleep 5
  done
fi

# ── Verify ──────────────────────────────────────────────────────────────────
# Every surface a user can actually open, not just the two processes. A green
# report that omits the sandbox is how a broken desktop shipped unnoticed.
echo "==> verify"
for svc in tailscaled docker teamwork prax; do
  state=$(systemctl is-active "$svc" 2>/dev/null || echo unknown)
  note "$svc" "$state"
  [[ "$state" == "active" ]] || fail=1
done

# curl prints 000 for a connection it never made AND exits non-zero, so a
# trailing `|| echo unreachable` appends rather than replaces. Map it here.
http() {
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$1" 2>/dev/null || true)
  [[ -z "$code" || "$code" == "000" ]] && code="unreachable"
  printf '%s' "$code"
}

for probe in \
  "prax:5001|http://127.0.0.1:5001/health" \
  "teamwork|http://127.0.0.1:8000/health" \
  "UI|http://127.0.0.1:8000/" \
  "sandbox noVNC|http://127.0.0.1:$SANDBOX_NOVNC/" \
  "sandbox CDP|http://127.0.0.1:$SANDBOX_CDP/json/version" \
  "desktop via TeamWork|http://127.0.0.1:8000/api/desktop/vnc.html"
do
  label="${probe%%|*}"; url="${probe#*|}"
  code=$(http "$url")
  note "$label" "$code"
  [[ "$code" =~ ^(200|101)$ ]] || fail=1
done

# The clipboard bridge speaks websocket only, so a plain GET is the wrong shape;
# a listening socket is the useful signal.
if (echo >"/dev/tcp/127.0.0.1/$SANDBOX_CLIPBOARD") 2>/dev/null; then
  note "sandbox clipboard" "listening"
else
  note "sandbox clipboard" "unreachable"
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo
  echo "INCOMPLETE — something above is not serving. The suite will look mostly"
  echo "fine in the UI; the affected panel will fail in a browser console."
  exit 1
fi
echo "done — all surfaces responding."
