.PHONY: lint layers test actions ci eval tailscale-up tailscale-down tailscale-status sandbox-gpu sandbox-gpu-check \
        run-local-min run-local-all run-local-all-dev run-local-all-tail-dev shutdown restart-prax restart-teamwork restart-sandbox local-status local-logs smoke integration \
        _local-qdrant _local-neo4j _local-teamwork _local-teamwork-prod _local-teamwork-dev _local-sandbox _local-prax _tailscale-local

# Tests that require a fully-configured Docker sandbox with a live
# /plugin_data mount.  These pass locally only when the sandbox
# container is running AND its plugin layout matches the test's
# expectations.  On most dev machines and in CI neither precondition
# holds, so they're excluded by default.  See CLAUDE.md for the
# rationale.  Run them manually with:
#
#     FLASK_SECRET_KEY=ci-test-key uv run pytest \
#       tests/test_plugin_capabilities.py::TestScopedFilesystem -q
#
# when the sandbox is up.
SANDBOX_EXCLUDES := not test_imported_run_command_forces_cwd and not test_builtin_run_command_respects_cwd

lint:
	uv run ruff check .

layers:
	uv run python scripts/check_layers.py

test:
	FLASK_SECRET_KEY=ci-test-key uv run pytest tests/ -x -q -k "$(SANDBOX_EXCLUDES)"

actions:
	actionlint

ci: actions lint layers test
	@echo "\nAll CI checks passed."

# Regression gate — replays recorded failure cases through the LIVE agent and
# scores them with an LLM judge.  Needs provider API keys (real calls), so it
# is intentionally NOT part of `make ci`.  Run before shipping a system-prompt
# or model-config change:  make eval   (or  PRAX_EVAL_MIN_PASS_RATE=0.8 make eval)
eval:
	FLASK_SECRET_KEY=$${FLASK_SECRET_KEY:-ci-test-key} uv run --python 3.13 python scripts/eval_gate.py

# ── Tailscale Serve mappings — HOST-INSTALLED FALLBACK ────────────────
# Preferred path is the dockerized sidecar (set TS_AUTHKEY +
# COMPOSE_PROFILES=tailscale in .env, then `docker compose up`).  These
# targets remain for users who already have `tailscaled` installed on
# the host and don't want a sidecar — they configure the same two
# tailnet→localhost mappings the sidecar provides:
#   :443  → :3000  (TeamWork UI; Desktop + Browser WS flow through same-origin)
#   :3001 → :3002  (Grafana — offset host port avoids 0.0.0.0 vs tailnet-IP conflict)
# HTTPS must be enabled on your tailnet: admin console → DNS → HTTPS Certificates.

tailscale-up:
	sudo tailscale serve --bg --https=443 http://localhost:3000
	sudo tailscale serve --bg --https=3001 http://localhost:3002
	@echo
	@echo "Mappings active. From your laptop:"
	@echo "  TeamWork:  https://<machine>.<tailnet>.ts.net/"
	@echo "  Grafana:   https://<machine>.<tailnet>.ts.net:3001/"
	@echo "Run 'make tailscale-status' to see the resolved hostname."

tailscale-down:
	-sudo tailscale serve --https=443 off
	-sudo tailscale serve --https=3001 off
	@echo "Mappings cleared."

tailscale-status:
	@sudo tailscale serve status

# ── GPU sandbox ─────────────────────────────────────────────────────
# Recreate the sandbox container with the GPU compose override layered
# in (docker-compose.gpu.yml).  Requires nvidia-container-toolkit on the
# host — `make sandbox-gpu-check` verifies that before trying.
#
# Persist by adding to .env:
#   COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml
# Then plain `docker compose up -d` always layers the override in.

sandbox-gpu-check:
	@command -v nvidia-smi >/dev/null 2>&1 || { \
	  echo "ERROR: nvidia-smi not found. Install the NVIDIA driver first."; exit 1; }
	@docker info 2>/dev/null | grep -qi 'Runtimes:.*nvidia' || { \
	  echo "ERROR: nvidia container runtime not registered with Docker."; \
	  echo "Install nvidia-container-toolkit: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"; \
	  exit 1; }
	@nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader

sandbox-gpu: sandbox-gpu-check
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d sandbox --force-recreate
	@echo
	@echo "Waiting for sandbox to come up..."
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
	  docker compose exec -T sandbox true 2>/dev/null && break; \
	  sleep 1; \
	done
	@echo
	@echo "── GPU visible inside sandbox ──"
	@docker compose exec -T sandbox nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
	@echo
	@echo "Sandbox now has GPU access. To make this persistent across all"
	@echo "future \`docker compose\` commands, add this line to .env:"
	@echo
	@echo "  COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml"

