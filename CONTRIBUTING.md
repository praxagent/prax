# Contributing to Prax

> **Draft — 2026-09, pending maintainer review.** This distils the working
> rules in [CLAUDE.md](CLAUDE.md) and [AGENTS.md](AGENTS.md) for an outside
> contributor. Where the two disagree, CLAUDE.md wins — it is the source.

## Setup

- Python 3.13 and [`uv`](https://docs.astral.sh/uv/). CLAUDE.md: *"Use `uv`
  for all Python operations, never `pip`."*
- `uv sync --python 3.13`, then `cp .env-example .env` and edit it. The full
  install path (Docker or host) is in [README.md](README.md).
- `actionlint` is required by `make ci` (see the README's install notes).
- **Tests must pass with no API keys.** CI is keyless by design; a test that
  needs a real provider key is a bug in the test.

## Before you open a PR

1. **`make ci` must be green.** CLAUDE.md: *"Always run `make ci` before
   considering a change complete. Don't declare work done until it's green."*
   The target runs `actionlint`, `uv run ruff check .`, the layer linter
   (`scripts/check_layers.py`) and `pytest tests/ -x -q` with the
   sandbox-dependent tests excluded.
   - Targeted run: `FLASK_SECRET_KEY=ci-test-key uv run pytest tests/<file>.py -x -q`
   - Lint only: `make lint`; auto-fix: `uv run ruff check --fix`
2. **Every behaviour change is flag-gated and defaults to prior behaviour.**
   Add a Pydantic `Field` in `prax/settings.py` with a `SCREAMING_CASE` env
   alias, a commented line in `.env-example`, and — if it is a credential — a
   row in `prax/services/credential_registry.py` (the drift-guard test fails
   CI otherwise).
3. **Never spike benchmarks.** CLAUDE.md: *"When an eval reveals a weakness,
   the fix in the system prompt or code must be an abstraction of the problem
   class — not a specific example from the failed task. If someone who knows
   the benchmark reads the system prompt, they must NOT be able to tell which
   tasks failed."* Never quote benchmark questions or answers into the repo.
4. **Respect the layer rules.** Wrap new tools with
   `prax/agent/governed_tool.py`; build agent loops only through
   `prax/agent/agent_loop.py:build_agent_loop` (never import `langchain.agents`
   or `langgraph` directly); services must not import agent modules
   (bar the grandfathered `llm_factory` / `user_context` carve-outs) or
   blueprints. `scripts/check_layers.py` enforces this in `make ci`; do not
   add to its `ALLOWLIST`.
5. **Be honest about what is verified.** If a change talks to an external
   service you did not exercise live, add a row to
   [`docs/VERIFICATION_LEDGER.md`](docs/VERIFICATION_LEDGER.md) in the same PR.
6. **Update docs** when behaviour, flags or setup change. Docs live with the
   component that owns the thing (`docs/` here; TeamWork and sandbox internals
   in their own repos).

## Git hygiene

- **Never `git add -A`.** Stage files explicitly, then sweep before you commit
  (from the PR template):
  `git diff --cached --name-only | grep -iE '\.db($|[.\-])|\.bak|(^|/)\.env$|secret|token|\.pem$|\.key$'`
  must print nothing.
- Never commit runtime data or secrets: `*.db` and their backups, `.env`,
  logs, `workspaces/`. Recovery playbook if it happens:
  [`docs/guides/git-hygiene.md`](docs/guides/git-hygiene.md).
- Never rename a library function without updating its callers in tests,
  routes and agent tools.

## Pull requests

- `main` is protected; the **`test`** check must be green before a merge.
  Merges are squash merges done by the maintainer.
- **The PR title must be a conventional commit** (`feat:`, `fix:`, `docs:`,
  `chore:`, …). Squash-merge uses the title as the commit subject on `main`,
  and release-please parses that subject — a prose title means the work never
  reaches the changelog or a release.
- Fill in [`.github/pull_request_template.md`](.github/pull_request_template.md)
  — what and why, how it was tested, and the pre-merge checklist.
- Prefer fewer, larger PRs over one PR per small change; batch related work.

## License

The repository's [`LICENSE`](LICENSE) file is Apache-2.0. (`pyproject.toml`
still declares `MIT` — a known discrepancy for the maintainer to reconcile.)
