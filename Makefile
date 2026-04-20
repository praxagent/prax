.PHONY: lint layers test actions ci

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