# ── Native (no-Docker) local run ────────────────────────────────────
# Run the stack as plain host processes — no Docker, no compose. Useful
# for fast iteration and for hosts without a Docker daemon.
#
#   make run-local-min   Prax core only, foreground (Ctrl-C to stop).
#                        Memory + sandbox + TeamWork all OFF — a pure
#                        harness over Discord / SMS / voice. Needs only
#                        FLASK_SECRET_KEY (+ an LLM key) in .env.
#   make run-local-all   Full local stack in the background:
#                        Qdrant + Neo4j (memory) + TeamWork + the sandbox
#                        + Prax. Memory ON, TeamWork ON, sandbox ON.
#                        Qdrant, Neo4j and the sandbox run in Docker; Prax
#                        and TeamWork are plain host processes. Qdrant/Neo4j
#                        persist to the user's workspace (see PRAX_USER), so
#                        memory survives restarts. Logs land in $(LOCAL_RUN)/.
#   make run-local-all-dev  Same as run-local-all but DEBUG=true, so Prax
#                        runs under Werkzeug's reloader and restarts on
#                        code change.
#   make run-local-all-tail-dev  run-local-all-dev plus a Tailscale serve
#                        exposing the TeamWork UI (:8000) over HTTPS on your
#                        tailnet. Needs tailscale + sudo. ('make tailscale-up'
#                        targets the Docker-compose ports instead.)
#   make shutdown        Stop everything run-local-all started.
#   make restart-prax / restart-teamwork / restart-sandbox
#                        Force-restart ONE service in place — pick up code /
#                        .env / sandbox-connection changes without cycling the
#                        rest of the stack (or the heavy Qdrant/Neo4j stores).
#                        restart-teamwork auto-detects its mode (dev + tailscale).
#   make local-status    Probe each service's port and report up/down.
#   make local-logs      tail -F every $(LOCAL_RUN)/*.log (Ctrl-C out).
#
# Each backing service is skipped with an actionable install hint (never a
# hard failure) if it can't be started: Qdrant and Neo4j prefer Docker
# (falling back to a native `qdrant`/`neo4j` binary), a sibling TeamWork
# checkout at $(TEAMWORK_PATH), and a sibling prax-sandbox checkout at
# $(SANDBOX_PATH) (Docker-only). Override locations/owner with e.g.
# `make run-local-all TEAMWORK_PATH=/path/to/teamwork SANDBOX_PATH=/path/to/prax-sandbox PRAX_USER=alice`.
#
# Docker images (qdrant/qdrant, neo4j:5) are pulled automatically by `docker
# run` on first use; the sandbox image is built from its checkout. We do NOT
# add an explicit `docker pull` step on purpose — auto-pull goes through the
# Docker daemon, which honours its own proxy/registry config. Behind a proxy,
# configure Docker (not make); to pre-warm, run `docker pull qdrant/qdrant`
# and `docker pull neo4j:5` yourself before `make run-local-all`.
LOCAL_RUN     := .local-run
LOCAL_PY      := uv run --python 3.13 python
TEAMWORK_PATH ?= ../teamwork
SANDBOX_PATH  ?= ../prax-sandbox
# Workspace owner for the local stack. Qdrant/Neo4j persist their data under
# this user's workspace (mirrors the bundled container's .services/ layout),
# and Prax runs as this user. Override with `make run-local-all PRAX_USER=alice`.
PRAX_USER     ?= local
QDRANT_DATA   := $(CURDIR)/workspaces/$(PRAX_USER)/.services/qdrant
NEO4J_DATA    := $(CURDIR)/workspaces/$(PRAX_USER)/.services/neo4j
# Passed through to Prax's app.run(debug=...). `run-local-all-dev` flips
# this to true so Werkzeug's reloader restarts Prax on code change.
DEBUG         ?= false
# When non-empty, run TeamWork in DEV mode: the Vite dev server (:5173, hot
# module reload for the UI) + the backend under uvicorn --reload (:8000). Set
# by `run-local-all-dev`. Empty = production (build static once, serve from :8000).
TEAMWORK_DEV  ?=
# When non-empty (set by `run-local-all-tail-dev`), the TeamWork dev UI is served
# over a tailscale HTTPS proxy, so Vite's HMR client is told to use wss:443.
TEAMWORK_TAILSCALE ?=
# When non-empty, `_local-prax` force-cycles Prax (stop the running process +
# its reloader children, then relaunch) instead of skipping it when a live
# pidfile exists. The dev targets set this so a re-run always boots the latest
# code/.env; `make restart-prax` is the standalone form. Empty = the original
# idempotent behaviour (skip if already running).
RESTART       ?=
# Sandbox connection for a NATIVELY-run TeamWork (the run-local path). The split
# sandbox runs as its own compose project `prax-sandbox` (compose `name:`) with
# ports published on localhost, so a native TeamWork must reach it via localhost
# + the container name — NOT TeamWork's in-Docker defaults (chrome_cdp_host=
# `sandbox`, desktop_vnc_url=http://sandbox:6080, empty sandbox_container). Without
# these, terminal (docker exec), browser (CDP), and desktop (noVNC) all break.
TW_SANDBOX_ENV = SANDBOX_CONTAINER=prax-sandbox-sandbox-1 CHROME_CDP_HOST=localhost CHROME_CDP_PORT=9223 DESKTOP_VNC_URL=http://localhost:6080

run-local-min:
	@echo "Starting Prax core (no memory / sandbox / TeamWork). Ctrl-C to stop."
	@MEMORY_ENABLED=false SANDBOX_ENABLED=false TEAMWORK_ENABLED=false $(LOCAL_PY) app.py

run-local-all: _local-qdrant _local-neo4j _local-teamwork _local-sandbox _local-prax
	@# Prax is the last to bind (it waits for TeamWork, then boots a heavy
	@# import graph). Poll its /health for up to ~30s so the status below
	@# reflects reality instead of a premature "down". Ctrl-C is safe.
	@printf "Waiting for Prax to come up"; \
	  for i in $$(seq 1 60); do \
	    curl -s -o /dev/null --max-time 1 http://localhost:5001/health && break; \
	    printf "."; sleep 1; \
	  done; echo
	@$(MAKE) --no-print-directory local-status

