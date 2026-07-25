#!/usr/bin/env bash
# Update a deployed Prax suite in place.
#
#   ./deploy/update.sh              # pull main, restart
#   ./deploy/update.sh --frontend   # also rebuild the TeamWork UI
#
# Run it ON the deployment. The frontend rebuild is opt-in because it is the
# slow part (~2 min on 2 vCPUs) and most updates do not touch the UI — when one
# does, build it on a bigger machine and rsync `teamwork/src/teamwork/static/`
# instead of paying for it here.
set -euo pipefail

PRAX_ROOT="${PRAX_ROOT:-$HOME/PRAX}"
BUILD_FRONTEND=false
[[ "${1:-}" == "--frontend" ]] && BUILD_FRONTEND=true

export PATH="$HOME/.local/bin:$PATH"

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

echo "==> verify"
for svc in tailscaled docker teamwork prax; do
  printf '    %-11s %s\n' "$svc" "$(systemctl is-active "$svc")"
done
printf '    %-11s %s\n' "prax:5001" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:5001/health || echo unreachable)"
printf '    %-11s %s\n' "teamwork" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:8000/health || echo unreachable)"
printf '    %-11s %s\n' "UI" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:8000/ || echo unreachable)"
echo "done."
