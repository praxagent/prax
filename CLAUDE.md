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
docs/                   # Documentation (architecture, agents, guides, research)
```

The **sandbox** (coding agents + browser + desktop container) is its own repo,
`../prax-sandbox`, consumed via the `prax_sandbox_client` dependency (uv path
source). Prax runs with or without it (`SANDBOX_ENABLED`); local or remote
(`SANDBOX_DAEMON_URL`). See `docs/infrastructure/sandbox.md`.

**Sibling-repo pattern.** The agent-agnostic add-ons live *next to* the prax
checkout and are consumed as siblings, never vendored in: `../teamwork` (UI),
`../prax-sandbox` (exec/browser/desktop), and `../prax-secrets-proxy` (the
credential-injecting egress proxy for **keyless Prax** — real keys held by a
separate, isolated service so a compromised Prax has nothing to steal). The
Makefile parameterises each path (`TEAMWORK_PATH`, `SANDBOX_PATH`,
`SECRETS_PROXY_PATH`) and docker-compose builds each from `${*_PATH}` (opt-in
profiles for the proxy). Prax's only coupling to the proxy is the `OPENAI_BASE_URL`
/ `ANTHROPIC_BASE_URL` wiring (`prax/agent/llm_factory.py`) — no code dependency; it
degrades to keys-in-`.env` when unset. Integration doc:
`docs/security/secrets-proxy.md` (proxy internals live in that repo's README).

**Credential registry — the never-drift contract.** Every credential Prax
supports lives in ONE canonical place: `prax/services/credential_registry.py`
(mirror doc: `docs/security/credentials.md`), classified `PROXY_MODEL` (proxied
today via base-URL), `PROXY_FORWARD` (Tier-2 transparent forward proxy, planned),
or `PROXY_LOCAL` (in-process/inbound/own-infra, never proxyable). A drift-guard
test (`tests/test_credential_registry.py`) **fails CI** if a `*_KEY`/`*_TOKEN`/
`*_SECRET`/`*_API` field is added to `settings.py` without a registry row — so
Prax and the proxy can't silently diverge. When you add any credential, add its
registry row in the same change.

## Docs placement — federate by ownership

Prax, **TeamWork** (`../teamwork`), **prax-sandbox** (`../prax-sandbox`), and
**prax-secrets-proxy** (`../prax-secrets-proxy`) are separate, independently-
cloneable repos. The add-ons are **agent-agnostic** (you can run them with a
different harness), so a doc belongs to the repo that **owns the thing it
documents**, not wherever it's convenient — e.g. the proxy's own internals live in
its README, while *how Prax uses it* lives in `docs/security/secrets-proxy.md`:

- **Component-intrinsic docs** (TeamWork's own UI/panels/mobile UX/API; the
  sandbox's container/browser/desktop/remote internals) live in **that repo's
  `docs/`** — `teamwork/docs/`, `prax-sandbox/docs/`.
- **Prax docs** cover Prax itself **and the integration** — how Prax *drives*
  TeamWork or *consumes* the sandbox (e.g. `docs/infrastructure/sandbox.md` is the
  Prax↔sandbox integration doc; the sandbox internals are in `prax-sandbox/docs/`).
- **Smell test:** if a doc still makes sense to someone running the component with
  a *different* agent, it belongs in the component repo. If it only makes sense
  with Prax as the brain, it stays here. Heavy mentions ≠ ownership — most Prax
  docs reference the sandbox/TeamWork because Prax integrates with them.
- Same rule for backlog ideas: component-facing ideas live in the component's
  backlog (`teamwork/docs/BACKLOG.md`); cross-cutting items are tracked on both
  sides with a cross-link (see `docs/IDEAS_BACKLOG.md` #21).

## Key Patterns

- All tools go through `prax/agent/governed_tool.py` (risk classification, audit logging)
- **All agent loops are built through `prax/agent/agent_loop.py`
  (`build_agent_loop`)** — never import `langchain.agents` or `langgraph`
  directly; the layer linter (rule 4) fails CI if you do.  In-loop
  middleware (provenance tainting of untrusted tool results, per-step
  heartbeat) lives in `prax/agent/loop_middleware.py` behind
  `AGENT_MIDDLEWARE_ENABLED` (default off; the eval gate governs the flip).
  Full stack contract: [`docs/architecture/lang-stack.md`](docs/architecture/lang-stack.md).
- Settings are Pydantic fields with env var aliases in `prax/settings.py`
- Plugin tools are loaded from `prax/plugins/tools/` and wrapped with governance
- Sub-agents (spokes) live in `prax/agent/spokes/` — browser, content,
  course, desktop, finetune, knowledge, memory, research, sandbox,
  scheduler, sysadmin, **tasks**, workspace.  The orchestrator
  delegates to them via the `delegate_<spoke>` tools.  The
  orchestrator itself carries ~42 tools (delegations + kernel +
  planning + meta) — well under the ~50-tool accuracy threshold
  Anthropic documents.  When adding tools, prefer spoke-internal
  placement over orchestrator-level.
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
- **Edit-with-linter:** `workspace_save` / `workspace_patch` run a
  language-aware syntax check (AST parse for .py, JSON/YAML/TOML
  decoders) and reject broken writes *before* they hit disk.  See
  `prax/agent/workspace_tools.py:_validate_syntax`.
- **Architectural layer linter** (`scripts/check_layers.py`) runs as
  part of `make ci` and catches cross-layer imports — plugins must
  route through the capability gateway; services must not import
  agent modules (except the `llm_factory` / `user_context` carve-
  outs); services must not import blueprints.  Grandfathered
  violations live in an `ALLOWLIST`; new code must not add to it.
- **Task runner** (`prax/services/task_runner_service.py`,
  opt-in via `TASK_RUNNER_ENABLED=true`) watches the Library Kanban
  and top-level todo list every ~5 minutes for items with
  `assignees=["prax"]` and spawns a synthetic orchestrator turn per
  pickup.  Respects the agent_plan/Kanban wall — Prax's internal
  plan stays ephemeral; only the user-created task gets updated.
  Management tools (`task_runner_status` / `pause` / `resume`) live
  in the `tasks` spoke.
- **Per-space session progress** survives the context-window
  boundary: `progress_read(slug)` at session start,
  `progress_append(slug, outcome, open_threads)` at session end.
  Bounded by construction (≤6000 chars, 3-section structure with
  LLM compaction when full).  Detail files in
  `workspaces/{user}/library/spaces/{slug}/.progress/` are *not*
  auto-loaded — fetch on demand via `progress_detail(slug, date)`.
- **Trace introspection** — `trace_search(query, top_k)` does
  semantic search over past execution traces (embeds `trigger` +
  top span summaries into a Qdrant collection
  `prax_trace_summaries`; lazy-indexed on first call per process).
  `trace_detail(trace_id)` fetches the full structured record of
  a specific trace.  Both tools degrade gracefully when Qdrant
  isn't available (lite deployments).  Prefer over
  `review_my_traces` when you want structured data rather than a
  reviewer-LLM narrative; prefer over `conversation_search` when
  you want semantic task-similarity rather than keyword matching.
  See `prax/services/trace_search_service.py`.
- **Reliability & quality flags** — a set of opt-in features
  (cross-provider LLM failover, durable checkpoints + resume,
  continuous/decomposed evals + `make eval`, retrieval rerank/query
  expansion + hybrid knowledge search, prompt selectivity, intent
  clarification, deny-by-default tool boundaries, hallucination-guard
  metrics) all gate behind env flags that **default to prior
  behaviour** (so `make ci` stays green keyless).  All flags are in
  `prax/settings.py` + `.env-example`; the rationale and per-feature
  anchors are in
  [`docs/research/reliable-agentic-systems-bayer.md`](docs/research/reliable-agentic-systems-bayer.md).
  When extending these, preserve the default-off contract and gate
  behaviour changes so the eval gate governs rollout.  **First eval-gate
  run (2026-07-08)** A/B'd every measurable flag — full verdicts in
  [`docs/research/flag-eval-campaign-2026-07-08.md`](docs/research/flag-eval-campaign-2026-07-08.md):
  `AGENT_MIDDLEWARE_ENABLED` and `PROMPT_SELECTIVITY_ENABLED` are now the
  recommended configuration (flipped in `.env-example`); intent
  clarification and deny-by-default tool boundaries were REJECTED on
  measured evidence (cost/correctness regressions) — don't flip them
  without new data; retrieval rerank/expansion and attended quarantine
  are deferred pending better eval coverage.  Code defaults stay off.
- **MCP server** (`prax/mcp/`, default-off) — exposes a curated,
  bearer-gated subset of Prax tools to *other* agents over the Model
  Context Protocol (`POST /mcp`, JSON-RPC, no SDK dep). Fail-closed
  (mounts only when a client is configured). **Per-caller identity**:
  each client token (`MCP_BEARER_TOKEN` or an `MCP_CLIENTS_PATH`
  registry) maps to its own Prax `user_id` + tool allowlist; write
  (MEDIUM) tools are grantable per-caller, HIGH never; governance stays
  in front. The "make Prax usable by other agents" surface. See
  [`docs/infrastructure/mcp-server.md`](docs/infrastructure/mcp-server.md).

## Rules

- **⭐ PRIME DIRECTIVE — every improvement must be GENERAL and HONEST.**
  When an eval or task reveals a weakness, the fix must **generalise the problem
  class and make Prax better on scenarios far beyond the single problem** — never
  a benchmark-specific patch, never overfit to the eval. If someone who knows the
  benchmark reads the change, they must NOT be able to tell which task it targeted.
  *A fix that only helps one problem is not a fix — it's a spike, and spikes are
  forbidden here (they're a safety issue: reward-hacking generalises to
  misalignment).* Equally non-negotiable: **Prax is HONEST.** It says "I don't
  know" rather than bullshitting; it never fabricates, bluffs, or guesses to look
  decisive or to dodge a zero. A made-up answer is worse than an honest one because
  it misleads. **Gaming a metric by guessing is BOTH a spike and a lie — doubly
  forbidden.** Before shipping any change, ask: *does this generalise, and is it
  honest?* If not, it doesn't ship. Corollary for measurement: when a benchmark
  looks weak, **audit the checker first** — three times running the "gap" was our
  scorer under-crediting Prax, not Prax's capability (`docs/research/`
  lanyon / proofjudge / axiomprover / eval-rigor-review). Fix the measurement
  before "fixing" the model.
- **Always run `make ci` before considering a change complete.**
  Don't declare work done until it's green.
- Never modify `.env` — secrets are passed via environment variables
- **Never commit runtime data or secrets** — databases (`*.db` and backups like
  `identity.db.bak2-*`, `conversations.db.legacy-backup`), `.env`, logs,
  `workspaces/`.  Stage explicitly; do **not** `git add -A` (it swept a DB
  backup into a public commit once).  Ignore backups with globs, not exact names
  (`identity.db*`, not `identity.db`).  Recovery + `git filter-repo` surgery
  playbook: [`docs/guides/git-hygiene.md`](docs/guides/git-hygiene.md).
- Use `uv` for all Python operations, never `pip`
- Never rename a library function without also updating its callers
  in tests, routes, and agent tools — the codebase has no runtime
  coverage net for a broken import until you hit it in production
- **Never spike benchmarks.**  When an eval reveals a weakness, the
  fix in the system prompt or code must be an **abstraction of the
  problem class** — not a specific example from the failed task.  If
  someone who knows the benchmark reads the system prompt, they must
  NOT be able to tell which tasks failed.  The instruction should
  improve Prax on all queries in the class, not just the ones in
  the eval set.
- **Be honest about what's verified.**  Unit tests prove logic, not
  that a real third-party API/credential path works.  When you ship a
  feature that talks to an external service you did **not** exercise
  live, add a row to [`docs/VERIFICATION_LEDGER.md`](docs/VERIFICATION_LEDGER.md)
  in the same PR (status: unit-tested-only / unverified).  Shipping
  unverified is fine; *implying* it's verified is not.  Update the row
  when it's later run for real.
- **The eval matrix & historical record.**  `make eval-matrix` runs
  every benchmark on its **real** dataset through the full harness
  (`MATRIX_LIMIT` cases each, cheap OpenRouter model) — the reproducible
  public scorecard.  Real datasets cache under `$PRAX_EVAL_DIR/datasets/`
  (data-only, never committed).  Full how-to + prereqs:
  [`docs/guides/eval-matrix.md`](docs/guides/eval-matrix.md).  The
  **historical results record** (`docs/eval-results/`, a committed,
  trend-tracking scorecard for public accountability) is **planned, not
  yet started** — held until the matrix is shaken down and the last
  benchmark is added.  When it lands, its hard rule is **aggregates only
  (pass-rate/tokens/cost/config/commit) — NEVER benchmark questions or
  answers in the public repo** (contamination firewall); raw per-case
  runs stay in `$PRAX_EVAL_DIR`.

## Solo dev flow (GitHub)

One human maintainer, so review is **CI + agentic review, not human
approvals** (deliberate — a second sock-puppet account was tried and dropped;
same brain, no independence):

- `main` is protected on all public praxagent repos: changes go through a PR
  with the **`test` check required** and **0 required approvals** — self-merge
  the moment CI is green.  Auto-merge is enabled; the normal flow is
  `gh pr create` → `gh pr merge --auto --squash`.
- Before merging anything non-trivial, run **`/code-review`** on the diff
  (`/code-review ultra` for substantive changes) — the PR template's checklist
  reminds you.  This is the review; treat its confirmed findings like a
  reviewer's blocking comments.
- Admins bypass protection (`enforce_admins` off), so a **direct push to
  `main` is allowed for small docs/infra one-offs** — never for behavior
  changes, and never force-push `main`.
- **Releases**: release-please runs in manifest mode
  (`release-please-config.json` + `.release-please-manifest.json`); merging
  the auto-opened `chore(main): release X` PR creates the tag.  **Expect that
  PR to appear/update a minute or two after any releasable merge to `main`**
  — and unless a `RELEASE_PLEASE_TOKEN` secret is configured, it arrives with
  NO CI checks (PRs opened by the workflow's `GITHUB_TOKEN` don't trigger
  workflows), so the required `test` check never reports and it sits
  unmergeable.  Nudge it: close and reopen (`gh pr close N && gh pr reopen N`)
  to fire CI, then auto-merge; or `gh pr merge N --squash --admin`
  (acceptable: the diff is generated version+changelog only).  Permanent fix:
  add a fine-grained PAT (contents + pull-requests: write) as the
  `RELEASE_PLEASE_TOKEN` repo secret — `release.yml` already prefers it when
  present, and release PRs then trigger CI like any other PR.  Only
  `feat`/`fix`/`perf`/`deps`/`revert` commits stage a release —
  `docs`/`chore`/`refactor`/`test`/`build`/`ci` are hidden and non-releasing
  (changelog-sections in `release-please-config.json`).  Don't recreate
  tags `v0.1.0`–`v0.16.0` — they're orphans from a pre-reset history,
  deliberately left in place.
- When a second human maintainer joins: raise required approvals to 1, add
  CODEOWNERS, and consider `enforce_admins` — this section is written for the
  solo phase only.

## Disk hygiene (live dev box)

This box hosts everything (Docker stack, eval runs, worktrees, caches) on one
77 GB disk, and heavy dev churn fills it. When it hits 100%, the sandbox (and
therefore Prax tool calls) fail with `No space left on device` — the 2026-07-08
outage. Habits:

- **`docker system prune` (or `docker image prune -af`) from time to time** —
  sandbox rebuilds and frequent restarts accumulate unused image layers
  (37 GB reclaimable found on 2026-07-08).
- **Watch for runaway outputs in the sandbox.** The actual 2026-07-08 cause
  was Prax running ffmpeg with an infinite lavfi source (`anullsrc`) and no
  `-t` bound → a 21 GB WAV in the container's `/tmp`. The container overlay
  IS the host disk — a runaway container process can take the whole box down.
  Check `docker exec prax-sandbox-sandbox-1 du -sh /tmp` when space vanishes.
  (Punch-list: bound the sandbox `/tmp` with a sized tmpfs in
  prax-sandbox's compose so this class of failure is contained.)
- Quick triage: `df -h /`, `docker system df`, then the usual suspects —
  container `/tmp`, `~/.cache`, old eval suite dirs, stray worktree venvs.

## Session checkpoint — `checkpoint.md`

Long working sessions maintain **`checkpoint.md` at the repo root** (gitignored
— never commit it). It is the resume point if the session is disrupted or the
connection drops: TJ reads it to know exactly where things stand and what to
pick up.

- **Write it early and update it frequently** — after every milestone (merge,
  campaign result, live-ops change, decision made or pending), not just at the
  end. A stale checkpoint is worse than none: state what changed and *when*.
- Contents: current live-server state (branch/commit, flags, health), work in
  flight (background tasks, PRs awaiting CI), results/decisions so far,
  **decisions pending from TJ**, bugs found-but-not-fixed, and concrete
  "if disrupted, resume by…" steps.
- It complements `~/PRAX/remaining_punchdown.md` (the cross-session handoff):
  the punchdown carries durable project state; `checkpoint.md` carries *this
  session's* fast-moving state.

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