# Same as run-local-all but with DEBUG=true, so Prax runs under Werkzeug's
# reloader and restarts on code change. RESTART=true force-cycles an
# already-running Prax (so a re-run always boots the latest code even if the
# previous process wasn't in DEBUG). Backing services (Qdrant/Neo4j/TeamWork)
# are unaffected. 'make shutdown' stops it.
run-local-all-dev:
	@$(MAKE) --no-print-directory run-local-all DEBUG=true TEAMWORK_DEV=true RESTART=true

# run-local-all-dev plus a Tailscale serve exposing the TeamWork DEV UI over
# HTTPS on your tailnet, so you can reach the hot-reloading stack from another
# device. TeamWork runs in dev mode (Vite :5173 HMR + backend reload) and
# tailscale serves the Vite dev server, so editing TeamWork live-reloads in the
# remote browser. Needs `tailscale` installed + HTTPS enabled on the tailnet,
# and uses sudo for `tailscale serve`. 'make tailscale-down' removes the mapping.
run-local-all-tail-dev:
	@$(MAKE) --no-print-directory run-local-all DEBUG=true TEAMWORK_DEV=true TEAMWORK_TAILSCALE=true RESTART=true
	@$(MAKE) --no-print-directory _tailscale-local TEAMWORK_DEV=true

_tailscale-local:
	@mkdir -p $(LOCAL_RUN)
	@if ! command -v tailscale >/dev/null 2>&1; then \
	  echo "WARN: tailscale not found - skipping the tailnet mapping."; \
	  echo "      Install it from https://tailscale.com/download and re-run."; \
	else \
	  if ! tailscale status >/dev/null 2>&1; then \
	    key="$$TS_AUTHKEY"; \
	    [ -z "$$key" ] && [ -f "$(CURDIR)/.env" ] && key=$$(grep -E '^TS_AUTHKEY=' "$(CURDIR)/.env" | tail -1 | cut -d= -f2- | tr -d '\"'); \
	    if [ -n "$$key" ]; then \
	      echo "Host is logged out of Tailscale - running 'tailscale up' with TS_AUTHKEY from .env..."; \
	      sudo tailscale up --authkey="$$key" $$TS_EXTRA_ARGS \
	        || echo "WARN: 'tailscale up' failed (bad/expired key?). See https://login.tailscale.com/admin/settings/keys"; \
	    else \
	      echo "WARN: host is logged out of Tailscale and no TS_AUTHKEY found (env or .env)."; \
	      echo "      Unlike 'docker compose' (which runs a tailscale sidecar that auths with"; \
	      echo "      TS_AUTHKEY from .env), this path uses the host's tailscaled. Either set"; \
	      echo "      TS_AUTHKEY in .env, or run 'sudo tailscale up' once, then re-run."; \
	    fi; \
	  fi; \
	  tw_port=8000; [ -n "$(TEAMWORK_DEV)" ] && tw_port=5173; \
	  sudo tailscale serve --bg --https=443 http://localhost:$$tw_port \
	    && { touch $(LOCAL_RUN)/.tailscale-on; \
	         echo "TeamWork now served at https://<machine>.<tailnet>.ts.net/ (-> :$$tw_port; run 'make tailscale-status')"; } \
	    || echo "WARN: 'tailscale serve' failed - host not on the tailnet, or HTTPS not enabled (admin console -> DNS -> HTTPS Certificates)."; \
	fi

# Qdrant runs in Docker by default, persisting to the user's workspace so
# memory survives restarts. Falls back to a native `qdrant`/`./qdrant` binary
# if Docker isn't available, and prints install hints if neither is.
_local-qdrant:
	@mkdir -p $(LOCAL_RUN)
	@if curl -s -o /dev/null --max-time 2 http://localhost:6333/; then \
	  echo "Qdrant already running -> :6333"; \
	elif command -v docker >/dev/null 2>&1; then \
	  mkdir -p "$(QDRANT_DATA)"; \
	  docker rm -f prax-qdrant >/dev/null 2>&1 || true; \
	  docker run -d --name prax-qdrant -p 6333:6333 -v "$(QDRANT_DATA)":/qdrant/storage qdrant/qdrant \
	    >$(LOCAL_RUN)/qdrant.log 2>&1 \
	    && echo "Qdrant started (docker) -> :6333   data: $(QDRANT_DATA)" \
	    || { echo "WARN: qdrant docker run failed - see $(LOCAL_RUN)/qdrant.log. LTM will degrade."; }; \
	elif command -v qdrant >/dev/null 2>&1; then \
	  nohup qdrant >$(LOCAL_RUN)/qdrant.log 2>&1 & echo $$! >$(LOCAL_RUN)/qdrant.pid; \
	  echo "Qdrant started (native, pid $$(cat $(LOCAL_RUN)/qdrant.pid)) -> :6333"; \
	elif [ -x ./qdrant ]; then \
	  nohup ./qdrant >$(LOCAL_RUN)/qdrant.log 2>&1 & echo $$! >$(LOCAL_RUN)/qdrant.pid; \
	  echo "Qdrant started (native, pid $$(cat $(LOCAL_RUN)/qdrant.pid)) -> :6333"; \
	else \
	  echo "WARN: neither Docker nor a qdrant binary found - skipping. LTM will degrade."; \
	  echo "      Easiest fix: install Docker (https://docs.docker.com/engine/install/) and re-run;"; \
	  echo "      run-local-all then starts Qdrant in a container automatically."; \
	  echo "      Or install the binary from https://github.com/qdrant/qdrant/releases (on PATH or ./qdrant)."; \
	fi

