# Harness Improvement Tiers

**Source:** Itemised from the harness-engineering research note
([`docs/research/harness-engineering.md`](../research/harness-engineering.md)),
which synthesises the SWE-agent ACI paper, Anthropic's long-running
harness post, OpenAI's Codex harness-engineering post, and the
awesome-agent-harness 7-layer taxonomy.

The 14 gaps between Prax's current state and those four references are
triaged here into three tiers by expected leverage vs. cost. Tier A is
"ship in an afternoon each, compounds across every future turn."
Tier B is real structural investment. Tier C is strategic —
decisions about Prax's shape that want alignment before code lands.

## Status (2026-04-19)

- **Tier A — all shipped.** ✅ A1 edit-with-linter, ✅ A5 bounded
  progress file, ✅ A9 `AGENTS.md` map.
- **Tier B — all shipped.** ✅ B2 stateful sandbox viewer, ✅ B8
  `browser_verify`, ✅ B10 observability-as-tools, ✅ B11 layer
  linter.
- **Tier C — mixed.** ✅ C15 task runner shipped (Kanban + top-level
  todo pickup, opt-in).  Tabled in
  [`prax-as-coding-agent.md`](prax-as-coding-agent.md): C3/C4/C6/C7/
  C12 (codegen-harness bundle) and C14 (spec-tools layer).  Held:
  C13 (`self_improve_submit` stays disabled).
