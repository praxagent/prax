# Prax Changes From Agentic To-Do Research

[← Research](README.md) · [← Agentic To-Do Flows](agentic-todo-flows.md)

This is a proposals document, not a commitment. It maps each finding
from [Agentic To-Do Flows](agentic-todo-flows.md) against what Prax
actually does today, and proposes concrete changes in priority order.
Each proposal is sized (S / M / L) so we can pick off high-value wins
without committing to the whole list.

Prax currently has two to-do mechanisms:

1. **Agent plan** (`prax/agent/workspace_tools.py:agent_plan`) — a
   linear ordered list of steps that the orchestrator writes to
   `agent_plan.yaml` at the start of a multi-step turn, updates via
   `agent_step_done`, and injects back into the system prompt every
   turn via `workspace_service.get_workspace_context()`. This is
   Prax's internal to-do list for his own work. ReAct-style.
2. **Library Kanban** (`prax/services/library_tasks.py`) — the new
   per-project human-visible task board with columns, activity log,
   assignees, due dates, and scheduler-wired reminders. This is the
   *user's* to-do list, which Prax can collaborate on.

Both systems are affected by the research.

## Current state vs. research findings

| Finding | Prax today | Gap |
|---|---|---|
| **§20 — Sequential subtasking beats one-shot** | Prax has `agent_plan` which enforces stepwise execution via `agent_step_done` gating. ReAct-style. ✓ | None — this is already the shape Prax uses. |
| **§21 — Realism ceiling (WebArena/GAIA/TravelPlanner)** | Prax has a Phase 0 coverage harness but no external benchmark run. The November morning-briefing hallucination shows Prax hits the same ceiling. | Benchmark against GAIA. Accept that Prax will initially score low and use the gap to drive Pareto-style prioritization (see §21 in the research doc + [`benchmarking.md`](benchmarking.md)). |
| **§22 — Plans mislead users** | P8 ✅ shipped read-only `agent_plan` visibility widget in the chat view. P2 ✅ shipped self-reported confidence dots on the widget and on Library Kanban cards. | Gap closed for now. |
| **§23 — Linear lists lose dependencies** | Both `agent_plan` (ordered list) and Library Kanban (unordered columns) are linear representations. Neither tracks prerequisite relationships. | Medium — add optional `blocked_by` / `blocks` to tasks and plan steps. |
| **§24 — Decomposition/tool-selection/param stages are independently measurable** | Prax has governed tool logs and the pipeline coverage harness. Decomposition quality is not separately instrumented. | Medium — add per-stage metrics to the health telemetry. |
| **§25 — Externalize working memory** | `agent_plan.yaml` is file-backed ✓. `library/.tasks.yaml` is file-backed ✓. P5 ✅ shipped: plans with >6 steps or >800 description chars now render compact in the system prompt, pointing Prax at `agent_plan_status` for the full list. | Gap closed. |
| **§26 — Tool outputs are a to-do-injection attack surface** | P1 ✅ shipped: every task carries a `source` field (`user_request` / `agent_derived` / `tool_output`); `tool_output` requires a justification; tool-output cards render with a warning badge and dedicated side-panel callout; dedicated `library_task_add_from_tool_output` agent tool makes the laundering case explicit. | Alignment-check helper (LLM-verifier variant from original proposal) is explicitly not built — the justification + UI badge were judged sufficient for the current threat model; revisit if we see abuse. |
| **§27 — Benchmarks lie** | Prax uses internal scenarios, no external benchmark yet. | Low — already covered by [`benchmarking.md`](benchmarking.md); just don't trust public leaderboards when we do benchmark. |

## Concrete proposals, priority-ordered

### P1 — Task provenance + alignment checks ✅ shipped (2026-04-08)

**Why:** §26 is the most concrete security gap the research exposes.
Prax's Library Kanban is collaborative, and `library_task_add` is
reachable by any tool Prax invokes. A subtly-prompt-injected tool
output could drop `"Pay the electricity bill to account 12345"` into
a user's Kanban board, and Prax has no mechanism to refuse that.

