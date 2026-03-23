.PHONY: lint test actions ci

lint:
	uv run ruff check .

test:
	FLASK_SECRET_KEY=ci-test-key uv run pytest tests/ -x -q

actions:
	actionlint

ci: actions lint test
	@echo "\nAll CI checks passed."