# Neo4j runs in Docker by default, persisting to the user's workspace. Auth
# matches Prax's defaults (neo4j / prax-memory). Falls back to a native `neo4j`
# install if Docker isn't available, and prints install hints if neither is.
_local-neo4j:
	@mkdir -p $(LOCAL_RUN)
	@if curl -s -o /dev/null --max-time 2 http://localhost:7474/; then \
	  echo "Neo4j already running -> :7687"; \
	elif command -v docker >/dev/null 2>&1; then \
	  mkdir -p "$(NEO4J_DATA)/data" "$(NEO4J_DATA)/logs"; \
	  docker rm -f prax-neo4j >/dev/null 2>&1 || true; \
	  docker run -d --name prax-neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/prax-memory \
	    -v "$(NEO4J_DATA)/data":/data -v "$(NEO4J_DATA)/logs":/logs neo4j:5 \
	    >$(LOCAL_RUN)/neo4j.log 2>&1 \
	    && { echo "Neo4j started (docker) -> :7687    data: $(NEO4J_DATA)"; \
	         printf "  waiting for Neo4j to accept Bolt connections"; ok=; \
	         for i in $$(seq 1 60); do \
	           docker exec prax-neo4j cypher-shell -u neo4j -p prax-memory "RETURN 1;" >/dev/null 2>&1 \
	             && { ok=1; echo " ready"; break; }; \
	           printf "."; sleep 1; \
	         done; \
	         [ -n "$$ok" ] || echo " (timed out after 60s - memory may degrade until Neo4j finishes starting)"; } \
	    || { echo "WARN: neo4j docker run failed - see $(LOCAL_RUN)/neo4j.log. Graph memory will degrade."; }; \
	elif command -v neo4j >/dev/null 2>&1; then \
	  neo4j start >$(LOCAL_RUN)/neo4j.log 2>&1 || echo "WARN: 'neo4j start' failed - see $(LOCAL_RUN)/neo4j.log"; \
	  echo "Neo4j start requested (native) -> :7687 ('make shutdown' stops it)"; \
	else \
	  echo "WARN: neither Docker nor a neo4j binary found - skipping. Graph memory will degrade."; \
	  echo "      Easiest fix: install Docker (https://docs.docker.com/engine/install/) and re-run;"; \
	  echo "      run-local-all then starts Neo4j in a container automatically."; \
	  echo "      Or install natively: 'brew install neo4j' (macOS) /"; \
	  echo "      https://neo4j.com/docs/operations-manual/current/installation/linux/ (Linux)."; \
	fi

_local-teamwork:
	@mkdir -p $(LOCAL_RUN)
	@# RESTART=true: stop the running TeamWork (backend + Vite + children) so the
	@# relaunch picks up new launch env (e.g. the sandbox connection vars).
	@if [ -n "$(RESTART)" ]; then \
	  stopped=""; \
	  for pf in $(LOCAL_RUN)/teamwork.pid $(LOCAL_RUN)/teamwork-vite.pid; do \
	    [ -f "$$pf" ] || continue; pid=$$(cat "$$pf"); \
	    if kill -0 "$$pid" 2>/dev/null; then \
	      kids=$$(pgrep -P "$$pid" 2>/dev/null); \
	      gkids=$$(for k in $$kids; do pgrep -P "$$k" 2>/dev/null; done); \
	      kill "$$pid" $$kids $$gkids 2>/dev/null; \
	      for i in $$(seq 1 20); do kill -0 "$$pid" 2>/dev/null || break; sleep 0.25; done; \
	      stopped="yes"; \
	    fi; \
	    rm -f "$$pf"; \
	  done; \
	  [ -n "$$stopped" ] && echo "TeamWork restart requested — stopped old backend + Vite."; \
	fi
	@if [ -f $(LOCAL_RUN)/teamwork.pid ] && kill -0 $$(cat $(LOCAL_RUN)/teamwork.pid) 2>/dev/null; then \
	  echo "TeamWork already running (pid $$(cat $(LOCAL_RUN)/teamwork.pid)). Use RESTART=true to force-cycle."; \
	elif [ ! -d "$(TEAMWORK_PATH)" ]; then \
	  echo "WARN: TeamWork checkout not at $(TEAMWORK_PATH) - skipping. The web UI won't be available."; \
	  echo "      To install TeamWork:"; \
	  echo "        * Clone it next to this repo:  git clone https://github.com/praxagent/teamwork $(TEAMWORK_PATH)"; \
	  echo "        * Or point at an existing checkout:  make run-local-all TEAMWORK_PATH=/path/to/teamwork"; \
	elif [ -n "$(TEAMWORK_DEV)" ]; then \
	  $(MAKE) --no-print-directory _local-teamwork-dev; \
	else \
	  $(MAKE) --no-print-directory _local-teamwork-prod; \
	fi