**What to build:**

- Add a `source` field to every task entry in `.tasks.yaml`:
  - `source: user_request` — explicit request from the current user turn
  - `source: agent_derived` — Prax added it while executing a user request (e.g., "break this into subtasks")
  - `source: tool_output` — a tool suggested this task (highest scrutiny)
- `library_task_add` takes an optional `source` kwarg; defaults to
  `agent_derived` when called from the agent.
- New `_verify_task_alignment(user_id, task, current_turn_context)`
  helper — called before adding a `tool_output` task — that checks
  whether the task title/description can be traced back to something
  the user asked for in the last N turns. Flag and gate if not.
- Activity log already records `actor: prax`; extend with `source: ...`
  so the audit trail shows provenance.
- UI change: task cards with `source: tool_output` get a small warning
  badge; the side panel explains where the task came from.

**What shipped:**

- `library_tasks.create_task()` now accepts `source` (one of
  `user_request` / `agent_derived` / `tool_output`) and
  `source_justification`. `tool_output` requires a non-empty
  justification or the call errors — the justification is the
  minimum audit trail explaining which tool suggested the task and
  why adding it is appropriate.
- Invalid `source` values and whitespace-only justifications are
  both rejected.
- The task `activity` log records the `source` on creation so the
  provenance is visible in the side panel history.
- New dedicated agent tool `library_task_add_from_tool_output` with
  required `source_tool` and `source_justification` params — makes
  the "I'm laundering a tool output" case explicit and trains Prax
  to pause and write the justification.
- `library_task_add` defaults to `source="agent_derived"`; the HTTP
  `POST /teamwork/library/projects/.../tasks` route defaults to
  `source="user_request"` because the UI is the caller.
- TeamWork UI: `TaskCard` renders an amber "From tool output" badge
  and an amber card background when `source="tool_output"`. The
  task side panel shows source + a dedicated warning block with the
  justification text and a "Review before trusting" reminder.
- Added `SourceBadge` component for the side panel metadata row.

**Files touched:**
- `prax/services/library_tasks.py`
- `prax/agent/library_tools.py`
- `prax/blueprints/teamwork_routes.py`
- `../teamwork/frontend/src/hooks/useApi.ts` — added `TaskSource`, `source`, `source_justification` fields
- `../teamwork/frontend/src/components/workspace/LibraryProjectView.tsx`
- `tests/test_library_todo_research_batch.py` (new)

### P2 — Plan uncertainty surfacing ✅ shipped (2026-04-08)

**Why:** §22 shows plausible-looking plans cause miscalibrated trust.
Prax's plans are invisible for internal work and over-confident for
Kanban/Library work. Users have no signal to distinguish "Prax is
confident this plan is right" from "Prax is guessing".

**What to build:**

- When `agent_plan` is created, capture a `confidence` field computed
  from one or more of:
  - Token entropy on the plan generation (already in
    `logprob_analyzer` for some models)
  - Whether the goal matched a known spoke vs fell through to generic
  - Whether the agent had to re-plan mid-turn (prediction_tracker
    records this)
- Surface confidence in the TeamWork UI for the Library Kanban:
  Prax-added tasks show a small dot (green/yellow/red) based on
  confidence.
- For human-facing plans (the Plan-Then-Execute reference pattern),
  include an explicit uncertainty section: "I'm ~70% sure about
  steps 1-3; step 4 depends on what we find." Use low-tier LLM to
  auto-generate from the plan body.

**What shipped:**

- `workspace_service.create_plan()` accepts `confidence` ("low" /
  "medium" / "high", default "medium"); invalid values fall back to
  "medium". The value is persisted in `agent_plan.yaml`.
- The `agent_plan` LangGraph tool exposes `confidence` as an
  optional third parameter with guidance in the docstring: "low"
  when guessing, "high" only for routine well-understood tasks.
- `get_workspace_context()` injects the confidence into the "Active
  Plan" header in both full and compact rendering modes so Prax
  stays aware of his own stated confidence across turns.
- `agent_plan_status` shows confidence in its status output.
- `/teamwork/agent-plan` HTTP route returns `confidence` in the
  response payload.
