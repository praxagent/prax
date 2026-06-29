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

## Layer 4 — research quality

### 13. STORM-style grounded multi-perspective research for `deep_dive`

- **Source**: Stanford OVAL Lab, **STORM** — *"Assisting in Writing Wikipedia-like
  Articles From Scratch with Large Language Models"*, Shao et al., **NAACL 2024**.
  Paper: https://arxiv.org/abs/2402.14207 · Code (MIT):
  https://github.com/stanford-oval/storm · Live demo: https://storm.genie.stanford.edu
  (STORM = *Synthesis of Topic Outlines through Retrieval and Multi-perspective
  Question Asking*.)
- **Why it matters**: STORM's measured win (≈25% more organized, ≈10% broader
  coverage vs outline-driven baselines, in the FreshWiki human eval) comes from
  **perspective discovery → multi-perspective question-asking _grounded in
  retrieval_ → outline → cited synthesis**. Prax's `note_deep_dive`
  (`prax/agent/spokes/knowledge/deep_dive.py`) already does *research → write →
  multi-pass reviewer/critique* with related-note retrieval, plus grounding via
  `url_reader` (Jina) and `claim_audit`. The one piece STORM has that we don't is
  the **explicit multi-perspective decomposition + contradiction map** before
  synthesis — that's the part worth borrowing.
- **What NOT to do**: the viral "4 copy-paste prompts" version (simulate 5
  experts → contradiction map → synthesis → self-critique) **drops retrieval**.
  Ungrounded "simulate experts" pulls from parametric memory — the exact
  unsourced-confidence failure mode Prax's reliability work exists to prevent.
  Adopting it literally would regress us. Borrow the *structure*, keep our
  grounding. (Reference is the paper, deliberately **not** the tweet.)
- **Prax mapping**: add an optional, flag-gated `multi_perspective` mode to
  `note_deep_dive`: generate N perspectives, ask **retrieval-backed** questions
  per perspective, build a contradiction map resolved against sources, then run
  the existing synthesis + reviewer/critique passes. Gate behind a setting
  (default off) and **measure with the eval harness** — never spike a benchmark
  (`CLAUDE.md`). Composes with #6 (evidence floor) and #12 (citations).
- **Effort**: ~1–2 days for a grounded prototype behind a flag.
- **Status**: not started — **but tracked in evals so it can't be lost**: the
  rubric lives as a golden at
  [`prax/eval/goldens/research_multiperspective.yaml`](../prax/eval/goldens/research_multiperspective.yaml),
  loaded by `prax/eval/goldens.py`, surfaced by `make eval`, and guarded by a
  key-free CI test (`tests/test_eval_goldens.py`) so deleting/forgetting it fails
  CI. Today the golden measures the **baseline gap** (deep_dive has no explicit
  multi-perspective stage); when the mode above ships, the same golden measures
  the improvement. This is the concrete "how do we not lose track of it" answer,
  and a minimal down-payment on #11 (real eval framework → goldens directory).

### 14. Our own schema-constrained document-extraction model (commercial-friendly Lift)

- **Source / context**: [Datalab Lift](research/lift-document-extraction.md) is
  best-in-class but non-commercial (Modified OpenRAIL-M). Full plan:
  [`research/diy-document-extraction-model.md`](research/diy-document-extraction-model.md).
- **Why it matters**: PDF/image + JSON Schema → guaranteed-valid typed JSON is a
  real gap in Prax's read/convert stack. **Key insight: the validity guarantee is
  decode-time (constrained decoding) — free on any permissive base, zero training**;
  training only buys value-accuracy. So a commercial-clean, no-egress extractor
  exists after Phase 0.
- **Prax mapping**: ≈70% scaffolded [verified] — `finetune_service.py`
  orchestration + LoRA hot-swap, `llm_factory.py:222` vLLM provider,
  `vision_tools.py` base64 path, `pdf_service`/`url_reader`, `make sandbox-gpu`,
  `prax/eval/goldens.py` injectable judge/replay seams. Greenfield ≈30%: a deployed
  vLLM with `guided_json`, the schema param threaded through provider+vision_tools,
  a deterministic field/full-doc/validity comparator, a VLM training script +
  `pypdfium2` renderer, and the ML deps.
- **Phases**: P0 constrained-decode MVP on Qwen3(-VL) Apache-2.0 (days) → P1 LoRA
  SFT on synthetic+permissive data for accuracy (weeks) → P2 optional vision VLM +
  GRPO on field-accuracy. License Apache-2.0 throughout (provenance manifest; no
  distilling frontier APIs into product weights).
- **OCR front-end option**: for **hard** docs (scanned/handwritten/table-heavy/
  multi-page) where text-extraction (`url_reader`/`pdf_service`) loses structure,
  [Baidu **Unlimited-OCR**](research/unlimited-ocr.md) (DeepSeek-OCR lineage, **MIT**,
  OpenAI-compatible via SGLang) is a self-hostable **faithful-transcription stage**
  *before* constrained decode: `PDF/image → Unlimited-OCR → faithful text/layout →
  guided_json → typed JSON`. MIT is the unlock Lift lacked. Adopt-candidate;
  validate quality vs. the current read stack before defaulting on.
- **Honest gate**: if you only need valid JSON from clean docs, a frontier API with
  structured outputs already does it — build only for data-residency / unit-cost /
  hard-tail / ship-the-weights.
- **Effort**: P0 days; P1 weeks; total to a credible 8B ≈ 50–200 GPU-hours
  (~$100–600), dominated by data labor.
