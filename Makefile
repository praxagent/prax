.PHONY: lint test verify-actions ci

lint:
	uv run ruff check .

test:
	FLASK_SECRET_KEY=ci-test-key uv run pytest tests/ -x -q

verify-actions:
	@echo "Verifying GitHub Action tags..."
	@for ref in \
		actions/checkout@v4 \
		astral-sh/setup-uv@v6 \
		googleapis/release-please-action@v4.4.0; \
	do \
		repo=$${ref%%@*}; tag=$${ref##*@}; \
		if curl -sf "https://api.github.com/repos/$$repo/git/ref/tags/$$tag" > /dev/null 2>&1; then \
			echo "  ✓ $$ref"; \
		else \
			echo "  ✗ $$ref — tag not found!" && exit 1; \
		fi; \
	done

ci: verify-actions lint test
	@echo "\nAll CI checks passed."
