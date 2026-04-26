.PHONY: lint layers test actions ci tailscale-up tailscale-down tailscale-status

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
