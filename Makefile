.PHONY: lint layers test actions ci tailscale-up tailscale-down tailscale-status sandbox-gpu sandbox-gpu-check

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
