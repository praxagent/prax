# Ideas Backlog — Things To Consider

A living list of engineering ideas we want to evaluate for Prax but haven't committed to. Each entry captures *why it matters*, *where it maps in the current codebase*, and *rough effort*. Nothing here is scheduled — treat it as a menu to pull from when picking the next improvement.

For phase-gated pipeline composition work, see [`PIPELINE_EVOLUTION_TODO.md`](PIPELINE_EVOLUTION_TODO.md).

For research-backed proposals from the agentic to-do flows study, see [`research/prax-changes-from-todo-research.md`](research/prax-changes-from-todo-research.md) — priorities P1-P7 with sizes and batching advice.

---

## Shipped

### Library — Phase 1 (2026-04-08)

Project → Notebook → Note layout with explicit author provenance and the
`prax_may_edit` permission gate so humans and Prax can collaborate on the
same corpus without stepping on each other.  Inspired by Andrej
Karpathy's "Second Brain" three-folder pattern (raw / wiki / outputs).

- Backend: `prax/services/library_service.py`, `prax/agent/library_tools.py`
- Routes: `prax/blueprints/teamwork_routes.py` (`/teamwork/library/*`)
- TeamWork proxy: `../teamwork/src/teamwork/routers/library.py`
- UI: `../teamwork/frontend/src/components/workspace/LibraryPanel.tsx`
- Tests: `tests/test_library_service.py`
- Design doc: [`library.md`](library.md)

### Library — Phase 5 (2026-04-08)

The final phase — Library is now feature-complete against the
original design.