# Production: build the React UI into src/teamwork/static once (if missing), then
# run the backend on :8000 serving that built SPA.
_local-teamwork-prod:
	@if [ ! -d "$(TEAMWORK_PATH)/src/teamwork/static" ] || [ -z "$$(ls -A "$(TEAMWORK_PATH)/src/teamwork/static" 2>/dev/null)" ]; then \
	  if command -v npm >/dev/null 2>&1; then \
	    echo "Building TeamWork web UI (first run only; npm ci + vite build, can take a few minutes)..."; \
	    ( cd "$(TEAMWORK_PATH)/frontend" && npm ci && npx vite build ) >$(LOCAL_RUN)/teamwork-build.log 2>&1 \
	      && echo "  TeamWork UI built -> src/teamwork/static/" \
	      || echo "  WARN: UI build failed - see $(LOCAL_RUN)/teamwork-build.log. The API still runs; the UI page won't render."; \
	  else \
	    echo "WARN: TeamWork web UI isn't built and 'npm' isn't installed - the API will run but the UI page won't render."; \
	    echo "      Install Node.js 18+ (https://nodejs.org/) and re-run, or build it once manually:"; \
	    echo "        cd $(TEAMWORK_PATH)/frontend && npm ci && npx vite build"; \
	  fi; \
	fi
	@( cd "$(TEAMWORK_PATH)" && \
	    DATABASE_URL="sqlite+aiosqlite:///./vteam.db" \
	    WORKSPACE_PATH="$(CURDIR)/workspaces" \
	    PRAX_URL="http://localhost:5001" \
	    $(TW_SANDBOX_ENV) \
	    CORS_ORIGINS='["http://localhost:3000","http://localhost:5173"]' \
	    nohup uv run --python 3.13 python -m teamwork.cli ) \
	      >$(LOCAL_RUN)/teamwork.log 2>&1 & echo $$! >$(LOCAL_RUN)/teamwork.pid
	@rm -f $(LOCAL_RUN)/.teamwork-dev
	@echo "TeamWork started (pid $$(cat $(LOCAL_RUN)/teamwork.pid)) -> :8000"

# Dev: Vite dev server (:5173, hot module reload for the UI) + the backend under
# uvicorn --reload (:8000). Edits to frontend/src hot-reload; edits to
# src/teamwork reload the backend. Falls back to a production build if npm is
# missing. The browser hits the Vite origin, which proxies /api + /ws to :8000
# (same-origin — no CORS needed).
_local-teamwork-dev:
	@if ! command -v npm >/dev/null 2>&1; then \
	  echo "WARN: TeamWork dev mode needs Node.js + npm (https://nodejs.org/, v18+). Falling back to a production build."; \
	  $(MAKE) --no-print-directory _local-teamwork-prod; \
	else \
	  if [ ! -d "$(TEAMWORK_PATH)/frontend/node_modules" ]; then \
	    echo "Installing TeamWork web UI deps (npm ci; first run only)..."; \
	    ( cd "$(TEAMWORK_PATH)/frontend" && npm ci ) >$(LOCAL_RUN)/teamwork-build.log 2>&1 \
	      || echo "  WARN: npm ci failed - see $(LOCAL_RUN)/teamwork-build.log"; \
	  fi; \
	  ( cd "$(TEAMWORK_PATH)/frontend" && TEAMWORK_TAILSCALE="$(TEAMWORK_TAILSCALE)" \
	    nohup npm run dev -- --port 5173 ) \
	      >$(LOCAL_RUN)/teamwork-vite.log 2>&1 & echo $$! >$(LOCAL_RUN)/teamwork-vite.pid; \
	  ( cd "$(TEAMWORK_PATH)" && \
	    TEAMWORK_RELOAD=true \
	    DATABASE_URL="sqlite+aiosqlite:///./vteam.db" \
	    WORKSPACE_PATH="$(CURDIR)/workspaces" \
	    PRAX_URL="http://localhost:5001" \
	    $(TW_SANDBOX_ENV) \
	    CORS_ORIGINS='["http://localhost:3000","http://localhost:5173"]' \
	    nohup uv run --python 3.13 python -m teamwork.cli ) \
	      >$(LOCAL_RUN)/teamwork.log 2>&1 & echo $$! >$(LOCAL_RUN)/teamwork.pid; \
	  touch $(LOCAL_RUN)/.teamwork-dev; \
	  echo "TeamWork (dev) started — backend pid $$(cat $(LOCAL_RUN)/teamwork.pid) :8000 (reload), Vite pid $$(cat $(LOCAL_RUN)/teamwork-vite.pid) :5173 (HMR)"; \
	  echo "  Open the UI on :5173 (or your tailnet URL). Edit $(TEAMWORK_PATH)/frontend/src for live UI reload, src/teamwork for backend reload."; \
	fi

