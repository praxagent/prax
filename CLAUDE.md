# Prax — AI Assistant

Multi-channel AI assistant (TeamWork web UI, Discord, SMS/voice) powered by a LangGraph ReAct agent with 97+ tools.

## Quick Reference

- **Language:** Python 3.13, Flask backend, LangGraph agent
- **Package manager:** uv (not pip)
- **Tests:** `uv run pytest tests/ -x -q` (exclude sandbox-dependent tests with `-k "not test_imported_run_command_forces_cwd"`)
- **Lint:** `uv run ruff check` (must pass — CI enforces it)
- **Lint fix:** `uv run ruff check --fix`

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
- Sub-agents (spokes) are in `prax/agent/spokes/` — browser, content, coding
- TeamWork integration via `prax/services/teamwork_service.py` (HTTP client to TeamWork API)

## Rules

- Always run both tests AND lint before considering a change complete
- Never modify `.env` — secrets are passed via environment variables
- Use `uv` for all Python operations, never `pip`