- `library_tasks.create_task()` and `update_task()` accept
  `confidence`; it's persisted on the task dict alongside `source`.
- TeamWork UI:
  - `AgentPlanCard` renders a small colored dot next to the Cpu
    icon (red=low, amber=medium, emerald=high) with a tooltip, plus
    a footer row in the expanded state explicitly calling it
    "self-reported, not calibrated".
  - Library `TaskCard` renders a 1.5px confidence dot on each card
    with a tooltip; the side panel shows "Confidence: {level}"
    alongside source + author in the metadata row.
- **Explicitly not calibrated** — this is a self-reported hint, not
  a probability. Documented in every docstring and the UI tooltip.

**Files touched:**
- `prax/services/workspace_service.py`
- `prax/agent/workspace_tools.py`
- `prax/services/library_tasks.py`
- `prax/blueprints/teamwork_routes.py`
- `../teamwork/frontend/src/hooks/useApi.ts` — added `Confidence`, `confidence` field on `AgentPlan` + `LibraryTask`
- `../teamwork/frontend/src/components/workspace/AgentPlanCard.tsx`
- `../teamwork/frontend/src/components/workspace/LibraryProjectView.tsx`
- `tests/test_library_todo_research_batch.py`

### P3 — Dependency-aware tasks (Medium, Medium-size)

**Why:** §23 is a measured ~15pp gap between sequence and graph
planning for GPT-4. Prax's Kanban is strictly column-based — if task
B depends on task A, there's no way to express that. In practice
humans route this through "put B in Blocked until A is done, then
manually move to Todo", which is brittle.

**What to build:**

- Add `blocked_by: list[task_id]` and `blocks: list[task_id]`
  (computed) to each task entry.