# The sandbox (coding agents + browser + desktop) is the one piece with no
# native form — it's a Docker container. We bring it up via its own compose
# file (pinned with -f so a COMPOSE_FILE/COMPOSE_PROFILES in Prax's .env can't
# leak in) and Prax connects to the published localhost ports (SANDBOX_HOST
# defaults to localhost). Persistence + creds mirror the bundled compose:
#   * /workspace is bind-mounted to workspaces/$(PRAX_USER) on the host, so the
#     sandbox's files persist there and survive `docker compose down` (they do
#     NOT vaporize with the container).
#   * Prax's .env names (ANTHROPIC_KEY/OPENAI_KEY) are mapped to the sandbox's
#     ANTHROPIC_API_KEY/OPENAI_API_KEY, same as the all-in-one compose.
# The .sandbox-on flag records success so _local-prax knows whether to start
# with SANDBOX_ENABLED=true. If the sandbox is expected (Docker + checkout) but
# fails to start, we hard-fail rather than silently downgrade.
_local-sandbox:
	@mkdir -p $(LOCAL_RUN)
	@rm -f $(LOCAL_RUN)/.sandbox-on
	@if ! command -v docker >/dev/null 2>&1; then \
	  echo "WARN: docker not found - skipping sandbox. Coding/desktop/browser tools will degrade."; \
	  echo "      The sandbox is Docker-only; install Docker Engine to enable it:"; \
	  echo "        https://docs.docker.com/engine/install/"; \
	elif [ ! -d "$(SANDBOX_PATH)" ]; then \
	  echo "WARN: prax-sandbox checkout not at $(SANDBOX_PATH) - skipping sandbox."; \
	  echo "      To install the sandbox:"; \
	  echo "        * Clone it next to this repo:  git clone https://github.com/praxagent/prax-sandbox $(SANDBOX_PATH)"; \
	  echo "        * Or point at an existing checkout:  make run-local-all SANDBOX_PATH=/path/to/prax-sandbox"; \
	else \
	  ( cd "$(SANDBOX_PATH)" && \
	    { docker image inspect prax-sandbox:latest >/dev/null 2>&1 || \
	      { echo "Building prax-sandbox image (first run only, this can take several minutes)..."; \
	        docker build -t prax-sandbox:latest sandbox/ ; } ; } && \
	    { docker compose -f docker-compose.yml down --remove-orphans >/dev/null 2>&1 || true; } && \
	    mkdir -p "$(CURDIR)/workspaces/$(PRAX_USER)" && \
	    { ak=""; ok=""; \
	      if [ -f "$(CURDIR)/.env" ]; then \
	        ak=$$(grep -E '^ANTHROPIC_KEY=' "$(CURDIR)/.env" | tail -1 | cut -d= -f2- | tr -d '\"'); \
	        ok=$$(grep -E '^OPENAI_KEY=' "$(CURDIR)/.env" | tail -1 | cut -d= -f2- | tr -d '\"'); \
	      fi; \
	      ANTHROPIC_API_KEY="$$ak" OPENAI_API_KEY="$$ok" \
	        WORKSPACE_DIR="$(CURDIR)/workspaces/$(PRAX_USER)" \
	        docker compose -f docker-compose.yml up -d; } ) \
	      >$(LOCAL_RUN)/sandbox.log 2>&1 \
	    && { touch $(LOCAL_RUN)/.sandbox-on; \
	         echo "Sandbox started -> :4096 (OpenCode) :9223 (CDP) :6080 (desktop)"; } \
	    || { echo "ERROR: sandbox failed to start - see $(LOCAL_RUN)/sandbox.log"; \
	         echo "       run-local-all aborts here: the sandbox was expected to come up (Docker + checkout present)."; \
	         echo "       Fix the error above, or run without it via 'make run-local-all SANDBOX_PATH='."; \
	         if command -v docker >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then \
	           echo "       Hint: 'docker' is installed but not usable as this user (daemon unreachable)."; \
	           echo "             If sandbox.log shows a /var/run/docker.sock permission error, add yourself"; \
	           echo "             to the docker group and start a fresh login shell:"; \
	           echo "               sudo usermod -aG docker \"$$USER\"   # then log out/in (or: newgrp docker)"; \
	         fi; \
	         exit 1; }; \
	fi

_local-prax:
	@mkdir -p $(LOCAL_RUN)
	@# RESTART=true: stop the running Prax (+ its Werkzeug reloader children) and
	@# wait for it to release :5001, so the relaunch below boots the latest code.
	@if [ -n "$(RESTART)" ] && [ -f $(LOCAL_RUN)/prax.pid ] && kill -0 $$(cat $(LOCAL_RUN)/prax.pid) 2>/dev/null; then \
	  pid=$$(cat $(LOCAL_RUN)/prax.pid); \
	  kids=$$(pgrep -P "$$pid" 2>/dev/null); \
	  gkids=$$(for k in $$kids; do pgrep -P "$$k" 2>/dev/null; done); \
	  kill "$$pid" $$kids $$gkids 2>/dev/null; \
	  for i in $$(seq 1 20); do kill -0 "$$pid" 2>/dev/null || break; sleep 0.25; done; \
	  rm -f $(LOCAL_RUN)/prax.pid; \
	  echo "Prax restart requested — stopped old pid $$pid (+ reloader children)."; \
	fi
	@if [ -f $(LOCAL_RUN)/prax.pid ] && kill -0 $$(cat $(LOCAL_RUN)/prax.pid) 2>/dev/null; then \
	  echo "Prax already running (pid $$(cat $(LOCAL_RUN)/prax.pid)). Use RESTART=true (or 'make restart-prax') to force-cycle."; \
	else \
	  sb=false; [ -f $(LOCAL_RUN)/.sandbox-on ] && sb=true; \
	  MEMORY_ENABLED=true SANDBOX_ENABLED=$$sb SANDBOX_HOST=localhost TEAMWORK_ENABLED=true TEAMWORK_URL=http://localhost:8000 PRAX_USER_ID=$(PRAX_USER) DEBUG=$(DEBUG) \
	    nohup $(LOCAL_PY) app.py >$(LOCAL_RUN)/prax.log 2>&1 & echo $$! >$(LOCAL_RUN)/prax.pid; \
	  echo "Prax started (pid $$(cat $(LOCAL_RUN)/prax.pid)) -> :5001 (DEBUG=$(DEBUG), SANDBOX_ENABLED=$$sb, PRAX_USER_ID=$(PRAX_USER))"; \
	fi

# Force-restart ONLY Prax (pick up code or .env changes) without touching the
# rest of the stack. Defaults to DEBUG/dev (Werkzeug reloader on) so it matches
# the dev workflow; pass DEBUG=false for a production-style process.
restart-prax: DEBUG := true
restart-prax:
	@$(MAKE) --no-print-directory _local-prax RESTART=true DEBUG=$(DEBUG)

