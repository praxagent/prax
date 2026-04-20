# AGENTS.md — Entry-point map for agents working in this repo

This file is a **map**, not a manual. Read it first. Follow the
pointers into `docs/` for anything deeper. The design principle is
**progressive disclosure** — a compact orienting file that tells you
where to look, rather than a monolithic instruction dump that crowds
out the actual task.

For the strict rules you must not violate, read [CLAUDE.md](CLAUDE.md)
alongside this file.

## What Prax is

Multi-channel AI assistant (TeamWork web UI, Discord, SMS/voice)
powered by a LangGraph ReAct agent. Python 3.13 + Flask backend +
Pydantic settings. Hub-and-spoke orchestrator with ~11 domain spokes
and a plugin system. Package manager is **uv**, never pip.

Flask entry: [`app.py`](app.py). Agent entry: [`prax/agent/orchestrator.py`](prax/agent/orchestrator.py).
Settings: [`prax/settings.py`](prax/settings.py).

## Where things live

| If you need... | Start here |
|---|---|
| Overall architecture & request flows | [`docs/architecture/`](docs/architecture/) — `hub-and-spoke.md`, `request-flows.md`, `workspace.md` |
| How a specific agent pattern works (planning, delegation, self-improvement) | [`docs/agents/`](docs/agents/) |
| How to extend Prax (new tools, plugins, spokes) | [`docs/guides/extending.md`](docs/guides/extending.md) |
| Setup, auth, scheduler, troubleshooting | [`docs/guides/`](docs/guides/) |
| Why a design decision was made (research-backed rationale) | [`docs/research/`](docs/research/README.md) — start from the index, then jump to the relevant §N |
| Security boundaries / sandboxing model | [`docs/security/`](docs/security/), [`docs/research/plugin-sandboxing.md`](docs/research/plugin-sandboxing.md) |
| Infrastructure, deployments, observability | [`docs/infrastructure/`](docs/infrastructure/) |
| In-flight design notes / improvement plans | [`docs/plans/`](docs/plans/) |
| The Library (spaces → notebooks → notes, Kanban) | [`docs/library.md`](docs/library.md) |
| Harness engineering (ACI, long-running harnesses, env design) | [`docs/research/harness-engineering.md`](docs/research/harness-engineering.md) |

## Where code lives

| Area | Path |
|---|---|
| Orchestrator + top-level tools | `prax/agent/` |
| Sub-agents (spokes) — browser, content, course, memory, research, sandbox, scheduler, workspace, etc. | `prax/agent/spokes/` |
| Business logic services (conversation, workspace, memory, library, teamwork) | `prax/services/` |
| Flask route blueprints | `prax/blueprints/` |
| Plugins + capability gateway | `prax/plugins/` |
| Docker sandbox (Claude Code / OpenCode / Codex inside a container) | `sandbox/` |
| Tests (unit + e2e) | `tests/` |
| Utility scripts | `scripts/` |
| Deployment modes: full compose, lite compose, k8s | `docker-compose.yml`, `docker-compose.lite.yml`, `k8s/` |

## Load-bearing rules (the full list is in CLAUDE.md)

- **Before declaring a change done, run `make ci`.** Green CI is the
  gate. Targeted test run: `FLASK_SECRET_KEY=ci-test-key uv run pytest tests/<file>.py -x -q`.
- **`uv` for all Python ops, never pip.**
- **Never modify `.env`** — secrets come from env vars.
- **Never spike benchmarks.** A fix that works only on the failed eval
  task is a bug in the fix. See [CLAUDE.md](CLAUDE.md#rules) for why.
- **The two to-do systems (`agent_plan` vs Library Kanban) must not
  mix.** Details in [CLAUDE.md](CLAUDE.md#to-do-systems--the-wall) and
  [`docs/library.md`](docs/library.md).

## Prax-specific patterns worth knowing before you edit

- **All tools route through `prax/agent/governed_tool.py`** for risk
  classification and audit logging. If you add a tool, wrap it.
- **Hub-and-spoke delegation.** The orchestrator holds ~44 tools
  (12 `delegate_*` + kernel + planning/meta + trace introspection)
  and hands domain work off to focused spokes. Target ceiling:
  Anthropic's ~50-tool accuracy threshold. Don't pile more tools
  onto the orchestrator — add a spoke or put the tool inside an
  existing spoke's internal toolset. Rationale in
  [`docs/research/production-patterns.md`](docs/research/production-patterns.md#9-tool-overload-and-selection-degradation).
- **Trace introspection before non-trivial work.**
  `trace_search(query)` does semantic lookup over past execution
  traces ("have I solved this before?"); `trace_detail(trace_id)`
  fetches the exact tool sequence of a past run. Use both before
  re-deriving a solution from scratch.
- **Edit-with-linter on workspace writes.** `workspace_save` /
  `workspace_patch` syntax-check `.py/.json/.yaml/.toml` output
  before it lands on disk. Broken writes are rejected with a
  structured error; the file is not modified.
- **Layer-boundary enforcement** via [`scripts/check_layers.py`](scripts/check_layers.py)
  (runs in `make ci`). Don't add cross-layer imports; if you must,
  allowlist with a comment explaining why.
- **Background task runner** — opt-in via `TASK_RUNNER_ENABLED=true`.
  Picks up Kanban / todo items with `assignees=["prax"]`. See
  [`prax/services/task_runner_service.py`](prax/services/task_runner_service.py).
- **Workspace is git-backed.** Every write through
  `workspace_service.save_file` commits. Don't bypass it.
- **Per-space session progress** lives at
  `workspaces/{user}/library/spaces/{slug}/.progress.md` — read with
  `progress_read(slug)` when resuming work, append with
  `progress_append(...)` at session end. Bounded by construction.

## When to call which tool for this meta-work

- **Read the current state first.** `conversation_history`,
  `conversation_search`, and the relevant `docs/` file before
  proposing changes.
- **Use `agent_plan` for any multi-step turn.** Not the Library Kanban.
- **If stuck, `self_upgrade_tier("high")` and re-attempt.** Don't
  thrash at lower tiers.
- **If you fail at something, `review_my_traces`** writes its review
  into `workspaces/{user}/self-improvement-log.md`.