- When a blocking task moves to `done`, auto-unblock any tasks that
  were blocked only by it (move them from wherever they are to a
  special "Ready" column if all their blockers are done, or just
  clear the block indicator if they're already in Todo).
- When a blocked task is moved to `doing`, show a warning that its
  dependencies aren't satisfied.
- UI: task cards show a small "chain" icon with a count when they
  have unresolved blockers. Side panel lists `Blocked by:` and
  `Blocks:` sections.
- Agent tool `library_task_add` accepts `blocked_by` as a
  comma-separated string.
- Same primitive for `agent_plan` steps: `step.depends_on: [step_ids]`.

**Size:** M (~4 hours)

### P4 — Per-stage tool-use observability (Medium, Medium-size)

**Why:** §24 shows tool-use flows have distinct failure modes at
(a) decomposition, (b) tool selection, (c) parameter filling, and
(d) execution. Prax's current health telemetry lumps everything into
"tool call failed" — we can't tell if Prax picked the wrong tool or
filled the right tool with wrong arguments.

**What to build:**

- Extend `governed_tool.py` to record on every tool invocation:
  - `stage: decomposition | selection | parameterization | execution`
  - `success: bool`
  - `error_class: pydantic_validation | tool_error | timeout | other`
- Add a `tool_stage_report` to the health telemetry service so the
  coverage harness can surface the per-stage breakdown.
- The claim auditor can then distinguish "Prax couldn't pick a
  tool" from "Prax picked the right tool with wrong args" and give
  targeted hints.

**Size:** M (~3 hours) — mostly instrumentation, no new concepts.

### P5 — Plan context cap ✅ shipped (2026-04-08)

**Why:** §25 externalization is already done (files), but the
orchestrator still loads the entire `agent_plan.yaml` into every
turn's system prompt. A 30-step plan with long descriptions can eat
significant context.

**What shipped:**

- `workspace_service.get_workspace_context()` now uses two
  thresholds: `PLAN_STEP_LIMIT=6` and `PLAN_CHAR_LIMIT=800` (total
  description characters). If either is exceeded the plan renders
  in compact mode: goal, current step in full, next 2 upcoming
  steps (trimmed to 80 chars each), and "... and N more step(s)"
  with a "Plan compacted to save context. Use agent_plan_status to
  see every step" notice pointing Prax at the overflow valve.
- Small plans still render fully — no behavior change for the
  common case.
- Library Kanban proactive-engagement cap is NOT included in this
  shipment; revisit if we see context pressure from it.

### P6 — GAIA external benchmark (Medium, Large-size)

**Why:** §21 shows the realism gap is the real ceiling, and Prax has
no external benchmark run. [`benchmarking.md`](benchmarking.md)
already argues for GAIA as the starting point. Time to do it.

**What to build:**

- New `prax/eval/gaia_runner.py` that feeds GAIA questions to the
  orchestrator and captures answers.
- Use the 166-question public dev set (not the held-out 300).
- Publish a per-question report to `docs/research/gaia-run-{date}.md`.
- Set an internal baseline target (e.g., "match GPT-4 + plugins at
  15%") and treat anything above that as an improvement signal.

**Size:** L (~1-2 days) — not strictly a to-do-research change, but
the research makes it urgent.

### P8 — Read-only `agent_plan` visibility widget ✅ shipped (2026-04-08)

**Why:** §22 says exposing the plan to the user raises cognitive load
and can reduce plan quality via miscalibrated trust when users edit
already-correct plans.  The right middle ground is **read-only
visibility** — the user can see what Prax is working on, but can't
interrupt, edit, or rubber-stamp the plan mid-flight.  This also
settled the broader design question "should Prax use the Library
Kanban for his own working memory?" with a firm no.

**What shipped:**

- `GET /teamwork/agent-plan` Flask endpoint reads `agent_plan.yaml`
  via `workspace_service.read_plan` and returns a denormalized view
  (goal, steps, done_count, total, current_step).
- TeamWork proxy router at `../teamwork/src/teamwork/routers/agent_plan.py`
  and `useAgentPlan()` hook in `useApi.ts` that polls every 3s.
- `AgentPlanCard.tsx` — collapsible card that renders between the
  chat channel header and the message list when an `agent_plan` is
  active.  Shows goal, current step, and `done/total` progress; click
  to expand the full step list.  Read-only by design.
- Hides automatically when `agent_plan_clear` runs at end of turn.
- Updated docstrings on `agent_plan` and `library_task_add` tools
  explicitly instructing Prax to keep the two systems separate.
- Updated knowledge + course spoke system prompts with the same
  boundary rule.
- Updated `docs/library.md` with a prominent "Scope — the wall
  between Kanban and `agent_plan`" section at the top.
- Updated `LIBRARY.md` seed so users see the rule from day one.
- Updated `CLAUDE.md` at the repo root for any agent working on Prax.

**Deliberately not built:**

- Any interactive controls (edit, proceed, skip, abort) on the
  widget.  Mid-execution oversight is net-negative per the CHI 2025
  study.  If we ever want oversight, it should live in a separate
  "plan preview before execution" flow (P7), not on this card.
- Full Kanban-style rendering.  The card is intentionally minimal.

### P7 — Plan-Then-Execute oversight UI pattern (Low, Large-size)

**Why:** §22 shows the Plan-Then-Execute pattern *can* improve
outcomes with oversight, but only when the plan is already
high-quality. Prax's current `agent_plan` is invisible to the human
during execution. We could expose it in the chat UI so the human can
intervene mid-plan.

**What to build (later):**

- When Prax creates an `agent_plan` in response to a user request,
  send a "plan preview" card into the chat channel before execution
  starts. Show each step and let the human click Proceed / Skip /
  Edit / Abort.
- Requires teamwork frontend work on a new message type.
- This is low priority because the CHI study shows the oversight
  UI is double-edged — it raises cognitive load and sometimes
  *reduces* plan quality. Worth building only after P1/P2 land
  because P2 gives the signal to decide when to show the preview.

**Size:** L — don't build this until the lower-priority items have
shipped and we have a specific hypothesis about where oversight helps.

### Items intentionally NOT changing

- **Library Kanban permission model** — the research argues for
  alignment checks but the current "both human and Prax freely edit
  tasks" model is right for a *single-user* collaborative scenario.
  The alignment check in P1 is the right safety net without
  locking everything down.
- **Linear `agent_plan`** — P3 adds dependency support but the
  default linear shape is correct for most multi-step tasks.
  Graph-structured plans are only worth it when dependencies exist.
- **Mid-execution interactivity on the agent_plan widget** — P8
  shipped read-only visibility.  Adding edit/proceed/skip controls
  is explicitly not planned because the CHI study says mid-execution
  oversight is net-negative for simple tasks.  P7 (full
  Plan-Then-Execute oversight UI) stays low-priority until there's a
  specific failure case demanding it.
- **Kanban as Prax's working memory** — closed decision, with the
  wall documented in `docs/library.md`, `CLAUDE.md`, and the tool
  docstrings.  The two systems (`agent_plan` for Prax, Library
  Kanban for humans) stay apart.  See below for the one exception
  under discussion.
- **Benchmark cargo-culting** — §27 warns against trusting public
  leaderboards. Stick with the internal coverage harness + GAIA + a
  self-collected post-cutoff scenario set.

### Future possibility (explicitly not scoped)

**Limited Kanban write access for multi-day Prax-driven projects.**
If Prax is ever the primary driver of a genuinely long-horizon
project — e.g., "write a 40-page report over the next two weeks",
"refactor the auth system across the codebase", "compile a
literature review with 100+ citations" — his work would benefit
from being visible alongside the user's project tasks.  In that
scenario the *persistence* and *time scale* of the work would
actually match the Kanban's design, and the user would *want* to
see it.

The proposal, if we ever build it:

- A new per-project flag `prax_may_add_tasks: bool` (default
  `false`)
- When `true`, Prax can call `library_task_add(source="agent_driven")`
  inside that project
- Every such task is tagged with its source so the user can tell
  what Prax added vs what they added themselves
- Prax still uses `agent_plan` for within-turn ephemeral work — the
  Kanban is for work items that genuinely span turns and deserve
  durable tracking
- Requires P1 (task provenance) to ship first so the audit trail
  is meaningful

**Not building this now** because: (a) there's no concrete use case
yet, (b) it requires P1 as a dependency, and (c) the current wall
is working fine with the read-only widget providing visibility.
When a real multi-day Prax-driven project shows up, revisit.

## Recommended batch

If picking a single coherent chunk to ship first:

**Already shipped**:

- **P8** ✅ — read-only `agent_plan` visibility widget + the wall
  between Prax's working memory and the Library Kanban (2026-04-08)
- **P1** ✅ — task provenance (source + justification) + UI warning
  badge + tool-output flow split into its own agent tool (2026-04-08)
- **P2** ✅ — self-reported confidence signal on `agent_plan` and
  Library Kanban tasks, rendered as colored dots in the UI
  (2026-04-08)
- **P5** ✅ — plan context cap: compact rendering for large plans
  in `get_workspace_context` (2026-04-08)

**Defer for later**:

1. **P3** — dependency-aware tasks (useful but not urgent)
2. **P4** — per-stage observability (useful for the next coverage harness round)
3. **P6** — GAIA benchmark (biggest long-term win, biggest effort)
4. **P7** — plan preview UI (only if we see evidence it's needed)

## What this isn't

This doc isn't a plan commitment — it's a research-aligned proposal.
Before shipping any of these:

- P1 needs a threat model check (is auto-capture from SMS URLs
  already covered by the "source_url present" signal?).
- P2 needs confidence signals we can actually measure; if logprobs
  aren't available on the current model tier, the signal is cheap
  but weaker.
- P3 needs a UX review — blocked-by graphs can become UI spaghetti.
- P6 needs a clean evaluation harness and stable prompt caching (so
  re-runs are cheap and reproducible).

**References:**
- [Agentic To-Do Flows](agentic-todo-flows.md) — the underlying research synthesis
- [Planning & Reflexion](planning-reflexion.md) — Prax's existing plan mechanism
- [Benchmarking](benchmarking.md) — external benchmark strategy
- [Library design](../library.md) — Kanban and task data model