# Force-restart ONLY TeamWork (backend + dev Vite) — leaves the sandbox, Prax,
# and the backing stores running. Picks up code / .env / sandbox-connection-env
# changes. Auto-detects the running mode: dev if the dev marker or a live Vite is
# present, tailscale if the serve mapping is up — so it comes back as it was.
restart-teamwork:
	@dev=""; \
	 if [ -f $(LOCAL_RUN)/.teamwork-dev ] || { [ -f $(LOCAL_RUN)/teamwork-vite.pid ] && kill -0 $$(cat $(LOCAL_RUN)/teamwork-vite.pid) 2>/dev/null; }; then dev=true; fi; \
	 tl=""; [ -f $(LOCAL_RUN)/.tailscale-on ] && tl=true; \
	 echo "Restarting TeamWork (dev=$${dev:-false}, tailscale=$${tl:-false})..."; \
	 $(MAKE) --no-print-directory _local-teamwork RESTART=true TEAMWORK_DEV="$$dev" TEAMWORK_TAILSCALE="$$tl"

# Force-restart ONLY the sandbox container (docker compose down + up) — leaves
# TeamWork, Prax, and the backing stores running. Recreates the container, so an
# in-flight coding session is lost; the browser/terminal/desktop panels reconnect
# on refresh.
restart-sandbox:
	@$(MAKE) --no-print-directory _local-sandbox

