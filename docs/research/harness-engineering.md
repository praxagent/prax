# Harness Engineering — The Environment Is the Agent

[← Research](README.md)

Three recent publications (SWE-agent 2024, Anthropic Nov 2025, OpenAI Feb 2026) converge on the same thesis: **the durable moat in applied AI is the harness, not the model**. The coding agent itself — Claude Code, Codex, Gemini CLI — is becoming a commodity; what differentiates teams is the environment they've built around it. This note summarises the three canonical write-ups and the community taxonomy that catalogues the ecosystem, and maps each finding to Prax's current state.

### 28. Agent-Computer Interface (ACI) — interface design as cognitive architecture

**Finding:** A purpose-built interface between an LM agent and a computer produces a **64% relative improvement** on SWE-bench over the same model using a raw bash shell. Same model, same compute, same task — the only variable was interface design.

GPT-4 resolved **3.97%** of SWE-bench issues through a standard shell; **12.47%** through the SWE-agent ACI. Ablation studies confirmed each ACI component carried weight:

- **Capped search** (`find_file`, `search_file`, `search_dir`) returns at most 50 matches; overshoot is suppressed with a "refine your query" message. This converts context-flooding into a forcing function for specificity.
- **Stateful file viewer** showing exactly 100 lines at a time with explicit line numbers prepended to every visible line — tested against 30-line and full-file variants; 100 was the Goldilocks number.
- **Edit-with-linter** — the edit command accepts `(start_line, end_line, replacement_text)` and runs a linter immediately. Syntax-broken edits are rejected before application, with a diff showing the original code and the failed edit.
- **Context compression** — observations beyond the last 5 turns are collapsed to single-line summaries.

The underlying insight: **for an LM agent, the interface *is* the mind**. The model has no selective attention over the context window; noise in the prompt competes with signal for reasoning capacity. Every design decision in the ACI is about managing cognitive load, not exposing capability.

**Prax state (updated 2026-04-19):**
- **Capped search** ✅ (`conversation_search` caps at 50).
- **Context compression** ✅ (`prax/agent/context_manager.py:compact_history`).
- **Stateful file viewer** ✅ — `sandbox_view` / `sandbox_scroll` /
  `sandbox_goto` in `prax/agent/sandbox_tools.py`: 100-line windows,
  prepended line numbers, per-(user, path) last-viewed position.
  Replaces `cat` for sandbox file inspection.  Result-capping pattern
  from the SWE-agent ACI.
- **Edit-with-linter** ✅ — `_validate_syntax()` in
  `prax/agent/workspace_tools.py` syntax-checks
  `.py` (AST parse), `.json` / `.yaml` / `.toml` (stdlib decoders)
  before every `workspace_save` / `workspace_patch`; broken writes
  are rejected with a structured error and the file is not modified.

