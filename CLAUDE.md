# Prax — AI Assistant

Multi-channel AI assistant (TeamWork web UI, Discord, SMS/voice) powered by a LangGraph ReAct agent with 97+ tools.

## Quick Reference

- **Language:** Python 3.13, Flask backend, LangGraph agent
- **Package manager:** uv (not pip)
- **Before considering any change done:** `make ci` — this runs
  actionlint + `uv run ruff check .` + the full pytest suite with
  `-x` and the sandbox-dependent tests excluded.  If `make ci` is
  green, CI will be green.
- **Targeted test run:** `FLASK_SECRET_KEY=ci-test-key uv run pytest tests/<file>.py -x -q`
- **Lint only:** `make lint` (or `uv run ruff check .`)
- **Lint auto-fix:** `uv run ruff check --fix`
- **Run a sandbox-dependent test locally:** start the Docker sandbox
  (`docker compose up sandbox`), then
  `FLASK_SECRET_KEY=ci-test-key uv run pytest tests/test_plugin_capabilities.py::TestScopedFilesystem -q`
  — the Makefile excludes these tests by default because they
  require a live `/plugin_data` mount that CI doesn't have.

## Project Layout

```
app.py                  # Flask entry point
prax/
  agent/                # LangGraph agent, tools, orchestrator, spokes
  services/             # Business logic (conversation, workspace, memory, etc.)
  plugins/              # Plugin system (loader, registry, capabilities gateway)
  blueprints/           # Flask route blueprints (TeamWork webhook, etc.)
  settings.py           # Pydantic settings (env vars)
tests/                  # Unit + integration tests
scripts/                # Utility scripts
sandbox/                # Docker sandbox (OpenCode, Claude Code, Codex)
docs/                   # Documentation (architecture, agents, guides, research)
```

## Key Patterns

- All tools go through `prax/agent/governed_tool.py` (risk classification, audit logging)
- Settings are Pydantic fields with env var aliases in `prax/settings.py`
- Plugin tools are loaded from `prax/plugins/tools/` and wrapped with governance
- Sub-agents (spokes) live in `prax/agent/spokes/` — browser, content,
  course, finetune, knowledge, memory, research, sandbox, scheduler,
  sysadmin, workspace.  The orchestrator delegates to them via the
  `delegate_<spoke>` tools.
- The Library (projects → notebooks → notes, Kanban, archive, inbox,
  outputs) lives in `prax/services/library_service.py` +
  `prax/services/library_tasks.py`.  Storage is at
  `workspaces/{user}/library/spaces/{slug}/` — note "spaces", not
  "projects" (renamed 2026-04 to disambiguate from TeamWork's
  top-level project concept).
- TeamWork integration via `prax/services/teamwork_service.py` (HTTP client to TeamWork API)
- URL fetching (notes, auto-capture, `fetch_url_content`) routes
  through `prax/services/url_reader.py` which uses the Jina Reader
  API.  Set `JINA_API_KEY` in `.env` for paid-tier quota; free tier
  works without a key.

## Rules

- **Always run `make ci` before considering a change complete.**
  Don't declare work done until it's green.
- Never modify `.env` — secrets are passed via environment variables
- Use `uv` for all Python operations, never `pip`
- Never rename a library function without also updating its callers
  in tests, routes, and agent tools — the codebase has no runtime
  coverage net for a broken import until you hit it in production

## To-do systems — the wall

Prax has **two separate** to-do mechanisms. They are kept apart on
purpose and must NOT be mixed:

- **`agent_plan`** (`prax/agent/workspace_tools.py`, storage in
  `workspaces/{user}/agent_plan.yaml`) is **Prax's private working
  memory** — used by the orchestrator for multi-step turns. Ephemeral
  (cleared at end of turn), auto-injected into the system prompt every
  turn, compact YAML format. Use this for any of Prax's own
  tool-calling work.
- **Library Kanban** (`prax/services/library_tasks.py`, storage in
  `library/spaces/{slug}/.tasks.yaml`) is **the user's work board**
  — days-to-weeks work items with activity log, assignees, due-date
  reminders, and the full Library UI. Prax touches this ONLY when the
  user explicitly asks for something tracked there. (Note: the
  on-disk directory is `spaces/` not `projects/` — the hierarchy is
  TeamWork > Project > Space > Notebook > Note.)

Never mirror `agent_plan` steps onto the Library Kanban. Never use
Kanban tasks as ephemeral subgoals for a single turn. See
[`docs/library.md`](docs/library.md#scope--the-wall-between-kanban-and-agent_plan)
for the full rationale and [`docs/research/agentic-todo-flows.md`](docs/research/agentic-todo-flows.md)
for the research behind the decision.