shutdown:
	@echo "Stopping native local stack..."
	@# Stop each tracked service and its WHOLE descendant tree, then escalate to
	@# SIGKILL for any survivor so a wedged process can't keep holding a port. A
	@# fixed-depth kill (pid + children + grandchildren) used to orphan deeper
	@# processes — the TeamWork dev server nests npm -> sh -> vite -> node, so the
	@# node survived and kept :5173. `ptree` walks the full tree and lists it
	@# bottom-up (children before parents), which also avoids re-parenting orphans.
	@if [ -d $(LOCAL_RUN) ]; then \
	  ptree() { for c in $$(pgrep -P "$$1" 2>/dev/null); do ptree "$$c"; done; echo "$$1"; }; \
	  for f in $(LOCAL_RUN)/*.pid; do \
	    [ -e "$$f" ] || continue; \
	    pid=$$(cat "$$f"); name=$$(basename "$$f" .pid); \
	    if kill -0 "$$pid" 2>/dev/null; then \
	      pids=$$(ptree "$$pid"); \
	      kill $$pids 2>/dev/null; \
	      alive="$$pids"; \
	      for i in 1 2 3 4 5 6 7 8 9 10; do \
	        rem=""; for p in $$alive; do kill -0 "$$p" 2>/dev/null && rem="$$rem $$p"; done; \
	        alive="$$rem"; [ -z "$$alive" ] && break; sleep 0.3; \
	      done; \
	      if [ -n "$$alive" ]; then kill -9 $$alive 2>/dev/null; echo "  stopped $$name (pid $$pid, SIGKILL)"; \
	      else echo "  stopped $$name (pid $$pid)"; fi; \
	    else \
	      echo "  $$name not running (stale pid $$pid)"; \
	    fi; \
	    rm -f "$$f"; \
	  done; \
	fi
	@# Belt-and-suspenders sweep for orphans with no pid file (e.g. a pre-fix run
	@# that orphaned a process). The tree-kill above handles tracked processes; this
	@# catches strays. Match `python[0-9]* app.py` so it covers both the reloader's
	@# `python3 app.py` children and a plain `python app.py`, without touching an
	@# unrelated `app.py`. pkill -f matches the whole command line of every process
	@# including this recipe's own shell — the [x] bracket trick stops each *pattern*
	@# from self-matching, and the echo text avoids the literal patterns. Covers all
	@# three native services incl. the Vite dev server (the one the old shallow kill
	@# used to leak on :5173).
	@pkill -f "[p]ython[0-9.]* app\.py" 2>/dev/null && echo "  swept stray Prax server" || true
	@pkill -f "[t]eamwork.cli" 2>/dev/null && echo "  swept stray TeamWork backend" || true
	@pkill -f "[v]ite.*--port 5173" 2>/dev/null && echo "  swept stray Vite dev server" || true
	@pkill -f "[n]pm run dev" 2>/dev/null || true
	@if command -v docker >/dev/null 2>&1; then \
	  for c in prax-qdrant prax-neo4j; do \
	    docker rm -f $$c >/dev/null 2>&1 && echo "  stopped $$c (docker)" || true; \
	  done; \
	fi
	@command -v neo4j >/dev/null 2>&1 && neo4j stop >/dev/null 2>&1 && echo "  stopped neo4j (native)" || true
	@if [ -f $(LOCAL_RUN)/.sandbox-on ] && [ -d "$(SANDBOX_PATH)" ] && command -v docker >/dev/null 2>&1; then \
	  ( cd "$(SANDBOX_PATH)" && docker compose -f docker-compose.yml down ) >/dev/null 2>&1 && echo "  stopped sandbox (docker compose down)"; \
	  rm -f $(LOCAL_RUN)/.sandbox-on; \
	fi
	@# Only touch Tailscale (which needs sudo) if run-local-all-tail-dev set it up.
	@if [ -f $(LOCAL_RUN)/.tailscale-on ] && command -v tailscale >/dev/null 2>&1; then \
	  sudo tailscale serve --https=443 off >/dev/null 2>&1 && echo "  removed tailscale :443 serve"; \
	  rm -f $(LOCAL_RUN)/.tailscale-on; \
	fi
	@echo "Done."

local-status:
	@echo "-- Native local stack --"
	@printf "  %-9s" "Qdrant";   curl -s -o /dev/null --max-time 3 http://localhost:6333/             && echo " up   -> :6333" || echo " down -> :6333"
	@# Neo4j: check Bolt (:7687, what Prax actually uses) via cypher-shell when
	@# the container is up; the HTTP :7474 endpoint is a flaky proxy for readiness.
	@printf "  %-9s" "Neo4j"; \
	  if docker exec prax-neo4j cypher-shell -u neo4j -p prax-memory "RETURN 1;" >/dev/null 2>&1 \
	     || curl -s -o /dev/null --max-time 3 http://localhost:7474/; then \
	    echo " up   -> :7687"; else echo " down -> :7687"; fi
	@printf "  %-9s" "TeamWork"; curl -s -o /dev/null --max-time 3 http://localhost:8000/health        && echo " up   -> :8000 (API)" || echo " down -> :8000 (API)"
	@printf "  %-9s" "TW UI";    curl -s -o /dev/null --max-time 3 http://localhost:5173/             && echo " up   -> :5173 (Vite dev)" || echo " n/a  -> :5173 (Vite dev; prod serves UI from :8000)"
	@printf "  %-9s" "Sandbox";  curl -s -o /dev/null --max-time 3 http://localhost:4096/global/health && echo " up   -> :4096" || echo " down -> :4096"
	@printf "  %-9s" "Prax";     curl -s -o /dev/null --max-time 3 http://localhost:5001/health        && echo " up   -> :5001" || echo " down -> :5001"
	@echo "Logs: make local-logs   Stop: make shutdown   Connectivity: make smoke"

# Connectivity smoke test: not just "are ports up" (that's local-status) but "is
# everything actually CONNECTED" — the cross-service wiring a fresh clone needs
# (TeamWork SPA serving, TeamWork->Prax proxy, sandbox CDP/desktop upgrades,
# Prax->memory). Run after `make run-local-all[-dev]`. Exit non-zero on failure.
smoke:
	@python3 scripts/smoke_test.py

# Pre-PR fresh-install integration test. `make smoke` only checks an ALREADY-up
# stack; this proves a CLEAN checkout brings the WHOLE stack up and everything
# connects — the harness that catches fresh-download regressions (TeamWork SPA
# not built -> 404, TeamWork->sandbox panel wiring, Prax->sandbox CDP). It is the
# local twin of .github/workflows/fresh-install.yml (which runs it on a clean
# runner); run it yourself before opening a PR that touches the run/wiring path.
#
# What it does:
#   1. `make shutdown`  — tear down any running native stack (DISRUPTIVE: this
#      stops your live local stack; you'll `make run-local-all` again after).
#   2. clear DERIVED state that masks fresh-install gaps — the built TeamWork SPA
#      (forces a from-scratch `npm ci + vite build`) and the .local-run markers.
#   3. optional `REBUILD_SANDBOX=1` — `docker compose build --no-cache` the
#      sandbox image (otherwise the existing image is reused).
#   4. `make run-local-all` — best-effort bring-up of all 5 services.
#   5. `make smoke` — the HARD assertion that everything is connected; its exit
#      code is this target's exit code (non-zero on any critical disconnect).
#
# Heavy: rebuilds the TeamWork SPA (minutes) and starts the Chrome+desktop
# sandbox; budget ~4GB free RAM + Docker + Node 18+ + uv. For a core-only run
# (skip the sandbox), pass a non-existent SANDBOX_PATH:
#   make integration SANDBOX_PATH=/nonexistent
# Leaves the stack UP on success so you can poke at it; `make shutdown` to stop.
integration:
	@echo "== Fresh-install integration test =="
	@echo "  (DISRUPTIVE: tears down the running stack, then rebuilds from a clean state)"
	@$(MAKE) --no-print-directory shutdown
	@echo "Clearing derived state (forces a fresh TeamWork SPA build)..."
	@if [ -d "$(TEAMWORK_PATH)/src/teamwork/static" ]; then \
	  rm -rf "$(TEAMWORK_PATH)/src/teamwork/static" && echo "  removed $(TEAMWORK_PATH)/src/teamwork/static"; \
	fi
	@rm -rf $(LOCAL_RUN)
	@if [ -n "$(REBUILD_SANDBOX)" ]; then \
	  if [ -d "$(SANDBOX_PATH)" ] && command -v docker >/dev/null 2>&1; then \
	    echo "Rebuilding sandbox image (REBUILD_SANDBOX set; --no-cache)..."; \
	    ( cd "$(SANDBOX_PATH)" && docker compose -f docker-compose.yml build --no-cache ) || exit 1; \
	  else \
	    echo "WARN: REBUILD_SANDBOX set but no sandbox checkout / docker - skipping image rebuild."; \
	  fi; \
	fi
	@echo "Bringing the full stack up from clean..."
	@$(MAKE) --no-print-directory run-local-all
	@echo "Asserting connectivity..."
	@$(MAKE) --no-print-directory smoke
	@echo "Integration test PASSED. Stack left up; 'make shutdown' to stop."

local-logs:
	@mkdir -p $(LOCAL_RUN)
	@echo "Tailing $(LOCAL_RUN)/*.log (Ctrl-C to stop)..."
	@tail -F $(LOCAL_RUN)/*.log
