.PHONY: lint layers test actions ci eval tailscale-up tailscale-down tailscale-status sandbox-gpu sandbox-gpu-check \
        run-local-min run-local-all shutdown local-status local-logs \
        _local-qdrant _local-neo4j _local-teamwork _local-prax

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
#   make run-local-all   Full no-Docker stack in the background:
#                        Qdrant + Neo4j (memory) + TeamWork + Prax.
#                        Opinionated: memory ON, TeamWork ON, sandbox
#                        OFF (the sandbox is Docker-only). PIDs and logs
#                        land in $(LOCAL_RUN)/.
#   make shutdown        Stop everything run-local-all started.
#   make local-status    Probe each service's port and report up/down.
#   make local-logs      tail -F every $(LOCAL_RUN)/*.log (Ctrl-C out).
#
# Each backing service is skipped with a warning (never a hard failure)
# if its binary isn't found: `qdrant` (or ./qdrant), `neo4j`, and a
# sibling TeamWork checkout at $(TEAMWORK_PATH). Override the TeamWork
# location with `make run-local-all TEAMWORK_PATH=/path/to/teamwork`.
LOCAL_RUN     := .local-run
LOCAL_PY      := uv run --python 3.13 python
TEAMWORK_PATH ?= ../teamwork

run-local-min:
	@echo "Starting Prax core (no memory / sandbox / TeamWork). Ctrl-C to stop."
	@MEMORY_ENABLED=false SANDBOX_ENABLED=false TEAMWORK_ENABLED=false $(LOCAL_PY) app.py

run-local-all: _local-qdrant _local-neo4j _local-teamwork _local-prax
	@sleep 2
	@$(MAKE) --no-print-directory local-status

_local-qdrant:
	@mkdir -p $(LOCAL_RUN)
	@if [ -f $(LOCAL_RUN)/qdrant.pid ] && kill -0 $$(cat $(LOCAL_RUN)/qdrant.pid) 2>/dev/null; then \
	  echo "Qdrant already running (pid $$(cat $(LOCAL_RUN)/qdrant.pid))."; \
	elif command -v qdrant >/dev/null 2>&1; then \
	  nohup qdrant >$(LOCAL_RUN)/qdrant.log 2>&1 & echo $$! >$(LOCAL_RUN)/qdrant.pid; \
	  echo "Qdrant started (pid $$(cat $(LOCAL_RUN)/qdrant.pid)) -> :6333"; \
	elif [ -x ./qdrant ]; then \
	  nohup ./qdrant >$(LOCAL_RUN)/qdrant.log 2>&1 & echo $$! >$(LOCAL_RUN)/qdrant.pid; \
	  echo "Qdrant started (pid $$(cat $(LOCAL_RUN)/qdrant.pid)) -> :6333"; \
	else \
	  echo "WARN: qdrant not found (no 'qdrant' on PATH, no ./qdrant) - skipping. LTM will degrade."; \
	fi

_local-neo4j:
	@mkdir -p $(LOCAL_RUN)
	@if command -v neo4j >/dev/null 2>&1; then \
	  neo4j start >$(LOCAL_RUN)/neo4j.log 2>&1 || echo "WARN: 'neo4j start' failed - see $(LOCAL_RUN)/neo4j.log"; \
	  echo "Neo4j start requested -> :7687 (neo4j manages its own process; 'make shutdown' stops it)"; \
	else \
	  echo "WARN: neo4j not found on PATH - skipping. Graph memory will degrade."; \
	fi

_local-teamwork:
	@mkdir -p $(LOCAL_RUN)
	@if [ -f $(LOCAL_RUN)/teamwork.pid ] && kill -0 $$(cat $(LOCAL_RUN)/teamwork.pid) 2>/dev/null; then \
	  echo "TeamWork already running (pid $$(cat $(LOCAL_RUN)/teamwork.pid))."; \
	elif [ -d "$(TEAMWORK_PATH)" ]; then \
	  ( cd "$(TEAMWORK_PATH)" && \
	    DATABASE_URL="sqlite+aiosqlite:///./vteam.db" \
	    WORKSPACE_PATH="$(CURDIR)/workspaces" \
	    PRAX_URL="http://localhost:5001" \
	    CORS_ORIGINS='["http://localhost:3000","http://localhost:5173"]' \
	    nohup uv run --python 3.13 python -m teamwork.cli ) \
	      >$(LOCAL_RUN)/teamwork.log 2>&1 & echo $$! >$(LOCAL_RUN)/teamwork.pid; \
	  echo "TeamWork started (pid $$(cat $(LOCAL_RUN)/teamwork.pid)) -> :8000 (UI needs a prior 'npm run build')"; \
	else \
	  echo "WARN: TeamWork checkout not at $(TEAMWORK_PATH) - skipping. Set TEAMWORK_PATH=... to override."; \
	fi

_local-prax:
	@mkdir -p $(LOCAL_RUN)
	@if [ -f $(LOCAL_RUN)/prax.pid ] && kill -0 $$(cat $(LOCAL_RUN)/prax.pid) 2>/dev/null; then \
	  echo "Prax already running (pid $$(cat $(LOCAL_RUN)/prax.pid))."; \
	else \
	  MEMORY_ENABLED=true SANDBOX_ENABLED=false TEAMWORK_ENABLED=true TEAMWORK_URL=http://localhost:8000 \
	    nohup $(LOCAL_PY) app.py >$(LOCAL_RUN)/prax.log 2>&1 & echo $$! >$(LOCAL_RUN)/prax.pid; \
	  echo "Prax started (pid $$(cat $(LOCAL_RUN)/prax.pid)) -> :5001"; \
	fi

shutdown:
	@echo "Stopping native local stack..."
	@if [ -d $(LOCAL_RUN) ]; then \
	  for f in $(LOCAL_RUN)/*.pid; do \
	    [ -e "$$f" ] || continue; \
	    pid=$$(cat "$$f"); name=$$(basename "$$f" .pid); \
	    if kill -0 "$$pid" 2>/dev/null; then \
	      kill "$$pid" 2>/dev/null && echo "  stopped $$name (pid $$pid)"; \
	    else \
	      echo "  $$name not running (stale pid $$pid)"; \
	    fi; \
	    rm -f "$$f"; \
	  done; \
	fi
	@pkill -f "python app.py" 2>/dev/null && echo "  swept stray app.py" || true
	@pkill -f "teamwork.cli" 2>/dev/null && echo "  swept stray teamwork.cli" || true
	@command -v neo4j >/dev/null 2>&1 && neo4j stop >/dev/null 2>&1 && echo "  stopped neo4j" || true
	@echo "Done."

local-status:
	@echo "-- Native local stack --"
	@printf "  %-9s" "Qdrant";   curl -s -o /dev/null --max-time 2 http://localhost:6333/        && echo " up   -> :6333" || echo " down -> :6333"
	@printf "  %-9s" "Neo4j";    curl -s -o /dev/null --max-time 2 http://localhost:7474/        && echo " up   -> :7687" || echo " down -> :7687"
	@printf "  %-9s" "TeamWork"; curl -s -o /dev/null --max-time 2 http://localhost:8000/        && echo " up   -> :8000" || echo " down -> :8000"
	@printf "  %-9s" "Prax";     curl -s -o /dev/null --max-time 2 http://localhost:5001/health  && echo " up   -> :5001" || echo " down -> :5001"
	@echo "Logs: make local-logs   Stop: make shutdown"

local-logs:
	@mkdir -p $(LOCAL_RUN)
	@echo "Tailing $(LOCAL_RUN)/*.log (Ctrl-C to stop)..."
	@tail -F $(LOCAL_RUN)/*.log