- **Status**: not started — tracked in evals via
  [`prax/eval/goldens/document_extract.yaml`](../prax/eval/goldens/document_extract.yaml).

### 15. Visual (screenshot) RAG — PixelRAG-style

- **Source**: [`research/pixelrag-visual-rag.md`](research/pixelrag-visual-rag.md)
  (StarTrail-org/PixelRAG, **Apache-2.0**).
- **Why it matters**: Prax retrieval is text-only (`knowledge_search`/Qdrant,
  `url_reader`, Library, Neo4j, `trace_search`) — it can't answer questions about
  tables/charts/layout. Retrieval over **page images** (render→tile→embed with a
  Qwen3-VL embedder→FAISS→reader) recovers that.
- **Prax mapping**: we already own the render half (sandbox CDP/Playwright + browser
  spoke). Adoption = a self-hosted Qwen3-VL embedder + an image-embedding index
  (FAISS or a Qdrant image collection), exposed as a **flag-gated, off-by-default
  visual-RAG mode**. Don't use the hosted `api.pixelrag.ai` for private docs.
- **Adopt-candidate** (Apache-2.0, unlike Lift). Shares the Qwen3-VL backbone with
  #14's Phase 2, so the two efforts compound.
- **Effort**: ~1 week for a self-hosted prototype behind a flag; GPU for the embedder.
- **Status**: not started — would get a visual-retrieval golden when built (per the
  [eval-goldens pattern](#process-notes)).

### 16. Plug-and-play cloud GPU: scoped power on/off + serve-on-demand

- **Source / context**: reference
  [`research/two-qwen3-on-one-spark.md`](research/two-qwen3-on-one-spark.md); full
  design in [`guides/cloud-gpu.md`](guides/cloud-gpu.md).
- **Why it matters**: the local-LLM / fine-tuning / vision rails assume a GPU, but
  most deploys (and the sandbox) often have none. Launch → serve vLLM → use →
  **power OFF** makes the whole `VLLM_BASE_URL` / finetune stack usable anywhere
  and matures fine-tuning — adoptably, without bloating core.
- **Prax mapping (~80% scaffolded)** [verified]: `VLLM_BASE_URL` / `VISION_BASE_URL`
  rails + the `vllm` provider in `llm_factory.py`; `make sandbox-gpu` +
  `docker-compose.gpu.yml` (local-GPU detect/serve); `finetune_service`
  load/unload-adapter; the plugin `permissions.md` capability ceiling
  (`extending.md`); the `prax-sandbox/docs/remote.md` bearer+TLS+single-port
  template. **Greenfield ~20%**: a `gpu_power(on|off|status)` tool/plugin, the
  scoped on/off credential (or power-broker), and an optional auto-start flow.
- **Security gate (firm)**: power on/off **only** — never create/destroy/resize/SSH/
  data. Enforced in TWO layers (provider-scoped cred *or* a power-broker, **plus**
  the plugin `permissions.md` ceiling). Prefer serverless scale-to-zero (Modal)
  where latency allows — removes the credential entirely. Reuse the shipped
  ephemeral-TTL creds + SSRF guard + HIGH-risk confirmation gate. Blast radius of a
  leak = "the GPU flaps on/off" — not a data breach, not unbounded spend.
- **No-hardcode**: ship the **ability** (a GPU endpoint + sandbox code-writing),
  never a specific model (Whisper/image-gen/video/SAM3…). Repeated recipes graduate
  to workspace plugins.
- **Effort**: docs/design done (this entry + cloud-gpu.md); a reference `gpu_power`
  plugin ~½ day; auto-start orchestration ~1–2 days.
- **Status**: design only — no behavior-changing code yet.

### 17. Success-side procedural capture ("skills" done right) + similarity recall

- **Source**: [`research/agent-skills-discipline.md`](research/agent-skills-discipline.md)
  (Anson Biggs, *"You're Probably Using Agent Skills Wrong"*, 2026-06). The article's
  point is the **authoring discipline**, not the `SKILL.md` file shape: an artifact
  should be born from a **gap discovered by actually solving a problem**, never
  generated speculatively for one the model can't solve.
- **Why it matters**: Prax already self-authors *code* after a real need
  (`plugin_write`, `system_prompt.md:81`) and already does progressive disclosure
  (`progress_*`, `trace_search`). But **every learning loop keys on failure**
  (metacognitive ≥3-occurrence patterns, the failure journal, negative feedback) or
  graduates the lesson into a **Python plugin**. There is no **success-side,
  knowledge-not-code** lane: after solving a hard, project-specific task the *hard
  way*, Prax has nowhere structured to crystallize the **generalized procedure** as
  reusable prose — `metacognitive.record_success` only decays a failure score, it
  authors nothing. Second gap: no **recall by task-similarity** (a stored procedure
  isn't surfaced when a similar task recurs).
- **Prax mapping** [verified]: invert the existing gap signal in
  `prax/agent/metacognitive.py` (which already fires post-hoc on real difficulty) to
  the success case; persist a markdown "playbook" (`name` + one-line `description`
  frontmatter + abstracted steps + optional script/reference links) alongside
  `self_tools` (`prax/services/self_tool_registry_service.py:21`) so it round-trips
  via `workspace_push` — **not** a new store; recall via the user-notes token-overlap
  selector (`prax/services/workspace_service.py:626`) or `trace_search` embeddings,
  injected by progressive disclosure (description first, body on demand).
- **Guardrail (from the article)**: author **only after a gap is overcome**, never
  speculatively — abstract this into prompt selectivity (encode the *class*, per
  `CLAUDE.md` "never spike benchmarks", not the example). Lowest-risk slice; can ship
  as a one-line prompt rule independently of the capture mechanism.
- **Open question (gates the build)**: is this a genuinely new tier, or just a recall
  policy + a frontmatter convention over the **Library / self_tools / memory** we
  already have? Resolve before building — a redundant subsystem violates "Prax stays
  nimble."
- **Concrete blueprint — Browser-BC / "Journey Forge Local"**
  ([github.com/Einsia/Browser-BC](https://github.com/Einsia/Browser-BC)): a working
  instantiation of this idea, scoped to browser tasks — *record trajectory → atomize
  → classify per-capability → **bucket same-capability segments (dedup)** → distill a
  reusable `SKILL.md` from real successes.* Two borrowables: (1) **distill from a real
  successful trajectory**, not hand-authored (matches the "born from a gap you solved"
  guardrail); (2) **capability-bucketing = dedup-by-theme**, the same accumulate-don't-
  duplicate mechanic as the signals primitive (#23). It also steers the **first capture
  domain**: the **browser spoke** — repetitive tasks ("export from app X") + an existing
  **CDP/Playwright trajectory stream** to record from = the highest-value place to start
  the record→distill→recall loop. *(Pattern only — the tool is separate + unlicensed.)*
- **Effort**: the prompt guardrail ~10 min; a flag-gated, default-off capture+recall
  prototype ~1–2 days (reusing the metacognitive trigger + workspace selector).
- **Status**: not started — tracked in evals via
  [`prax/eval/goldens/skill_capture_reuse.yaml`](../prax/eval/goldens/skill_capture_reuse.yaml)
  (rubric measures the baseline gap today; the same rubric measures the gain when
  capture ships), so the idea can't be lost.

### 18. Wire learned tier/role routing (close the dormant bandit loop)

- **Source**: Prax's own dormant scaffolding + [ATLAS](https://github.com/itigges22/ATLAS)
  (signal-fused difficulty estimator integrated with bandit-based tier routing — the
  target shape). See [`research/model-routing.md`](research/model-routing.md). *(An
  earlier version of this entry cited Sakana **Fugu** / Conductor / Trinity as
  external validation; that citation was **pulled** — Fugu's benchmark comparisons
  were community-noted as unfair, cross-source/cross-scaffold. The idea stands on
  Prax's own code + ATLAS regardless; see
  [`research/provider-independence-export-control.md`](research/provider-independence-export-control.md).)*
- **Why it matters**: Prax's delegation is **heuristic** (orchestrator LLM
  tool-choice over a fixed spoke/category map) and model routing is **static**
  (tiers + per-component config). The learned-routing scaffolding already exists but
  is **dormant**: `prax/agent/tier_bandit.py` (Thompson sampler) never has
  `select_tier`/`record_outcome` called on the live path, and `difficulty.py`'s
  estimate is discarded — so the learning loop never closes.
- **Prax mapping** [verified]: close the loop on the existing bandit — `select_tier`
  at component entry (`llm_factory.build_llm`/`get_component_config`), `record_outcome`
  at turn end (`orchestrator.py`), behind a flag, **measured against static routing**
  (never spike — `CLAUDE.md`). A **Thinker/Worker/Verifier** role layer maps onto the
  existing diverse-reviewer / `multi_model_query` / verifier patterns over current
  tiers/providers; RL-trained delegation is a larger, later step.
- **Escalation target — a Mixture-of-Agents "virtual provider".** Beyond escalating a
  hard turn to a higher *tier*, escalate it to a MoA *ensemble*: cheap reference models
  (no tool schemas, stripped context) feed an aggregator that acts — integrated at the
  `llm_factory` model-provider layer so the orchestrator just "picks a model" (cleaner
  than the `multi_model_query` tool; preserves the agent loop + caching). **Strictly
  difficulty-gated** (N models/turn is expensive) and governed by accept-rate (#22).
  See [`research/model-routing.md`](research/model-routing.md) §15 (Nous Hermes MoA).
- **Note**: the provider-independence half is already shipped (cross-provider
  failover + terminal-failure denylist+notify, `llm_fallback.py` — see
  [`research/provider-independence-export-control.md`](research/provider-independence-export-control.md)).
  This item is the *learned-routing* half only.
- **Effort**: wiring + flag + an eval comparison ~2–4 days; RL delegation is research-scale.
- **Status**: not started — `model-routing.md` now documents the dormant bandit
  honestly (no longer overstates it as wired).

### 19. Failure-driven self-improvement via trace-observable diffing — "Learning Mechanics" loop

- **Source**: [Ziming Liu, *"Discovering 108 Tricks to Accelerate Grokking"*](https://kindxiaoming.github.io/blog/2026/grokking-tricks/)
  (2026-06). The transferable idea is **not grokking** (a training-dynamics
  phenomenon Prax doesn't have) but the **loop**: instrument broadly → let an agent
  identify which observables *predict* the outcome (instead of a human guessing the
  metric) → hypothesize an **abstracted** intervention → **verify before rollout**.
- **Why it matters**: Prax already owns every rung *in isolation* — execution traces
  + `trace_search`/`trace_detail`/`review_my_traces` are the broad instrumentation;
  the "never spike benchmarks / fix the problem class" rule (`CLAUDE.md`) is the
  abstraction doctrine; goldens + `make eval` are the gate. What's missing is the
  **loop that connects them**: introspection and the eval gate don't talk to each
  other through a *discovery* step, and post-hoc failure analysis
  (`prax/agent/metacognitive.py`, the failure journal) keys on a failure **in
  isolation** rather than **diffing it against a passing sibling** to localize the
  predictive observable.
- **Prax mapping**: a flag-gated, default-off `trace_diff(pass_id, fail_id)`
  introspection step that contrasts two traces of the same task class, surfaces the
  diverging span / missing retrieval / changed tool result, and feeds an
  *abstracted* fix proposal into `make eval` before rollout. Reuse the existing trace
  store + decomposed evals — **not** a new "metrics / learning-mechanics" subsystem
  (keep Prax nimble). Composes with #11 (real eval framework) and #18 (close the
  bandit loop).
- **Guardrail**: the loop must **decline** when signal is insufficient (no
  contrasting passing run, noisy/non-reproducible flake) rather than fabricate a
  root cause — same honesty discipline as #17's "author only after a gap is
  overcome."
- **Effort**: a `trace_diff` introspection tool + the abstracted-fix-proposal prompt
  ~1–2 days; the closed auto-loop (propose → eval → flag-gated adopt) is larger.
- **Status**: not started — tracked in evals via
  [`prax/eval/goldens/failure_driven_trace_diff.yaml`](../prax/eval/goldens/failure_driven_trace_diff.yaml)
  (rubric measures the baseline gap today; the same rubric measures the gain when the
  loop ships), so the idea can't be lost.

### 20. Make sovereign / open-backend deployment first-class (validate the agent loop on an open backend)

- **Source**: assessing open backends for Prax. Two recommended models (see
  [`guides/cloud-gpu.md`](guides/cloud-gpu.md#sovereign--data-resident-deployment--which-open-model-to-serve)):
  **GLM-5.2** (Z.ai — 744B MoE / ~40B active, 1M ctx, **MIT**, strong agentic/coding +
  tool-calling) as the *frontier-capability* pick, and [Apertus](https://apertvs.ai/)
  (Swiss AI Initiative — EU AI Act-built, 1000+ languages) as the *compliance/multilingual*
  pick. **GLM-5.2 is the lead candidate to validate first** — its strong BFCL-class
  tool-calling is the best chance the 97-tool loop runs at fidelity on an open backend.
- **Why it matters**: Prax should serve **all** users, including those under
  **sovereignty / on-prem / no-egress / EU AI Act** constraints — the one scenario the
  default hosted backends (Claude et al., remote + US-hosted) can't satisfy. The
  *inference wiring* for this already exists and is **verified**: the `vllm`/`local`
  provider path (`prax/agent/llm_factory.py:219-230`) constructs cleanly against an
  Apertus server (`build_llm(provider='vllm', model='swiss-ai/Apertus-70B')` →
  `ChatOpenAI` bound to `VLLM_BASE_URL`). Documented in
  [`guides/cloud-gpu.md`](guides/cloud-gpu.md#sovereign--data-resident-deployment--which-open-model-to-serve)
  + `.env-example`.
- **The actual gap (what's NOT free)**: wiring an endpoint ≠ the **97-tool agent loop
  running at fidelity** on an open backend. Open models vary widely on OpenAI-style
  **tool-calling**, **instruction adherence** under a long system prompt, and
  **long-context** behaviour — and Prax leans hard on all three (orchestrator +
  delegation + governed tools). Unproven today; can't be assumed from "the URL loads."
- **Prax mapping**: run the existing smoke/eval surface against a self-hosted
  open backend via vLLM (start with **GLM-5.2**, then Apertus) — the fresh-install
  smoke test (`scripts/smoke_test.py`) plus the golden suite (`make eval`) — to
  *measure* tool-call success rate, schema adherence, and delegation correctness on
  the open backend vs. the hosted default. (Hosted OpenAI-compatible endpoints with
  a key — e.g. Baseten — need a small `base_url`+key provider option first; the
  self-hosted `vllm` path is already drop-in.) Where it falls
  short, the fix is an **abstraction** (prompt/tool-schema robustness that helps every
  weaker backend), never a per-model hack (`CLAUDE.md` "never spike"). Compose with the
  cross-provider failover already shipped (`llm_fallback.py`) so a sovereign deployment
  can still tier within its own model pool.
- **Effort**: a one-off validation pass against a rented/served Apertus ~half a day;
  hardening the agent loop for weaker tool-callers is open-ended (scope to findings).
- **Status**: not started — documented as a supported *config* today; this item is the
  *prove-it-works-well* follow-up that makes it first-class.

### 21. Portable, live-syncing "components" — make Library notes/outputs embeddable

- **Source**: [`research/teamwork-vs-microsoft-loop.md`](research/teamwork-vs-microsoft-loop.md)
  (comparison with Microsoft Loop, 2026-06). Loop's one genuinely transferable
  primitive is the **portable component**: a piece of content (list/table/note)
  that can be embedded in many places and **stays in sync** as the source changes.
- **Why it matters**: today a Library note or an agent **output** (a generated
  table, chart, task list) is **pinned in one place**. There's no way to drop a
  *live reference* to it into a chat message or another note that re-renders when
  the source updates — so agent results aren't portable the way Loop components
  are. This is a real gap for a workspace whose whole point is *producing*
  artifacts, and it needs **none** of Loop's M365/cloud machinery.
- **Prax/TeamWork mapping**: a **transclusion-by-reference** primitive — embed a
  Library note/output by id into a message or note; TeamWork renders it live and
  re-renders on change (the WebSocket stream already pushes updates). Composes
  with the existing Library (Project→Space→Notebook→Note,
  `prax/services/library_service.py`) + outputs store — a reference + a renderer,
  **not** a new content type. Cross-cutting: the *artifact/reference model* is
  Prax-side; the *embed rendering* is TeamWork-side.
- **Guardrail / explicitly NOT in scope**: do **not** chase Loop's M365
  integration, sensitivity/retention labels, or **multi-human CRDT co-editing** —
  wrong audience for an agent-teammate harness, large surface, low payoff here.
  The value is *portability of agent output*, not real-time human co-authoring.
- **Effort**: design the reference/transclusion model + a live-embed renderer
  ~2–4 days for a first slice (notes first, then outputs); live-update reuses the
  existing WS push.
- **Status**: not started — documented in the Loop comparison as the single
  adopt-candidate; tracked here so it isn't lost. **Cross-cutting**: the TeamWork
  rendering half is also tracked in TeamWork's own backlog
  (`teamwork/docs/BACKLOG.md` #1); this entry owns the Prax artifact/reference
  model. Keep the two in sync on the reference shape.

### 22. Loop-health metric — "cost per accepted change" (+ premature-completion guard)

- **Source**: Anatoli Kopadze, *"Loops explained"* (X, 2026-06-20). Most of the
  piece restates loop anatomy Prax already embodies (plan→execute→**verify**→
  iterate, maker≠checker, durable state, success+hard-cap stop conditions). Two
  ideas are worth keeping: (a) the metric that actually decides whether an
  autonomous loop is net-positive is **cost per *accepted* change** — not tokens
  spent or iterations run — and below a ~50% accept rate a loop costs more than it
  returns; (b) the **"Ralph Wiggum loop"** failure (the agent declares done on a
  half-finished job and the loop keeps spending silently).
- **Why it matters** [verified]: Prax's metrics (`prax/observability/metrics.py`)
  track tokens / latency / call counts (`LLM_TOKENS`, `LLM_DURATION`, …) and
  reference-free `EVAL_QUALITY`, but **nothing joins cost to *accepted* outcomes**.
  The accept signal **already exists** — `prax/services/feedback_service.py`
  (thumbs up/down on agent messages) — it's just never tied to token cost. And the
  task runner's `_report_success` (`prax/services/task_runner_service.py:278`)
  treats *completion* as success with **no accept gate** — a direct Ralph-Wiggum
  exposure for the autonomous pickup loop.
- **Prax mapping**: (1) a `prax_accepted_outcomes_total` counter + a derived
  **cost-per-accepted-change** signal (join `LLM_TOKENS` with the
  `feedback_service` thumbs / non-reverted task completions), surfaced on the
  observability dashboards and alertable when accept-rate drops below a floor;
  (2) an explicit **goal-not-met-but-exited guard** for the autonomous/scheduled
  loops (the task runner + `EVAL_NIGHTLY`), reusing the existing
  plan-completion / evidence-floor hallucination guards rather than a new check.
- **Guardrail**: never conflate **completed** with **accepted** — the whole point
  is that the model is too generous grading its own homework. The accept signal
  must come from an independent source (user feedback, a revert, an eval gate),
  not the loop's self-report.
- **Effort**: the counter + dashboard panel ~half a day; wiring the accept signal
  end-to-end + the premature-exit guard ~1–2 days.
- **Status**: not started — tracked in evals via
  [`prax/eval/goldens/loop_cost_per_accepted_change.yaml`](../prax/eval/goldens/loop_cost_per_accepted_change.yaml)
  (rubric measures the baseline gap today; the same rubric measures the gain when
  the metric + guard ship), so the idea can't be lost.

### 23. Ambient initiative loop — make Prax proactive, not just reactive (governed by #22)

- **Source**: Anthropic **Claude Tag** (a collaborative AI teammate in Slack:
  multiplayer/shared channel context, contextual memory, async self-scheduled
  execution over hours/days, and **"ambient" proactive behavior** — it flags
  relevant info and follows up on threads/tasks that have gone quiet, unprompted).
  Direct user pain (2026-06): *"Prax is so passive."* This is the highest-value,
  most-aligned-with-user-pain item in this backlog.
- **Why it matters** [verified]: Prax has the *aspiration* — an **"Initiative"**
  section in `system_prompt.md:27` ("Don't just wait for instructions… suggest,
  don't just serve") — and pieces (the task runner, the scheduler, opt-in proactive
  pop-quizzes, `system_prompt.md:392`). But it's only proactive **within a turn it's
  already having**, and almost nothing *gives* it turns: Prax wakes only on a direct
  message, an **explicitly-assigned** task (the task runner — `task_runner_service.py`,
  pickup marker `"prax"`), a cron, or an opt-in quiz cadence. There is **no ambient
  loop** that observes channel/workspace state and *self-initiates*. The task runner
  (the closest engine) does "assigned chores," never "notice what needs doing" — and
  shipped **`default=False`** (`settings.py`), so the one heartbeat was off. *(P0: now
  enabled in the local launch — `TASK_RUNNER_ENABLED=true` in the Makefile — a bounded
  pulse: it only executes work assigned to "prax".)*
- **Convergence (a prioritization signal)**: independent sources keep landing on
  this same thesis — Anatoli Kopadze (#22), Jason Zhou (below), Peter Steinberger &
  Boris Cherny, and PostHog's *"Why we're bullish on loops"* (2026-06, same four
  pieces: goal / context / **evaluation** / agent; "an agent on a cron pulls signals
  from product data and emits work to subagents" = this item's outer loop). When the
  builders of multiple major harnesses converge, that's an argument to **prioritize
  this**. (One caution from PostHog's framing — *"agents do the verification"* — is
  exactly where Prax stays sharper: self-verification needs an **independent** accept
  signal, #22, not the maker grading its own homework.)
- **Architecture (sharpened by Jason Zhou's "loop engineering",
  [template](https://github.com/JayZeeDesign/loop-engineer-template), 2026-06)** —
  the operational blueprint for this item. Split the harness into two nested layers:
  - **Inner loop** = task execution (context → tools → verify). Prax *has* this
    (orchestrator + spokes + `agent_plan` + verify).
  - **Outer loop** = *"what should the agent work on next, and how does it learn
    from the result?"* — the ambient layer Prax lacks; its cycle is
    **notice → investigate → act → record → verify → decide-next**.
  Three artifacts make the outer loop work and let loops **compound**:
  - **Signals** *(the key new primitive — adopt)*: a structured, durable,
    **dedup-by-theme, frequency-accumulating** record of "something worth working
    on" (schema: type/status/priority/sources/**frequency** + observation/evidence/
    causes/next-action/timeline; dedup rule: *increment frequency, never
    duplicate*). This is the missing data structure — without it the outer loop has
    nothing to accumulate "noticing" into; with it, separate loops read each other's
    signals and compound. Prax has the Library (notes/Kanban/tasks) but **no signal
    type** — closest fit: a new Library artifact `kind="signal"`.
  - **Loop contracts** *(adopt)*: a per-loop README — goal, trigger/cadence,
    workflow, **dedup rules**, timeline — so each opted-in loop is bounded,
    auditable, and configurable (the task runner has a prompt, no contract).
  - **Shared log / compounding**: read the latest N log entries before major work,
    append a concise linked summary after. Prax **half-has** this — `progress_read`/
    `progress_append` (per-space) + trace introspection; Zhou's is the cross-loop
    generalization.
- **Prax mapping**: an **ambient initiative tick** (the outer loop) reusing the
  scheduler/task-runner infra — per **opted-in** channel/space, gather recent
  activity + quiet/stalled threads + goals + memory + **open signals**, then a
  **judgment turn that DEFAULTS TO SILENCE** unless an action is high-value *and*
  high-confidence → either **write/increment a signal** (notice) or **act** (flag,
  follow up, advance a stalled task, **self-schedule** a follow-up). Plus **ambient
  channel awareness**: read activity in opted-in channels Prax wasn't tagged in.
- **The governor (what makes it *good*, not annoying)**: wire it to the
  **cost-per-accepted-change / loop-health metric (#22)** so proactivity
  **self-calibrates** — throttle when its actions are ignored/rejected, expand when
  they're accepted. This is the missing feedback loop; without it, initiative lands
  on one of two cliffs (inert, or noisy-and-wrong — the *"don't SMS the user
  fabricated news at 9am"* class this whole backlog is organized around).
- **Guardrails (reuse what's shipped)**: per-channel **opt-in** + cadence (the
  faculty pop-quiz pattern is the proven model for unprompted outreach); the
  **hallucination/grounding guards** (never proactively assert unverified claims);
  a hard **confidence/value threshold** biased toward silence.
- **Effort (phased)**: **P0** enable + bound the task runner — *done* (the pulse).
  **P1** the observe→judge→act tick, default-silent, opt-in ~3–5 days. **P2** wire
  the #22 accept-rate governor + self-scheduled follow-ups ~days. **P3** ambient
  cross-channel awareness.
- **Status**: P0 shipped (task runner enabled in the local launch); P1–P3 not
  started — tracked in evals via
  [`prax/eval/goldens/proactive_initiative_judgment.yaml`](../prax/eval/goldens/proactive_initiative_judgment.yaml)
  (the rubric measures the hardest part — *when to act unprompted vs. stay quiet* —
  today and as the loop ships). Composes with **#22** (the governor) and the
  memory/grounding stack.

### 24. Move rendering (LaTeX / Mermaid / images) into the sandbox — and render diagrams for chat channels

- **Source**: user directive (2026-06): *"the LaTeX and image rendering should be
  sandbox — we shouldn't be doing that locally inside Prax."* Surfaced by a trace
  where Prax dumped a raw ```mermaid block into **Discord** (which renders plain
  text only), and by the fact that there is **no** Mermaid→image path at all.
- **Why it matters** [verified]: rendering currently runs **inside the Prax
  process** — `prax/services/latex_render.py` shells out to local `latex` +
  ImageMagick (`subprocess`), and `discord_service._render_latex_segments` invokes
  it to interleave math PNGs into Discord replies. That violates the *Prax-stays-
  sleek / sandbox-is-plug-and-play* principle (heavy renderers + a headless browser
  do **not** belong in core), and it doesn't generalize: **Mermaid has no renderer**
  (a local `npx mmdc` probe fails — no Chrome in core), so diagrams on Discord/SMS
  show raw source. Math is rendered, Mermaid isn't, and both are done in the wrong
  place.
- **Prax mapping**: a **sandbox-side render service** — the sandbox already has
  Node + Chrome — exposing `render_latex(src)→png` and `render_mermaid(src)→png`
  (mmdc / headless-chrome). Prax calls it over the existing sandbox client
  (`delegate_sandbox` / `sandbox_tools`), gets back image bytes, and the channel
  adapters attach them. Then:
  - **Migrate** `latex_render` off local `subprocess` to the sandbox call (keep a
    graceful fallback: if no sandbox, send a note link rather than raw source).
  - **Add Mermaid**: extend `discord_service` to detect ```mermaid blocks and
    interleave rendered PNGs exactly as it already does for `$$…$$` math — so a
    Mermaid block bound for Discord/SMS becomes an image, never raw source.
  - Aligns with the system-prompt guidance just added (chat channels render plain
    text; render to an image in the sandbox or send a note link; never raw).
- **Guardrail**: core must run **without** the sandbox (`SANDBOX_ENABLED=false`) —
  so the render path degrades to a note-link, never a crash and never raw diagram
  source presented as a visual.
- **Effort**: the sandbox render endpoints + client wiring ~1–2 days; the Discord
  Mermaid interleave mirrors the existing math path (~hours); migrating LaTeX off
  local subprocess ~half a day.
- **Status**: not started — system-prompt guidance shipped now (don't dump raw
  Mermaid/LaTeX to Discord/SMS); this item is the real render-in-sandbox fix.

### 25. Auto-disable `logprobs` on a provider 400 (end the recurring entropy-feature crash class)

- **Source**: the same bug biting twice — `gpt-5.4-pro` (the professor,
  `Responses.create() … 'logprobs'`) and then `gpt-5.5` (`400 'logprobs' is not
  supported with this model`). Both were the entropy feature
  (`llm_factory` injects `logprobs`/`top_logprobs` for `logprob_analyzer`) hitting a
  reasoning model that rejects the param. See
  [`research/model-routing.md`](research/model-routing.md) §14.
- **Why it matters**: the current guard is a **name denylist** (`-pro` / `o*` /
  `gpt-5.5`). It's fragile by construction — the *next* reasoning model that rejects
  logprobs crashes **every turn on its tier** until someone hand-adds a marker.
  Model ids change faster than this list will.
- **Prax mapping**: make logprobs **self-disabling**. On the first call where a
  model returns `400 … 'logprobs' is not supported` (or `temperature`), catch it,
  **cache "no logprobs/temperature" for that model id** (process-level + persisted),
  and **retry once without the offending param** so the turn still succeeds. Then
  the name denylist becomes a fast-path hint, not the safety mechanism. Entropy
  stays on where supported, off where not — with **zero** crashes and zero manual
  upkeep.
- **Guardrail**: the retry must be bounded (one strip-and-retry, not a loop) and the
  cache invalidatable, so a transient 400 doesn't permanently disable a feature.
- **Effort**: ~half a day (a wrapper around the OpenAI invoke path + a small
  per-model capability cache).
- **Status**: not started — name denylist holds for now (gpt-5.4/5.5 covered); this
  is the durable fix so we stop patching the list reactively.

### 26. Supervising auditor for fuzzy goldens (P0 shipped — follow-ons here)

- **Source**: "Diffuse AI Control on Fuzzy Tasks"
  ([`research/diffuse-ai-control-judge-robustness.md`](research/diffuse-ai-control-judge-robustness.md))
  — weak LLM judges reward impressive-but-vacuous answers on fuzzy tasks; a stronger
  scorer catches it.
- **Why it matters**: Prax's golden judge is a **low-tier** model on irreducibly-
  judgeable criteria — exactly the gameable case. The failure is asymmetric (weak
  judge → false **positives**), so a high-tier **auditor re-checks only the criteria
  the cheap judge passed** and may veto (1→0): maker≠checker applied to the judge.
- **P0 — SHIPPED**: `goldens.score_golden(audit=…)` runs the auditor on judged
  passes (veto power), records `vetoed`, degrades gracefully on auditor failure;
  `verify` (deterministic) criteria are never audited. Opt-in via
  `EVAL_AUDITOR_ENABLED` (eval-time only, default off). Tests cover veto + degrade.
- **Follow-ons (not done)**:
  1. **Cross-provider auditor** — point the auditor at a *different provider* so it's
     not the same model's blind spots checking themselves (reuse the failover pool).
  2. **"Gameable criterion" signal** — a criterion the auditor keeps vetoing is a
     candidate to convert to a `verify` check or rewrite; surface vetoes over runs
     (ties to the signals primitive, #23).
  3. **Enable in `make eval`** — flip `EVAL_AUDITOR_ENABLED` on for the pre-ship gate
     once cost/latency are confirmed acceptable.
  4. **Variants** if needed — a 2-model *jury* (disagreement = the flag) or escalate-
     only-on-suspicion, beyond audit-on-passes.
- **Guardrail**: the auditor is a **better proxy, not ground truth** (the paper's own
  caveat). Keep leaning on `verify` (deterministic) + #22 (independent accept signal)
  so a smarter judge doesn't create false confidence.
- **Effort**: P0 done; follow-ons ~1–2 days total.

### 27. Outbound MCP / A2A client — close the interop "consumer" half (Prax consumes other agents)

- **Source**: [`architecture/interoperability.md`](architecture/interoperability.md)
  — Prax is a first-class interop *provider* (MCP server + OKF interchange) but **not
  a consumer**. The single highest-leverage step onto the interoperability frontier.
- **Why it matters** [verified]: `prax/mcp/` is **server-only** — it exposes Prax
  tools to other agents; `prax/mcp/clients.py` is the inbound *caller registry*, not
  an outbound client. Prax can't yet treat an external MCP server (or an A2A peer) as
  a capability source. Closing this makes "expose *and* consume" symmetric.
- **Prax mapping**: an **outbound MCP/A2A client** that registers an external server
  as a **spoke** — its tools become governed, delegatable tools, reusing the existing
  hub-and-spoke + `governed_tool` (risk classification) + per-caller-identity
  machinery. **First slice:** connect to ONE configured external MCP server, surface
  its tools through a new `delegate_external` (or a synthetic spoke), behind a flag.
- **Guardrails**: (1) **tool overload** — imported tools must route through the
  on-demand tool-search / hub-and-spoke discipline (don't flood the orchestrator's
  ~50-tool budget); (2) **governance** — external tools are imported-untrusted →
  default risk classification through the gateway, never auto-HIGH; (3) **SSRF/egress**
  — outbound connections honor the egress allowlist; (4) per-server trust + bounded
  timeouts.
- **Effort**: a flag-gated single-server MCP client + spoke wiring ~2–3 days; A2A and
  multi-server discovery later.
- **Status**: not started — the provider half is shipped; this is the consumer half.

### 28. Content provenance / credentials — make Prax's output verifiably attributable

- **Source**: [`architecture/interoperability.md`](architecture/interoperability.md)
  (Identity & provenance axis) + the interpretability bridge
  ([`research/pangram-detector-interpretability.md`](research/pangram-detector-interpretability.md)):
  AI content is reliably detectable, so the honest move is **attribution, not evasion**.
- **Why it matters**: Prax *produces* content across Discord/SMS/TeamWork/notes/media
  with **no outbound attestation**. Downstream systems must *guess* provenance.
  Signing "produced by Prax / agent X" lets them **verify** it — the natural second
  interop frontier, and the right side of the AI-detection world.
- **Prax mapping**: attach **content credentials** — C2PA Content Credentials for
  generated **media** (images/audio/video from the sandbox render/gen paths); signed
  metadata / a provenance footer for **text notes** (the Hugo-published notes already
  have frontmatter to carry it). **First slice:** stamp generated media with a C2PA
  manifest at the sandbox render boundary (ties to #24's sandbox-render work), behind
  a flag.
- **Guardrails**: **opt-in** (some users/channels want no attribution); degrade
  gracefully where a channel strips metadata (Discord/SMS) — never block delivery;
  **honest** labels only (don't claim human authorship). Composes with #24
  (sandbox rendering is where media is produced) and the MCP per-caller identity.
- **Effort**: media C2PA stamping ~2–3 days (lib + the render boundary); text/note
  provenance ~1 day.
- **Status**: not started — documented as the provenance frontier in the interop doc.

### 29. Self-regeneration spoke — close the recursive self-improvement loop inward

- **Source**: [`agents/self-regeneration.md`](agents/self-regeneration.md) — the design.
  "AI in the loop that improves AI," scoped to **harness** self-improvement (Prax
  produces a better version of its own tools/prompts/routing/skills, *verified*).
- **Why it matters**: Prax has the self-modification **surfaces** (`plugin_write`,
  prompt-as-file, `finetune_service`, sandbox + `make ci`) and — crucially — the
  **hard half**: a gaming-resistant fitness function (`verify` + auditor + accept-rate
  #22 + never-spike) and safe flag-gated/checkpointed rollout. What's missing is the
  **one spoke that closes the loop**: notice → propose → isolate → verify → canary-adopt
  → rollback/record, run on Prax itself. It's **#23 (ambient loop) pointed inward**,
  governed by **#22**.
- **The precondition (non-negotiable)**: RSI optimizes brutally against its fitness
  function — a *gameable* eval yields a version that games it (the METR env-cheating
  finding at the self level, [`prax-benchmarks.md`](research/prax-benchmarks.md) §6).
  So the un-gameable-eval work (#26 auditor, `verify`, #22) **gates** this — don't
  widen self-modification autonomy faster than the fitness function earns trust.
- **Prax mapping / first rung**: a flag-gated **self-improvement spoke**. **P1
  (lowest-risk, most-measurable):** Prax proposes + tests a new/edited **plugin**
  against a golden, **auto-adopting behind a flag only if `make ci` + `make eval`
  improve without regression or spike.** That's "make a better version of one of my
  tools, proven." **Graded-autonomy boundary** (deny-by-default, the tool-risk model
  applied to self-changes): low-risk (plugin) auto-adopt behind the gate; medium
  (prompt) canary + accept-rate gate; **high (orchestrator policy / weights) → human
  PR gate** (the existing [`self-modification.md`](agents/self-modification.md) path).
- **The line not to cross (yet)**: autonomous orchestrator-policy / system-prompt
  edits before the fitness function is proven un-gameable — that's where a gamed eval
  turns self-improvement into undetectable self-degradation.
- **Effort**: P1 plugin micro-loop ~3–5 days (it's wiring existing pieces: trace-diff
  notice + `plugin_write` + worktree + `make ci`/`make eval` gate + flag + checkpoint
  rollback); widening to prompts/routing is later and gated on fitness-function trust.
- **Status**: not started — tracked in evals via
  [`prax/eval/goldens/self_regeneration_judgment.yaml`](../prax/eval/goldens/self_regeneration_judgment.yaml).
  Composes with #19 (notice), #22 (governor), #23 (the loop engine), #26 (un-gameable
  gate), #17 (record/compound).

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