See [§3 in production-patterns](production-patterns.md) for the pattern family and [§5 in orchestration](orchestration.md#5) for the SWE-agent citation.

**Reference:**
- Yang, Jimenez, Wettig, Lieret, Yao, Narasimhan, Press, "SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering," NeurIPS 2024 — [arXiv:2405.15793](https://arxiv.org/abs/2405.15793) · [GitHub](https://github.com/SWE-agent/SWE-agent)

### 29. Long-running harnesses — surviving the context window boundary

**Finding:** Frontier coding models running in a single loop across multiple context windows **consistently fail** to build production-quality applications, even with compaction. Two failure modes dominate:
1. **One-shotting too much** — agent tries to implement every feature before finishing any, runs out of context mid-build, leaves the next session with a half-implemented app and no map of what's done.
2. **Premature victory** — a later session looks around, sees code exists, and concludes the job is done on a partially-complete application.

Both share a root cause: no persistent, structured ground truth that survives the context window boundary. Anthropic's Claude Code harness solves this with a **two-agent architecture**:

- **Initializer agent** (runs once) produces three artefacts:
  - `init.sh` — deterministic dev-env bootstrap the coding agent runs at session start
  - `feature_list.json` — enumerated end-to-end features with `passes: true/false` per feature (JSON chosen deliberately — empirically, models tamper with JSON files less than Markdown). In the claude.ai clone experiment this file had 200+ features, all initialised to `false`.
  - `claude-progress.txt` — rolling human-readable session log, plus an initial git commit
- **Coding agent** (runs every subsequent session) follows a mandatory startup ritual: `pwd` → read progress → read feature list → `git log` → run `init.sh` → basic smoke test → *only then* pick the highest-priority failing feature. Every session ends with a git commit, an updated progress file, and a clean-mergeable state.

A second critical piece: **Puppeteer MCP** for real browser-level verification. Unit tests passing is not evidence a user-visible feature works. The quality of an agent's work is bounded by the quality of its feedback loops.

**Prax state (updated 2026-04-19):**
- **Per-space progress file** ✅ — `progress_read` /
  `progress_append` / `progress_detail` in `workspace_tools.py`,
  service in `prax/services/progress_service.py`.  Stored at
  `workspaces/{user}/library/spaces/{slug}/.progress.md`.  Bounded
  by construction (≤6000 chars, 3 sections: Archive, Recent, Open
  threads); auto-compaction folds the oldest entries into the
  Archive paragraph via a LOW-tier LLM summariser.  Detail files
  live in `.progress/YYYY-MM-DD-{id}.md` and are *not* auto-loaded.
- **Browser E2E from agent loop** ✅ — `browser_verify(flow)` in
  `prax/agent/cdp_tools.py`.  Eight verbs (`goto`, `click`, `type`,
  `key`, `wait_for`, `assert_visible`, `assert_text`, `screenshot`);
  short-circuits on first failure and snapshots the page for
  debugging.
- **Two-agent architecture** ❌ / **`feature_list.json`** ❌ /
  **`init.sh` startup ritual** ❌ / **end-of-session clean-state**
  ❌ — consciously tabled.  Prax already delegates coding work to
  Claude Code / OpenCode / Codex (see
  [`docs/plans/prax-as-coding-agent.md`](../plans/prax-as-coding-agent.md))
  so reimplementing these patterns at the Prax layer duplicates
  harness state that already exists inside the coding CLI's own
  harness.  Revisit if the coding-CLI delegation seam becomes the
  consistent failure mode.

**Reference:**
- Young, "Effective harnesses for long-running agents," Anthropic Engineering, 2025-11-26 — [anthropic.com/engineering/effective-harnesses-for-long-running-agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)

### 30. Repository as system of record — progressive disclosure at scale

**Finding:** OpenAI's internal Harness team shipped ~1M lines of code across ~1,500 PRs in five months **with zero manually-written code**. Three engineers scaled to seven; per-engineer throughput *rose* with team size to ~3.5 PRs/engineer/day. The work was not "writing code" — it was designing the environment, specs, and feedback loops that let agents write code reliably.

Concrete patterns that carried the load:

- **Repository is the system of record.** Anything not accessible to the agent in-context effectively doesn't exist. Slack threads, Google Docs, tribal knowledge — invisible. They moved design docs, architecture maps, and in-progress plans into the repo as first-class artefacts.
- **Short `AGENTS.md` (~100 lines) + structured `docs/` tree.** The monolithic instruction file approach failed in four predictable ways (context competition, too-much-guidance-is-no-guidance, instant rot, unverifiable drift). Progressive disclosure — a compact entry point that points to deeper sources — outperforms comprehensive dumps.
- **Git worktree isolation per task** so parallel agents never collide on the filesystem.
- **Application legibility:** bootable-per-worktree, Chrome DevTools Protocol wired into the agent runtime, full local observability stack (logs via LogQL, metrics via PromQL, traces via TraceQL) queryable by the agent — the same tools a human SRE would use. Agents debug production-like issues with real telemetry, not inference from code.
- **Custom linters enforce architectural invariants.** A rigid layer model (domains → layers → permissible dependency edges) validated by linters written by Codex itself. Linter error messages are formatted for injection into agent context — violation + rule + remediation in a single actionable feedback message.
- **"Golden principles" + cleanup bots.** Mechanical rules (prefer shared utilities, validate at boundaries) enforced by recurring background tasks that scan for drift, update quality grades, and open targeted refactor PRs — most reviewable and automergeable in under a minute.
- **Minimal blocking merge gates.** When agent throughput far exceeds human attention capacity, pull requests waiting for review are *blocking agent work*. Test flakes get follow-up runs, not investigations. Corrections are cheap; waiting is expensive.

**Prax state (updated 2026-04-19):**
- **Repository as system of record** ✅ (`docs/` is comprehensive).
- **Progressive disclosure** ✅ — top-level [`AGENTS.md`](../../AGENTS.md)
  is a ~90-line map pointing into `docs/`.
- **Worktree isolation** ✅ (`self_improve_start` in
  `prax/agent/codegen_tools.py`).
- **CDP** ✅ (`prax/agent/cdp_tools.py`) + structured
  `browser_verify` on top.
- **Observability-as-tools** ✅ — `obs_query_logs` / `obs_query_metrics`
  / `obs_query_traces` in `prax/agent/obs_tools.py`, backed by
  `prax/services/obs_service.py`.  Thin HTTP wrappers over Loki /
  Prometheus / Tempo with result caps and graceful lite-mode
  degradation (empty URL → `status: not_available` instead of a
  crash).  Registered only when `OBSERVABILITY_ENABLED=true`.
- **Custom architecture linters** ✅ —
  [`scripts/check_layers.py`](../../scripts/check_layers.py) runs
  in `make ci`.  Enforces: plugin isolation (no direct
  `prax.services.*` / `prax.agent.*`), no reverse dependency
  (services → agent, with `llm_factory` / `user_context` carve-outs),
  services HTTP-agnostic (no `prax.blueprints.*`).  Existing
  violations grandfathered in an `ALLOWLIST`.
- **"Golden principles" + cleanup bots** ❌ — deferred until drift
  is actually observed.
- **Minimal merge gates** ❌ — `self_improve_submit` remains
  intentionally disabled; see C13 in
  [`docs/plans/improvement_tier.md`](../plans/improvement_tier.md).

**Reference:**
- OpenAI Harness team (Victor Zhu, Zach Brock et al.), "Harness engineering: leveraging Codex in an agent-first world," 2026-02-11 — [openai.com/index/harness-engineering](https://openai.com/index/harness-engineering/)

### 31. The harness ecosystem — a 7-layer taxonomy

**Finding:** The community-maintained *awesome-agent-harness* catalogue frames the ecosystem as seven layers stacked above a commoditised execution layer. Its central analogy: **agent = CPU, context = RAM, harness = OS**.

| Layer | Role | Representative tools |
|-------|------|----------------------|
| 1. Human oversight | Humans steer; agents execute | — |
| 2. Spec / requirements tools | Turn human intent into structured task DAGs before execution | Chorus |
| 3. Full-lifecycle platforms | Requirements → delivery with approval gates | — |
| 4. Task runners | Issue-tracker → agent → PR pipelines | — |
| 5. Agent orchestrators | Parallel agents with git-worktree isolation | Vibe Kanban, Emdash, Composio |
| 6. Harness frameworks/runtimes | Composable primitives + persistent infra (memory, scheduling) | Claude Agent SDK |
| 7. Coding agents | Commoditised execution | Claude Code, Codex, Gemini CLI, Aider |

The sharp claim is that **the model is almost irrelevant; the harness is everything**. Chorus in particular is interesting: it inverts the "human writes the spec" flow (a major failure point because humans are imprecise in the ways agents need precision) — the AI proposes the task DAG and humans sit in a verification/approval gate.

**Prax state (updated 2026-04-19):**
- **Layers 6 and 7** ✅ — Prax is both a harness framework and an
  execution agent; layer-4/5 infrastructure (worktrees, scheduler)
  exists.
- **Task runner** ✅ — `prax/services/task_runner_service.py` polls
  the Library Kanban + top-level todos every N minutes (opt-in via
  `TASK_RUNNER_ENABLED`) and spawns a synthetic orchestrator turn
  per `assignees=["prax"]` pickup.  Not an issue-tracker integration
  (no Linear/GitHub ingestion yet) — but the equivalent via Prax's
  own task surfaces.
- **Spec tools layer (L1 graduation)** ❌ — still no structured
  task-DAG generation step before tool execution; matches the
  "pure L0" diagnosis in the agentic-todo-flows research
  (§20–§27 of [agentic-todo-flows](agentic-todo-flows.md)).  Tabled
  in [`docs/plans/prax-as-coding-agent.md`](../plans/prax-as-coding-agent.md)
  as C14 — wants a prototype (hand-draft a typed DAG for one real
  flow) before building.

**Reference:**
- AutoJunjie, "awesome-agent-harness" — [github.com/AutoJunjie/awesome-agent-harness](https://github.com/AutoJunjie/awesome-agent-harness)

### 32. Trace introspection — "have I solved this before?"

**Finding:** None of the four reference sources explicitly cover
semantic lookup of an agent's *own* past execution traces, but the
pattern is a natural extension of the harness-engineering thesis:
**the agent's history is part of the environment.**  If Prax can't
ask itself "did I already solve something like this?", it will
re-derive solutions from scratch every turn — wasting tool calls
and drifting in behaviour across sessions.

Every completed execution graph already persists to
`.prax/graphs/graphs-YYYY-MM-DD.jsonl` (7-day window, workspace-
global).  The trace carries the user intent (`trigger`), span
summaries, tool-call counts, and outcomes.  Treating this archive
as a *retrievable surface* gives the orchestrator a natural
self-reference loop: before starting a non-trivial task, look up
similar prior traces, read the tool sequence that worked, and
adapt rather than re-derive.

**Prax implementation (2026-04-19):**
- `trace_search(query, top_k)` — semantic search via a dedicated
  Qdrant collection `prax_trace_summaries`.  Lazy-indexed: first
  call per process scans the JSONL archive and upserts any trace
  not yet embedded.  Reuses the existing Qdrant + embedder stack
  (`prax/services/memory/vector_store.py`,
  `prax/services/memory/embedder.py`).
- `trace_detail(trace_id)` — fetches the full structured span tree
  from in-memory `_active_graphs` or the JSONL files.
- **Graceful degradation** when Qdrant / embedder aren't configured
  (lite mode): tools return a `not_available` message pointing the
  agent at `conversation_search` / `review_my_traces` instead of
  crashing.
- The system prompt tells Prax to reach for `trace_search` *before*
  starting a non-trivial task.  This is the concrete mechanism for
  the "don't re-solve what you've already solved" discipline.

Complements — does not replace — the existing introspection
surfaces:

| Tool | What it does | When to use |
|---|---|---|
| `trace_search` | Semantic similarity over past traces | "Have I done something like this before?" |
| `trace_detail` | Full span tree of one trace | "What exactly did I do in that past run?" |
| `review_my_traces` | LLM-digested narrative over recent traces | "What am I doing wrong / right lately?" |
| `conversation_search` | Keyword over conversation log | "Did we ever talk about X?" |
| `memory_recall` | Semantic over long-term memories (facts/entities) | "What do I know about X?" |

### Design patterns that repeat across all four sources

1. **Progressive disclosure** over context dumps — short entry point + structured map.
2. **One agent, one worktree** — filesystem isolation is the concurrency primitive.
3. **Spec-first; repository as system of record** — if the agent can't read it from the repo, it doesn't exist.
4. **Mechanical architecture enforcement** — linters + structural tests scale; code review doesn't, when agents out-produce humans 10×.
5. **Integrated, tight feedback loops** — syntax errors at edit time, UI bugs via browser automation, runtime issues via queryable observability. Every loop-tightening is a permanent quality improvement.

The engineering question shifts from *"how do I write a better prompt?"* to *"what capability is missing from the environment that is causing this failure class to appear?"* The prompt fix is local and temporary; the environment fix is general and permanent.