**Hugo publishing refactor** (task #80):
- New `prax/services/hugo_publishing.py` owns the Hugo site
  skeleton, KaTeX + Mermaid + theme CSS templates, `courses_dir`,
  `hugo_site_dir`, `ensure_hugo_site`, `run_hugo`,
  `get_course_site_public_dir`, `find_course_site_public_dir`
- `course_service.py` lost ~250 lines of Hugo code; re-exports the
  public names with `# noqa: F401` so existing callers keep working
- `note_service.publish_notes` and `_generate_hugo_notes` import
  directly from `hugo_publishing` now
- `main_routes.py /courses/<path>` handler imports directly too
- The Hugo shared site still lives at `workspaces/{user}/courses/_site/`
  (unchanged URL scheme) but is now editable in one place

**Scheduled library health checks** (task #81):
- New `library_service.schedule_health_check(user, cron_expr, channel, timezone)`
  creates a recurring schedule via `scheduler_service.create_schedule`
- Default: Mondays at 09:00 over all channels
- The scheduled prompt tells Prax to run `library_health_check` and
  summarize findings in <200 words (full report still auto-saves to
  `library/outputs/`)
- Agent tool: `library_schedule_health_check(cron_expr, channel)`
- HTTP route: `POST /library/health-check/schedule`
- UI: **Schedule** button in Health Check panel header opens an
  inline form with preset cron options and a channel dropdown

**Tag normalization** (task #82):
- New `_normalize_tag` / `_normalize_tags` helpers
- Strips leading `#`, lowercases, trims whitespace, collapses
  `//` → `/`, drops empty segments, dedupes lists
- Applied on `create_note`, `update_note`, `_tag_path`,
  `list_tag_tree`, `list_notes_by_tag_prefix`
- Queries work case-insensitively — `"#Math"` and `"math"` match
  the same notes

**Draggable graph nodes** (task #83):
- Mousedown on a node starts drag; mousemove moves the node freely
  (scaled by zoom); mouseup pins it
- Click without drag still opens the note (3px movement threshold
  distinguishes click from drag)
- Double-click a pinned node to unpin
- "Unpin all" button appears in top-right when any node is pinned
- Pinned nodes render with an amber ring + small amber dot for
  visual distinction
- Physics sim skips velocity integration for pinned nodes while
  still letting other nodes respond to them

**Tests**: 13 new Phase 5 tests covering tag normalization (every
edge case), scheduled health check (defaults + custom params, via
mocked scheduler), and the Hugo refactor (back-compat re-exports
still work, canonical homes are in `hugo_publishing`, `note_service`
imports directly).

**What was intentionally cut**: wikilink alias rendering in edit
mode.  Inline markdown rendering inside a textarea requires either a
heavy editor library (CodeMirror / Monaco) or a contenteditable
rewrite, neither of which is worth the investment for the value.
The rendered view already handles aliases correctly; edit mode just
shows the raw `[[slug|alias]]` syntax.

**Verification**:
- 345 Prax tests passing (279 at end of Phase 4, +66 across
  course/note/phase3/phase5)
- Ruff clean on all 13 changed Prax files
- TeamWork backend + frontend clean
- Live check: 30 library tools registered in the knowledge spoke
  (up from 29), `library_schedule_health_check` visible,
  `hugo_publishing` module loads

The Library is done.

---

### Library — Phase 4 (2026-04-08)

The long-tail items from the Phase 3 "deferred" list all shipped in
the same session:

**Course generation into the Library** (task #73):
- New `library_service.create_learning_project(subject, title, modules, ...)`
  helper that builds a project with `kind="learning"` + a sequenced
  notebook + one ordered lesson note per module
- New agent tool `library_create_learning_project` exposed in both
  the knowledge and course spokes
- Course spoke system prompt rewritten to prefer the new path for
  any new course, with legacy `course_*` tools kept for back-compat
  with existing `workspace/courses/` data
- 5 new tests covering module creation, empty-modules, custom
  notebook name, first-lesson auto-current, topic rendering
- The old `course_service.py` stays untouched because
  `note_service.publish_notes()` still uses its Hugo site for SMS/
  Discord note delivery (entangled by design; moving the Hugo
  infrastructure is a separate cleanup)

**Proactive engagement on `prax_may_edit` toggle** (task #75):
- `set_prax_may_edit` now enqueues a pending engagement when a
  human-authored note flips from locked → unlocked
- `pending_engagements.yaml` per-user queue at
  `library/.pending_engagements.yaml`
- `workspace_service.get_workspace_context` drains the queue at the
  start of every turn and injects the unlocked notes into the system
  prompt so Prax proactively offers to refine/expand them without
  being asked
- Locking a note back removes any pending engagement
- Duplicates deduped on (project, notebook, slug)
- 5 new tests covering queue / drain / dedup / locked-to-locked no-op

**"Ask Prax to refine" via the chat agent** (task #74):
- New endpoint `POST /teamwork/library/notes/{p}/{n}/{slug}/refine-via-agent`
- Routes through `conversation_service.reply` with the note body +
  instructions inlined into the prompt, so Prax can use any tool
  (web search, arxiv, knowledge graph, library_*) during refinement
- UI refine modal now has two buttons: **Quick refine** (cheap LLM,
  diff preview) and **Full agent** (chat-agent path with tools, no
  preview — the agent saves directly)
- Auto-unlocks the note via `set_prax_may_edit(editable=True)` before
  invoking the agent (human's click is explicit consent)

**Raw capture from external channels** (task #76):
- `sms_service._maybe_auto_capture_raw` detects URLs in inbound SMS
  messages (excluding PDFs, which have their own flow) and saves them
  to `library/raw/` via `library_service.raw_capture`
- Discord path wired the same way via `discord_service`
- The agent prompt is annotated with the captured slug so Prax knows
  to offer a "promote to notebook?" follow-up
- 3 new tests covering URL-only capture, text-only messages, and PDF
  skip behavior

**Force-directed graph view with zoom and filters** (task #77):
- Replaced the static circular SVG layout in `LibraryGraphView` with
  a custom physics simulation (Coulomb repulsion + spring attraction
  along wikilinks + center gravity + friction, ~400 iterations max)
- Pan via canvas drag, zoom via mouse wheel, reset button
- Filter by project (dropdown), filter by author (All / Human / Prax)
- Node labels are pointer-events-none so they don't block clicks
- No new dependencies — pure JS running in React state via
  `requestAnimationFrame`

**Nested tags** (task #78):
- Tags like `math/algebra/linear` are already supported at the
  storage layer (tags are freeform strings)
- New helpers `list_tag_tree(user)` returns a nested dict with counts
  per segment, and `list_notes_by_tag_prefix(user, prefix)` returns
  every note matching a prefix or any descendant
- New routes `GET /library/tags` and `GET /library/notes/by-tag?prefix=...`
  plus TeamWork proxy mirrors
- 3 new tests covering tree nesting, prefix filtering, empty-prefix
  returns all

**Test + lint gate**: 279 Prax tests passing (22 new since Phase 3),
ruff clean on every changed file in both repos, TypeScript clean on
the frontend.

---

### Library — Phase 3 (2026-04-08)

Universal projects + Kanban tasks + sequenced notebooks + reminder
integration.  The Library is now the single surface for any
goal-directed effort: life areas, courses, features, creative
projects, business ops.

**Service layer:**
- `library_service.update_project` / `get_project` — full metadata
  CRUD with `status` / `kind` / `target_date` / `pinned` /
  `tasks_enabled` / `reminder_channel`
- Notebook sequenced mode: `sequenced` + `current_slug`, auto-backfill
  on toggle
- Notes: `lesson_order` + `status` (todo/done) in frontmatter,
  auto-sorted in sequenced notebooks
- `reorder_notes`, `set_note_status` (advances `current_slug` on done)
- **New**: `prax/services/library_tasks.py` — per-project Kanban board
  in `.tasks.yaml` with columns + tasks + append-only activity log +
  comments + reminder integration via `scheduler_service`

**Agent tools** (added to knowledge spoke; total now 28):
- `library_project_update`
- `library_notebook_sequence`, `library_notebook_reorder`,
  `library_note_mark`
- `library_task_add` / `list` / `move` / `update` / `delete` /
  `comment`
- `library_column_add` / `rename` / `remove`

**HTTP routes**: ~17 new routes mirrored through the TeamWork proxy +
React Query hooks.

**UI:**
- **New**: `HomeDashboard.tsx` — grid of active projects with status
  pills, kind tags, progress bars, last activity, pinned-first sort
- **New**: `LibraryProjectView.tsx` — project metadata editor +
  Kanban board with drag-between-columns + expandable task side panel
  showing description / assignees / due date / reminder toggle /
  checklist / activity log / comments
- **New**: `NotebookView` inside LibraryPanel — sequenced-notebook
  rendering with progress bar + ordered lessons + drag-reorder + "set
  current lesson" + "Next lesson" button
- Click project name in sidebar → opens project view (chevron still
  toggles expand/collapse independently)
- Click notebook name → opens notebook view
- Removed: `ContentPanel.tsx`, "Notes & Courses" rail icon, teamwork
  `content.py` proxy router, all `/teamwork/content/*` Flask routes,
  `ContentNote` / `ContentCourse` / `ContentNews` types
- Removed: news briefing persistence entirely (`list_news`,
  `create_news_briefing`, `publish_news`, `_generate_hugo_news`, Hugo
  news templates).  News plugin now writes briefings to
  `library/outputs/` via `library_service.write_output`.

**Reminder integration** (new):
- Task with `due_date` + `reminder_enabled: true` auto-schedules a
  one-time reminder via `scheduler_service.create_reminder`
- `reminder_id` stored on task for lifecycle management
- Update due_date / reminder_channel → reminder reschedules
- Move to `done` / delete task → reminder cancels
- Channel resolution: per-task override → project default → "all"

**Tests**: `tests/test_library_phase3.py` — 36 new tests covering
project metadata CRUD, sequenced notebooks (backfill, reorder, mark
status, current auto-advance), Kanban (create/move/update/delete,
activity log, comments, assignees), columns (CRUD, duplicate
rejection, empty-check before delete), reminder integration (mocked
scheduler — create/cancel/reschedule on due date changes and status
moves, channel override), and a smoke test verifying news functions
and content routes are gone.

**What's deferred to Phase 4**:
- Course generation workflow refactor (`course_service.py` still
  writes to the old `workspace/courses/` layout).  New courses
  generated by the agent work as-is; future PR rewires this so that
  a generated course becomes a Library project with a sequenced
  notebook automatically.
- "Ask Prax to refine" wired through the chat agent
- Proactive engagement when `prax_may_edit` toggles
- Raw capture from external channels (iOS / Discord share → raw/)
- Force-directed graph layout with zoom and filters

Design doc: [`library.md`](library.md) — updated with full Phase 3 reference.

### Library — Phase 2 (2026-04-08)

Everything that was marked "not in Phase 1" shipped in the same session:

**Service layer** (`prax/services/library_service.py`):
- `extract_wikilinks(body)` — pulls `[[slug]]`, `[[notebook/slug]]`,
  `[[project/notebook/slug]]`, and aliased `[[slug|display]]` forms
- Wikilinks auto-stored in note frontmatter on create/update
- `get_backlinks(user, project, notebook, slug)` — reverse lookup
- `find_dead_wikilinks(user)` — static unresolved-link scan
- `rebuild_index(user)` — auto-regenerates `INDEX.md` after every
  project/notebook/note write, with author-badge emojis
- `read_index(user)` — returns current `INDEX.md`
- `read_schema(user)` / `write_schema(user, content)` — `LIBRARY.md` I/O
- `raw_capture(user, title, content, source_url?)` — junk-drawer write
- `list_raw(user)`, `get_raw(user, slug)`, `delete_raw(user, slug)`
- `promote_raw(user, raw_slug, project, notebook, new_title?)` —
  promote a raw item to a real note with `promoted_from` provenance
- `write_output(user, title, content, kind)` / `list_outputs(user)` /
  `get_output(user, slug)` — generated briefs/reports/answers
- `refine_note(user, p, n, slug, instructions)` — low-tier LLM
  refinement with before/after preview (does not apply)
- `apply_refine(user, p, n, slug, new_content)` — human-approved apply
  with `override_permission=true`
- `run_health_check(user)` — Karpathy's monthly audit: static checks
  (dead wikilinks / empty notebooks / orphans / short notes) + LLM
  analysis (contradictions / unsourced claims / gap topics), writes a
  full report to `outputs/health-check-{date}.md`

**Agent tools** (`prax/agent/library_tools.py`):
- `library_raw_capture`, `library_raw_list`, `library_raw_promote`
- `library_outputs_write`, `library_outputs_list`
- `library_health_check`

**HTTP routes** (`prax/blueprints/teamwork_routes.py`):
- `GET/PUT /library/schema`, `GET /library/index`
- `GET /library/notes/{p}/{n}/{slug}/backlinks`
- `POST /library/notes/{p}/{n}/{slug}/refine`
- `POST /library/notes/{p}/{n}/{slug}/apply-refine`
- Raw: `GET/POST /library/raw`, `GET/DELETE /library/raw/{slug}`,
  `POST /library/raw/{slug}/promote`
- Outputs: `GET /library/outputs`, `GET /library/outputs/{slug}`
- `POST /library/health-check`

**TeamWork proxy** (`../teamwork/src/teamwork/routers/library.py`):
- All new routes proxied; matching hooks added to
  `../teamwork/frontend/src/hooks/useApi.ts`

**UI** (`../teamwork/frontend/src/components/workspace/LibraryPanel.tsx`):
- Wikilinks rendered as clickable indigo pills in note bodies
- Backlinks panel below every note
- HTML5 drag-and-drop: drag notes between notebooks; drop targets
  highlight; dropdown kept as keyboard fallback
- LIBRARY.md schema editor
- INDEX.md viewer
- Raw browser with Promote flow (project + notebook picker)
- Outputs browser
- SVG graph view (per-project circular layout, click-to-open)
- Health check runner with clickable findings
- Refine flow: instructions modal → diff preview → Apply/Cancel

**Tests**: `tests/test_library_service.py` — 62 total (31 Phase 1 + 31
Phase 2 covering wikilinks extraction, backlinks, dead links, INDEX
regeneration, schema I/O, raw CRUD + promote, outputs CRUD, LLM refine
with mocked LLM, health check with mocked LLM).

Phase 3 still open (refine wired through the chat agent for tool-using
refinements; scheduled health checks; proactive engagement on
`prax_may_edit` toggle).  Phase 4 still open (raw capture from external
channels; force-directed graph; nested tags).

---

## Sources (2026-04-08)

Distilled from reading these customer stories + cookbook recipes against the bugs we fought on 2026-04-07/08 (duplicate SMS reminders, hallucinated daily briefing, `build_llm(config_key=)` TypeError cascade, `delegate_parallel` schema mismatch, Phase 0 over-routing to `delegate_memory`):

- **Sentry** — https://claude.com/customers/sentry (multimodal grounding, handoff architecture, managed sandbox delegation)
- **Rakuten** — https://claude.com/customers/rakuten (sustained autonomous execution, parallel-with-selective-oversight, test-first code generation, 24-way task decomposition)
- **Vibecode** — https://claude.com/customers/vibecode (multi-file coherence as selection criterion)
- **Anthropic Cookbook** — https://platform.claude.com/cookbook/ (full recipe index)

---

## Top 5 — direct answers to bugs we just hit

### 1. LLM-based evaluator-optimizer for scheduled tasks

- **Cookbook**: `patterns-agents-evaluator-optimizer`
- **Why it matters**: The regex auditor at `prax/agent/claim_audit.py` can only pattern-match hallucinations ("top news", "$83", "42%"). It missed yesterday's fabricated briefing on 2026-04-07 because the language didn't happen to trip a regex. A cheap second LLM given `(tool_results, draft_response)` can score groundedness semantically and either approve, request a retry, or block. For scheduled tasks the blast radius is high (user is absent, SMS already sent) so the few-cents-per-call cost is trivial.
- **Prax mapping**: `prax/agent/claim_audit.py` already has `audit_narrative_grounding()` as the hook point. Add `audit_with_llm()` gated behind the `scheduled=True` flag that the orchestrator already passes. Use `build_llm(config_key="briefing_evaluator", default_tier="low")` — now that `config_key` routing is fixed.
- **Effort**: ~2 hours
- **Status**: not started

### 2. Background context compaction

- **Cookbook**: `tool-use-automatic-context-compaction`, `misc-session-memory-compaction`
- **Why it matters**: Yesterday's hallucinated briefing started because synchronous compaction crashed with the `build_llm(config_key=…)` TypeError, fell back to truncation, and the model lost history mid-turn. We patched the signature bug, but compaction is still blocking — a turn that hits the 50k-token threshold stalls waiting for the summary LLM to return. The cookbook pattern runs compaction on a background thread and serves the previous compacted view until the new one is ready.
- **Prax mapping**: `prax/agent/context_manager.py:270`. Refactor `compact_history()` to return immediately with the last-known-good compaction while a `ThreadPoolExecutor` task builds the fresh one, then swap atomically.
- **Effort**: ~2 hours
- **Status**: signature bug fixed 2026-04-08; background refactor still pending

### 3. Tool search with embeddings → fix spoke over-routing

- **Cookbook**: `tool-use-tool-search-with-embeddings`
- **Why it matters**: The Phase 0 coverage harness found that the medium-tier orchestrator (`gpt-5.4-mini`) kept routing everything to `delegate_memory` because at 54k+ system-prompt tokens and 97+ tools, the model keyword-matches instead of reading the boundary rules. We worked around it by removing `delegate_memory` from the orchestrator's tool list entirely. The root fix is semantic tool retrieval — embed each tool's description, embed the user turn, only inject the top-k relevant tools into the prompt.
- **Prax mapping**: Directly addresses `docs/PIPELINE_EVOLUTION_TODO.md` Phase 0 findings. Would replace the current static tool list built in `prax/agent/tools.py` / `prax/agent/spokes/__init__.py` with a `select_tools_for_turn(user_input)` function invoked at the start of each orchestrator turn.
- **Effort**: ~4-6 hours (Qdrant is already running, embedding pipeline exists)
- **Status**: not started

### 4. Programmatic Tool Calling (PTC) → kills the `delegate_parallel` shape bug

- **Cookbook**: `tool-use-programmatic-tool-calling-ptc`
- **Why it matters**: The model repeatedly mis-shapes `delegate_parallel` arguments (e.g., wrapping items in OpenAI-internal `{recipient_name, parameters}` envelopes, or missing the outer `tasks` field entirely — see yesterday's trace). Our fix was `_normalize_task_spec()` unwrapping the common bad shapes, which is a band-aid. PTC has the model write *Python code* that calls tools directly; the Python type system enforces argument shape and there's nothing to misremember.
- **Prax mapping**: `prax/agent/subagent.py`. `delegate_parallel` could be exposed as a Python code interpreter tool with pre-imported `delegate_browser`, `delegate_research`, etc. functions the model calls normally.
- **Effort**: ~1 day
- **Status**: band-aid shipped 2026-04-08 (`_normalize_task_spec`); structural fix pending

### 5. Prompt caching on the system prompt

- **Cookbook**: `misc-prompt-caching`, `misc-speculative-prompt-caching`
- **Why it matters**: Our system prompt is ~54k tokens (logged at startup; noted in the Phase 0 writeup). We re-send it on every turn. Anthropic's prompt caching gives a 90% discount on cache hits, which cuts per-turn cost dramatically and drops time-to-first-token meaningfully. **Speculative caching is especially relevant for scheduled tasks** — warm the cache at 08:55 so the 09:00 briefing fires near-instantly against a hot cache.
- **Prax mapping**: `prax/plugins/prompts/system_prompt.md` is mostly stable between turns. Mark it as a cache breakpoint when constructing the LangChain message in `prax/agent/llm_factory.py` (Anthropic provider) or equivalent OpenAI request. Add a pre-warming hook in `prax/services/scheduler_service.py:_on_fire` that fires ~5 minutes before scheduled execution.
- **Effort**: ~1-2 hours
- **Status**: not started

---

## Layer 2 — reliability lessons from case studies

### 6. Multimodal grounding floor (Sentry pattern)

- **Why it matters**: Sentry grounds every root-cause analysis in stack traces + profiling + logs + spans + metrics — 5+ independent signals per RCA. Yesterday's weak briefing had exactly 2 signals (`user_notes_read` + `get_current_datetime`) and no external fetch. Principle: **for any scheduled task the orchestrator should enforce a minimum-evidence floor before allowing assertive output.** "If this is a news/weather/market briefing, ≥1 research-family tool MUST have a non-empty result, or the response is suppressed."
- **Prax mapping**: Extends the `audit_narrative_grounding()` check in `prax/agent/claim_audit.py` with a per-intent minimum-evidence table. Wire it into `_audit_claims()` alongside the existing blocking path.
- **Effort**: ~2 hours
- **Status**: partially done (the 2026-04-08 narrative auditor enforces ≥1 research/browser tool call for news/weather claims; extending to per-intent thresholds is pending)

### 7. Parallel-with-selective-oversight (Rakuten pattern)

- **Why it matters**: Rakuten runs 5 tasks in parallel but keeps human oversight on the single highest-risk one. Our `delegate_parallel` treats all branches equally — a cheap research fetch and a destructive sysadmin change get the same attention. The fix: each task gets a risk tier and risky branches auto-upgrade to the higher-tier model or require an audit stage; cheap branches run as-is.
- **Prax mapping**: `prax/agent/action_policy.py` already classifies tools by risk. Propagate that into `prax/agent/subagent.py:_run_spoke_or_subagent()` so the parallel executor reads each branch's declared spoke/category, looks up the implied risk tier, and routes accordingly.
- **Effort**: ~3-4 hours
- **Status**: not started

### 8. Test-first code generation (Rakuten pattern)

- **Why it matters**: Rakuten's engineers report that Claude "generates comprehensive tests instantly, then builds features that pass them" and hit 99.9% numerical accuracy on an ML refactor. Prax's content/codegen spokes currently generate implementation first. Flipping the order catches "looks-right-but-broken" patterns that pure review can't, and fits the existing reviewer-loop we already have in `prax/services/note_quality.py`.
- **Prax mapping**: `prax/agent/spokes/content/agent.py` (codegen entry point) and any plugin_fix_agent flows. Add a "tests first" system-prompt variant and wire the existing review loop to validate against the generated tests.
- **Effort**: ~1 day
- **Status**: not started

### 9. Durable scheduled-task execution (Rakuten pattern)

- **Why it matters**: Rakuten sustained 7 hours of autonomous execution on a single task. Our scheduler is fire-and-forget — `_on_reminder_fire` delivers and auto-deletes the YAML entry before checking that the agent response was actually grounded. If the process crashes mid-turn the reminder is gone. We need a "completed" flag separate from "fired" so interrupted runs resume and the post-send audit can retroactively block a bad delivery.
- **Prax mapping**: `prax/services/scheduler_service.py` — split the reminder lifecycle into `pending → firing → delivered → acknowledged`. The auto-delete only happens on `acknowledged`. Requires schema change to `schedules.yaml` (add `last_state` field) and a recovery pass in `_load_all_users()`.
- **Effort**: ~1 day
- **Status**: not started

---

## Layer 3 — medium-term infrastructure wins

### 10. Server-side prompt versioning and rollback

- **Cookbook**: `managed-agents-cma-prompt-versioning-and-rollback`
- **Why it matters**: On 2026-04-07 we spent an entire session iterating on spoke descriptions in the Phase 0 fix round. Without versioning and rollback, any regression (e.g., a new description causing sysadmin to stop matching) is invisible until the next harness run. The cookbook pattern ties prompts to versions, runs the harness on every version change, and auto-reverts on regression.
- **Prax mapping**: `prax/plugins/prompts/` gets a `.version` file or git-sha marker. Add a `scripts/verify_prompt_version.py` that runs the coverage harness against the current + previous version and fails CI if the fallback rate regresses.
- **Effort**: ~4-6 hours
- **Status**: not started

### 11. Real eval framework (not just the coverage harness)

- **Cookbook**: `misc-building-evals`, `tool-evaluation-tool-evaluation`
- **Why it matters**: `scripts/run_coverage_harness.py` is a one-off script that measures fallback rate. A proper eval framework runs deterministic goldens against (prompt, tool, model) combinations, tracks regressions across runs, and gates deploys. It's the long-form answer to "how do we know briefing quality actually improved after a change?"
- **Prax mapping**: New `prax/eval/` module (there's already a stub — `prax/eval/runner.py` exists) expanded into a full eval runner with a goldens directory, per-eval config, regression tracking, and a report generator that feeds back into the Phase 0 docs.
- **Effort**: ~2-3 days
- **Status**: stub exists; full framework pending

### 12. Citations API for grounded output

- **Cookbook**: `misc-using-citations`
- **Why it matters**: Anthropic's Citations API surfaces exact spans from source documents that back each claim. For scheduled briefings every factual claim would cite a tool result span — if no citation is possible, the auditor blocks the claim. This is a structurally stronger version of the regex narrative check we shipped on 2026-04-08.
- **Prax mapping**: Where tool results currently flow into the agent as opaque strings, wrap them as citeable sources. Update the scheduled-task system prompt variant to require inline citations. The auditor in `prax/agent/claim_audit.py` cross-references claim spans against cited sources.
- **Effort**: ~1 day (provider-dependent — Citations is Anthropic-native; would need emulation on OpenAI)
- **Status**: not started

---

## Recommended ordering — "what I'd ship this week"

Ranked by impact-per-hour against the specific problem of *"don't SMS the user fabricated news at 9am."*

| # | Item | Why this order | Effort |
|---|---|---|---|
| 1 | **LLM-based scheduled-task auditor** (#1) | Directly replaces the 2026-04-08 regex auditor with something robust. Pays for itself on the first blocked hallucination. | 2h |
| 2 | **Prompt caching on system prompt** (#5) | Pays for itself immediately on cost + latency, no behavior change risk. | 1-2h |
| 3 | **Background compaction** (#2) | Removes the stall that triggered the 2026-04-07 cascade. Low-risk refactor. | 2h |
| 4 | **Tool embeddings for spoke routing** (#3) | Structurally fixes the Phase 0 over-routing findings that we only worked around. | 4-6h |
| 5 | **PTC for parallel delegation** (#4) | Structural fix replacing the `_normalize_task_spec` band-aid. | 1 day |

Everything in Layers 2–3 is valuable but less urgent than the top 5. Revisit after the scheduled-task hallucination class is closed out.

---

## Process notes

- When picking from this list, move the chosen item into an active plan or ticket — don't leave "in progress" state on this document.
- When an item ships, strike it through but keep it here for one release cycle so the rationale is still visible in diffs.
- When a new customer story or cookbook recipe suggests an idea, add it here with date + source link rather than silently starting work.