- **Side effect of the split:** orchestrator tool count went from
  49 → 42 tools (under Anthropic's ~50-tool accuracy threshold).
  Moved office-doc tools into the workspace spoke, `analyze_image`
  into the browser spoke, and `todo_*` into a new `tasks` spoke.
- **Trace introspection (new capability, 2026-04-19):**
  `trace_search(query)` + `trace_detail(trace_id)` shipped —
  semantic lookup over past execution traces so Prax can check
  "have I solved this before?" at the start of a complex task.
  Backed by a dedicated Qdrant collection
  (`prax_trace_summaries`); lazy-indexed; graceful lite-mode
  degradation.  System prompt now tells Prax to reach for these
  before starting non-trivial work.  See §32 in
  [`harness-engineering.md`](../research/harness-engineering.md).
  Orchestrator tool count 42 → 44.

---

## Tier A — high leverage, low cost

### A1. Edit-with-linter
**Gap #1.** Wrap `workspace_patch` and `workspace_write` so every edit
runs the appropriate linter (`ruff check` for Python, language-aware
for others) on the result and **rejects syntax-broken writes before
they hit disk**. Return a structured error to the agent containing the
original snippet and the failed edit. Cited in the SWE-agent paper as
one of the highest-leverage ACI components on ablation.

**Target files:** `prax/agent/workspace_tools.py`,
`prax/services/workspace_service.py`.
**Effort:** < 1 day.
**Risk:** low — failure mode is "edit rejected when it shouldn't be",
caught immediately by the agent retrying.

### A5. Rolling session progress file (bounded)
**Gap #5.** A per-user/per-space progress log that survives the
context-window boundary, so the next session can orient itself in a
handful of tokens rather than re-deriving state from scratch.

**How we stop it from polluting context** — the concern is legitimate:
naive append-only logs become giant files within weeks and defeat their
own purpose. The design has to enforce a bounded size by construction.

Proposed shape, modelled on Anthropic's `claude-progress.txt` plus
Prax's existing compaction discipline:

1. **Scope is per Library space, not global.** Progress is
   project-meaningful, not user-meaningful. `workspaces/{user}/library/spaces/{slug}/.progress.md`.
   Cross-space user facts stay in `user_notes.md`.
2. **Hard token cap: ~1500 tokens per file** (≈ 6k chars). Enforced
   every time the file is written. If a write would exceed the cap,
   the writer compacts first.
3. **Three-layer structure inside the file:**
   - `## Archive` — single paragraph summarising everything older than
     the last 10 sessions. Rewritten by the compaction pass.
   - `## Recent sessions` — up to 10 bullet entries, one per session:
     `YYYY-MM-DD · <one-line outcome> · <commit sha if any>`.
   - `## Open threads` — short list of known-incomplete items the next
     session should look at first.
4. **Compaction trigger.** When `Recent sessions` exceeds 10 entries
   *or* the file exceeds the token cap, the oldest entries are folded
   into `Archive` via a cheap LLM summarisation call (one paragraph,
   HAIKU tier). The full per-session detail is never re-loaded — it
   lives in git history and in per-session files under
   `.progress/YYYY-MM-DD-{sha}.md` which are **not** auto-loaded.
5. **Auto-inject discipline.** Only `.progress.md` itself is auto-loaded
   into the orchestrator system prompt at the start of a session — the
   per-session detail files are accessible via a `progress_read(date)`
   tool only when the agent explicitly asks. This mirrors the
   progressive-disclosure pattern from the OpenAI harness post.
6. **Write discipline.** A single `progress_append(outcome, open_threads)`
   tool, callable at most once per turn (policy-enforced), so the agent
   can't spam the log mid-turn. Prompt instruction: append only at turn
   end, one line per turn.

This gives us the reproducibility benefit of claude-progress.txt
without the unbounded-growth failure mode. The worst case is
"summary slightly lossy" — never "file grows until it swallows
context."

**Target files:** `prax/services/workspace_service.py`,
`prax/agent/workspace_tools.py`, `prax/agent/context_manager.py`
(auto-inject hook).
**Effort:** ~1 day including compaction tool.
**Risk:** low. Worst case we find we need per-user progress too;
additive, not corrective.

### A9. Short `AGENTS.md` entry-point map
**Gap #9.** 100-line map at repo root (or alongside `CLAUDE.md`)
whose only job is pointing into the existing `docs/` tree. Pure
progressive-disclosure. `CLAUDE.md` today is close but is framed as
instructions rather than as a map.

**Target files:** new `AGENTS.md`. Optionally cross-link from
`CLAUDE.md`.
**Effort:** one afternoon.
**Risk:** nil.

---

## Tier B — structural investment

### B2. Stateful file viewer with line numbers
**Gap #2.** `workspace_read` today supports line limits but not
windowed offsets with prepended line numbers. SWE-agent ablated
30-line, 100-line, and full-file viewers and landed on 100 as the
Goldilocks number ([SWE-agent §3.2](https://arxiv.org/abs/2405.15793)).
Implement `workspace_view(path, start_line, window=100)` that returns
line-numbered output and maintains last-viewed position per file.

**Target files:** `prax/agent/workspace_tools.py`.
**Effort:** 1-2 days including the statefulness.
**Risk:** low.

### B8. Real-browser E2E verification from the agent loop
**Gap #8.** CDP plumbing exists in `prax/agent/cdp_tools.py` but
there's no structured E2E verification flow — the "click through the
golden path and assert visible state" tool. Anthropic's post credits
Puppeteer MCP with catching a whole class of bugs invisible from code
alone.

**Shape:** a `browser_verify(flow_spec)` tool that drives CDP through
a declarative sequence (navigate → click → type → assert) and returns
structured pass/fail per step. Consumed by both the codegen spoke and
the content spoke's post-publish verification.

**Target files:** `prax/agent/cdp_tools.py`, new `browser_verify` tool.
**Effort:** ~2 days.
**Risk:** medium — flaky-test territory; needs retry discipline.

### B10. Observability-as-tools
**Gap #10.** We run a full LGTM stack (Loki/Grafana/Tempo/Mimir per
`docs/infrastructure/observability.md`) but no LogQL/PromQL/TraceQL
query is callable as an agent tool. This is the single biggest piece
of "application legibility" missing — when Prax crashes or a
self-improve PR misbehaves, the agent can't self-diagnose against the
running system.

**Shape:** `obs_query_logs(logql)`, `obs_query_metrics(promql)`,
`obs_query_traces(traceql)` — thin authenticated wrappers around the
Grafana HTTP API, results capped and paginated (mirroring SWE-agent
search).

**Target files:** new `prax/agent/obs_tools.py`,
`prax/services/obs_service.py`.
**Effort:** 2-3 days.
**Risk:** medium — authentication + rate-limiting to sort out; paid
back every time a self-improve run fails and can now debug itself.

### B11. Custom architectural linters
**Gap #11.** Enforce the hub-and-spoke layering mechanically:
blueprints → services → agent → plugins/spokes, no reverse imports.
AST-based custom ruff rules (or a separate `scripts/check_layers.py`
wired into `make ci`). Stops drift that is otherwise invisible until
a plugin accidentally imports a Flask blueprint.

**Target files:** new `scripts/check_layers.py`, `Makefile`,
`pyproject.toml`.
**Effort:** 1-2 days.
**Risk:** low. Expect to find a few existing violations and grandfather
them.

---

## Tier C — strategic decisions needed before building

### C3/C4/C6/C7/C12 — "codegen harness mode"
**Gaps #3, #4, #6, #7, #12.** Two-agent architecture (initializer +
coding), `feature_list.json` ground truth, `init.sh` + startup ritual,
mandatory end-of-session clean-state commit, golden-principles cleanup
bots.

These map cleanly onto **long-horizon project builds** — which in Prax
means the **codegen spoke** and the self-improve flow. They do **not**
fit the conversational orchestrator wholesale (most Prax turns are
not "build a feature across many sessions"; they're "answer a
question" or "run a multi-step task in one turn").

**Proposal:** treat this as a bundled "codegen harness mode" design
note rather than five separate tickets. Decide: does the codegen
spoke get its own Anthropic-style two-agent harness with all five
artefacts, or do we pick the subset that applies (e.g. progress file
+ `feature_list.json` yes; two-agent split no)? This is an
architecture conversation first, a coding task second.

**Pre-work:** a `docs/plans/codegen-harness-mode.md` design note that
answers the above before any code lands.

### C13. Automerge culture / enabling `self_improve_submit`
**Gap #13.** OpenAI's post argues that when agent throughput exceeds
human review capacity, blocking merge gates become the bottleneck. But
we intentionally disabled `self_improve_submit` ("git push disabled").
This is a policy decision: under what conditions are we willing to let
Prax push to shared branches without a human gate?

**Pre-work:** explicit trust-tier policy — e.g. automerge only from
the dev sandbox, only for PRs touching specific paths, only after N
passing CI runs + Nth independent diverse-model review. Then we
re-enable `self_improve_submit` with those guardrails. Not a pure
engineering task.

### C14. Spec-tools layer (Chorus-style task-DAG proposals)
**Gap #14.** Already flagged in
[agentic-todo-flows](../research/agentic-todo-flows.md) (§20-§27) as
the missing L1 graduation. A layer that runs between user intent and
tool execution, proposes a typed task DAG, and holds a verification
gate before execution begins. Inverts the "humans write precise specs"
assumption (which empirically fails) in favour of "AI proposes,
humans approve."

**Pre-work:** its own design note. Load-bearing question: does this
live in the orchestrator, or as a new spoke? Both are defensible.

### C15. Task-runner — issue tracker → workspace → PR
**Gap #15.** Probably later. Depends on whether we want external work
(GitHub Issues, Linear, TeamWork tasks) ingested automatically. Worth
it eventually; not urgent.

---

## Execution order recommendation

If we're picking one Tier A to start with: **A1 (edit-with-linter)**
is the cleanest — smallest surface, highest cited leverage, zero
architectural implications. A5 (bounded progress file) is the one
that pays dividends across the most future sessions, but it has more
design questions to sign off on (per-space scoping, compaction
policy).

If we're picking a Tier B to sequence second: **B10
(observability-as-tools)** has the broadest downstream effect —
unlocks self-diagnosis for every future codegen/self-improve run and
every future B8-style browser-verify flakiness investigation.

Tier C items should each get their own design note before any code
lands.
