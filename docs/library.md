# Library — universal hierarchical workspace

The Library is Prax's (and, more generally, any TeamWork agent's)
persistent workspace for the user.  Every goal-directed effort lives
here: life areas, work initiatives, learning, creative projects,
operational boards, personal tracking — whatever the user wants to
organize.  Humans and agents collaborate on the same corpus with
explicit authorship provenance so neither steps on the other.

The core shape is **Project → Notebook → Note** with three cross-cutting
features:

- **Sequenced notebooks** for ordered lessons / steps / chapters with
  progress tracking and a "current lesson" pointer
- **Kanban task boards** per project — columns + cards with activity
  log, assignees, checklist, and due-date reminders wired into the
  existing scheduler
- **Raw / outputs / wikilinks / graph view / schema editor / health
  check** — every Obsidian-style feature you'd expect plus a
  Karpathy-style audit

> **Branding note.** TeamWork is agent-neutral — any agent running on
> TeamWork can own a Library — so the panel is simply **Library**.
> Alongside Library there is a **Home** rail showing active projects
> as cards.

## Scope — the wall between Kanban and `agent_plan`

> **The Library Kanban is for humans, not for Prax's working memory.**
> Prax has a separate, private to-do mechanism called `agent_plan`
> (stored at `workspaces/{user}/agent_plan.yaml`) that he uses for his
> own multi-step work.  The two systems MUST NOT be mixed.

| System | Whose list? | Storage | Time scale | Visibility |
|---|---|---|---|---|
| **`agent_plan`** | Prax's **private** working memory | `workspaces/{user}/agent_plan.yaml` | Seconds to minutes (per user turn) | Read-only widget in the chat view; cleared at end of turn |
| **Library Kanban** | The **human's** project board | `library/projects/{p}/.tasks.yaml` | Days to weeks (real project work) | Full Library UI, drag-and-drop, activity log, reminders |

**Why the wall matters:**

- **Different time scales.** Kanban is built for work items that live
  for days or weeks.  Prax's "tasks" are seconds-long tool calls — a
  single user turn would generate dozens of create/move/done operations
  and leave a cluttered board behind.
- **Different cognitive objects.** Kanban is a human UI for tracking
  durable commitments.  `agent_plan` is a compact internal state file
  Prax uses to remember where he was across a multi-step turn.
- **Context cost.** `agent_plan.yaml` compresses to ~200 tokens for a
  10-step plan; a Kanban task with activity log + metadata is 400-800
  tokens *per task*.  Prax reasoning over Kanban as working memory
  would burn significant context per turn.
- **Semantic collision risk.** If Prax used Kanban for his own work,
  he'd have to constantly disambiguate "my todo column" from "the
  user's todo column" when working inside a project that already has
  a real Kanban.  Keeping them in different files with different tool
  families removes the confusion entirely.
- **The research supports this.** See §22 and §25 in
  [`research/agentic-todo-flows.md`](research/agentic-todo-flows.md) —
  exposing Prax's working memory to the user as an editable artifact
  raises cognitive load and can actually *reduce* plan quality via
  miscalibrated trust.  The read-only chat widget (below) is the
  research-supported middle ground.

**What Prax may do with the Library Kanban:**

- Add tasks **when the user explicitly asks** for something tracked on
  the board ("add a card to remind me to ship the spec by Friday")
- Move / comment on / complete tasks the user asks him to touch
- Read the board for context when the user references it ("what's
  blocked on Q2?")

**What Prax must NOT do with the Library Kanban:**

- Use it to track his own tool-call sequence inside a single turn
- Create tasks representing ephemeral subgoals that exist only for
  the duration of a user request
- Mirror `agent_plan` steps onto the board

**Agent-plan visibility in the chat UI.**  Even though `agent_plan` is
private by default, the TeamWork chat view renders a small **read-only
card** showing Prax's current goal + current step + progress (`3/7
done`) whenever an `agent_plan` is active.  This gives the user
situational awareness without the cost of a full Kanban or the risks
of mid-execution oversight.  The card is not editable — the CHI 2025
Plan-Then-Execute study (N=248) found that mid-execution user edits
to plans often *reduce* plan quality when the system's initial plan
was already correct.

**Future possibility (not built, not planned yet).**  For genuinely
multi-day projects where Prax is the primary driver — e.g., "write
a 40-page report over the next two weeks" — we may eventually give
Prax limited write access to the Library Kanban so his long-horizon
work becomes visible alongside the user's work.  This would be an
explicit opt-in per project (a `prax_may_add_tasks` project-level
flag), not a default capability, and it would coexist with
`agent_plan` for ephemeral within-turn state.  This is deferred
until there's a concrete use case demanding it.

## Why projects are universal

"Project" in the Library is deliberately broad.  A project is anything
the user wants to organize toward a goal or outcome:

| Example use case | `kind` | Notebook shape | Tasks? |
|---|---|---|---|
| Learn French in 6 months | `learning` | sequenced notebooks for Vocab / Grammar / Speaking / Culture | practice reminders, chapter deadlines |
| Ship Feature X | `initiative` | free-form notebooks for Spec / Research / Decisions / Log | yes — the Kanban board *is* the feature tracker |
| Knit an Aran sweater | `creative` | free-form notebooks for Pattern / Materials / Progress photos | optional (shopping list, milestones) |
| Run business Ops | `ops` | notebooks for Metrics / Meetings / Decisions | yes — recurring tasks and reviews |
| Personal (life area) | `life_area` | free-form notebooks for Health / Hobbies / Journal | optional |
| Research X | `research` | sequenced notebook for reading list + free-form for notes | optional |

`kind` is a freeform label — the system never enforces any specific
set, but the UI suggests common ones via an autocomplete datalist.
The same hierarchy handles reading lists, software launches, and
knitting projects.  No separate "Courses" surface is needed — a
learning project is just a project whose notebooks are `sequenced=true`.

## Inspiration: Karpathy's Second Brain

The core shape of the Library is adapted from Andrej Karpathy's
personal knowledge base pattern (widely shared on Twitter in early
April 2026).  His central idea:

> *Three folders.  One schema file.  An AI that maintains everything.*

- `raw/` — the junk drawer.  Articles, clips, screenshots, meeting
  notes.  Never organized by hand; the agent classifies from here.
- `wiki/` — the organized layer.  Every topic is a markdown file,
  linked to related topics.  The agent maintains it.
- `outputs/` — generated answers, reports, and briefings.  Separate
  from the wiki so agent output never pollutes the source-of-truth.

Plus a schema file (he calls it `CLAUDE.md`; we call ours `LIBRARY.md`)
that tells the agent what the knowledge base is about, what the rules
are, and what the human cares about.

We extended Karpathy's pattern in two ways to make it fit Prax:

1. **A Project → Notebook hierarchy replaces the single flat `wiki/`.**
   Real users want separate life areas that don't bleed into each
   other.  Projects are the big groupings; notebooks are topics inside
   a project; notes are individual files inside a notebook.
2. **Explicit author provenance.**  Every note is tagged
   `author: human` or `author: prax`, and human-authored notes are
   read-only to Prax unless the human explicitly opts in.  This is the
   trust primitive that lets a human actually keep their own writing
   in the Library without worrying about the agent overwriting them.

## Layout on disk

Inside each user's workspace:

```
workspaces/{user_id}/library/
├── LIBRARY.md                      # schema / rules / user interests
├── INDEX.md                        # auto-maintained table of contents
├── .pending_engagements.yaml       # queue for prax_may_edit unlocks
├── raw/                            # unsorted captures (junk drawer)
│   └── 2026-04-08-140000-article.md
├── outputs/                        # generated briefs / reports / answers
│   └── 2026-04-08-140000-health-check.md
└── projects/
    └── {project-slug}/
        ├── .project.yaml           # project metadata
        ├── .tasks.yaml             # Kanban columns + tasks (when tasks_enabled)
        └── notebooks/
            └── {notebook-slug}/
                ├── .notebook.yaml
                └── {note-slug}.md
```

`LIBRARY.md` is seeded on first access and is freely editable by both
the human and the agent.  It's the contract between the human's intent
and the agent's behavior — the agent reads it at the start of every
library turn so the human's rules override any generic system prompt.

`INDEX.md` is rebuilt automatically on every note/notebook/project
write.  Humans can read it but shouldn't edit — edits will be
overwritten on the next mutation.

## Data model

### Project metadata (`.project.yaml`)

```yaml
slug: learn-french
name: Learn French
description: 6 months to fluency
kind: learning          # freeform label (learning, initiative, creative, ops, life_area, research, …)
status: active          # active | paused | completed | archived
target_date: "2026-10-01"
started_at: "2026-04-08T..."
pinned: true            # floats to the top of the Home dashboard
tasks_enabled: true     # show Kanban board?
reminder_channel: sms   # default channel for task reminders (all/sms/discord/teamwork)
created_at: "..."
updated_at: "..."
```

The Home dashboard shows pinned projects first, then alphabetical.

### Notebook metadata (`.notebook.yaml`)

```yaml
slug: lessons
name: Lessons
description: "Sequenced French lessons"
project: learn-french
sequenced: true         # ordered notebook with progress tracking
current_slug: vocab-basics   # pointer to the active lesson
created_at: "..."
updated_at: "..."
```

Flip `sequenced: true` to turn a notebook into an ordered sequence.
Notes gain `lesson_order` and `status` (todo/done).  Marking a note
done auto-advances `current_slug` to the next todo.

### Note frontmatter

```yaml
---
title: "Sleep optimization"
slug: sleep-optimization
author: human                 # "human" or "prax" — set once, never changes
project: personal
notebook: health
prax_may_edit: false          # permission gate — see below
last_edited_by: human
tags: [sleep, health, routine]   # always stored normalized: lowercase, no leading #
wikilinks: [cbt-i, circadian]    # extracted from [[…]] patterns on write
lesson_order: 0               # only meaningful in sequenced notebooks
status: todo                  # todo | done (only meaningful in sequenced notebooks)
created_at: "..."
updated_at: "..."
---
```

### Task entries (`.tasks.yaml`)

```yaml
columns:
  - id: todo
    name: "To Do"
  - id: doing
    name: "Doing"
  - id: done
    name: "Done"
tasks:
  - id: tsk-abc123
    title: "Ship feature X"
    description: "..."
    column: doing
    author: human           # or 'prax'
    source: user_request    # user_request | agent_derived | tool_output
    source_justification: ""    # required when source == tool_output
    confidence: medium      # low | medium | high (self-reported)
    assignees: ["prax", "sam"]
    due_date: "2026-04-15T17:00:00-07:00"
    reminder_enabled: true
    reminder_id: "rem-..."  # present iff a scheduler reminder is live
    reminder_channel: ""    # overrides project default when set
    checklist:
      - text: "Endpoints"
        done: true
      - text: "Auth"
        done: false
    activity:
      - actor: human
        at: "..."
        action: created
        source: user_request
      - actor: prax
        at: "..."
        action: moved
        from: todo
        to: doing
    comments: []
```

**Permission model**: unlike notes, tasks are inherently collaborative.
Both human and Prax can freely create, move, update, and comment on
any task — no `prax_may_edit` gate.  Every mutation appends to the
`activity` log with the actor, the action, and any relevant fields.
The log is the audit trail — read-only in the UI.

**Task provenance (P1)**: `source` records where the request to
create a task came from, so the activity trail can explain how a
task got onto the board.

- `user_request` — the human explicitly asked for this task.  This
  is the default when the UI's "Add task" button is the caller.
- `agent_derived` — Prax added the task while executing a user
  request (e.g., "break this down for me").  Default when
  `library_task_add` is called from an agent tool.
- `tool_output` — a tool suggested the task.  This is the dangerous
  case: a scraped webpage or calendar entry could carry instruction-
  like text that's really a prompt-injection attempt.  Creating a
  `tool_output` task requires `source_justification` or the call
  errors out; the UI renders an amber warning badge and a dedicated
  "Review before trusting" callout in the side panel.  Prax has a
  dedicated `library_task_add_from_tool_output` tool that forces
  him to state `source_tool` and explain why the task belongs on
  the user's board.

**Confidence signal (P2)**: `confidence` is Prax's self-reported
hint (`low` / `medium` / `high`) about how sure he is that a task
is well-scoped or — on `agent_plan` — that the plan is correct and
complete.  It is **not calibrated** and **not a probability**; it's
a situational-awareness cue rendered as a small colored dot (red /
amber / emerald) on cards and the `AgentPlanCard` widget so the
user knows when to pay extra attention.  The UI tooltip explicitly
labels it "self-reported, not calibrated".

**Plan context cap (P5)**: `workspace_service.get_workspace_context`
compacts large plans before injecting them into the system prompt.
When a plan has more than 6 steps or more than 800 characters of
step descriptions, the injected view shrinks to: goal, current step
in full, next 2 upcoming steps (brief), and a `… N more step(s)`
pointer at `agent_plan_status` as the overflow valve.  This
prevents long-running plans from eating turn context while still
keeping Prax aware of his current focus.  See
[`docs/research/agentic-todo-flows.md`](research/agentic-todo-flows.md)
§25 for the rationale.

## The `prax_may_edit` permission gate

The core trust primitive for notes.  It answers: "is the agent allowed
to modify this specific note?"

- **Prax-authored notes**: `prax_may_edit` defaults to `true`.  Prax
  created the note and can refine it freely.  The human can turn it
  off to freeze a Prax note as canonical.
- **Human-authored notes**: `prax_may_edit` defaults to `false`.  Prax
  cannot edit the note.  `update_note` refuses the mutation and
  returns an error that the knowledge spoke surfaces back so the
  orchestrator can tell the user to unlock the note first.
- **The UI "Ask Prax to refine" button** passes
  `override_permission=true` on the API call.  That's the human's
  explicit per-turn consent, so it bypasses the gate without requiring
  the persistent `prax_may_edit: true` flag.

### Proactive engagement on unlock

When the human flips `prax_may_edit` from `false` to `true` on a
human-authored note, the note is queued at
`library/.pending_engagements.yaml`.  At the start of every turn,
`workspace_service.get_workspace_context()` drains the queue and
injects this into Prax's system-prompt context:

> ## Proactive engagement — notes just unlocked for you
> The user flipped `prax_may_edit` to true on the following
> human-authored notes since your last turn. Read each one and
> proactively offer to refine, expand, fact-check, or add to it.
> Don't wait to be asked — the unlock is the ask.
>
> - **My thoughts** (`personal/journal/my-thoughts`) — unlocked at 2026-04-08T14:30:00Z

Prax-authored notes don't trigger the queue (Prax already has free
editing access).  Locking a note back removes any pending engagement.
The queue drains once per turn so Prax doesn't nag on subsequent turns.

## Storage service (`prax/services/library_service.py`)

### Projects

| Function | Purpose |
|---|---|
| `ensure_library(user_id)` | Idempotent skeleton creation |
| `create_project(user_id, name, description?, kind?, status?, target_date?, pinned?, tasks_enabled?, reminder_channel?)` | Create a project |
| `update_project(user_id, project, ...)` | Update any subset of metadata fields |
| `get_project(user_id, project)` | Full project detail with progress rollup |
| `list_projects(user_id)` | All projects, pinned first then alphabetical |
| `delete_project(user_id, project)` | Delete an empty project |
| `create_learning_project(user_id, subject, title?, modules?, description?, target_date?, notebook_name?)` | One-call course creation: project + sequenced notebook + ordered lesson notes |

### Notebooks

| Function | Purpose |
|---|---|
| `create_notebook(user_id, project, name, description?, sequenced?)` | Create a notebook inside a project |
| `update_notebook(user_id, project, notebook, name?, description?, sequenced?, current_slug?)` | Update metadata (auto-backfills `lesson_order` when turning sequenced on) |
| `get_notebook(user_id, project, notebook)` | Notebook metadata |
| `list_notebooks(user_id, project?)` | All notebooks (optionally scoped to one project) |
| `delete_notebook(user_id, project, notebook)` | Delete an empty notebook |
| `reorder_notes(user_id, project, notebook, slug_order)` | Batch rewrite `lesson_order` to match a new order |

### Notes

| Function | Purpose |
|---|---|
| `create_note(user_id, title, content, project, notebook, author?, tags?, prax_may_edit?, lesson_order?, status?)` | Create a note (author defaults to `prax`) |
| `get_note(user_id, project, notebook, slug)` | Fetch a single note with metadata + content |
| `list_notes(user_id, project?, notebook?)` | List notes in scope — sorted by `lesson_order` in sequenced notebooks |
| `update_note(user_id, project, notebook, slug, content?, title?, tags?, editor?, override_permission?)` | Update a note — enforces the permission gate |
| `delete_note(user_id, project, notebook, slug)` | Delete a note |
| `move_note(user_id, from_project, from_notebook, slug, to_project, to_notebook)` | Move a note between notebooks / projects |
| `set_note_status(user_id, project, notebook, slug, status)` | Mark a note todo/done (auto-advances `current_slug` on done) |
| `set_prax_may_edit(user_id, project, notebook, slug, editable)` | Toggle the permission flag and enqueue/drain a pending engagement |

### Wikilinks + backlinks + index

| Function | Purpose |
|---|---|
| `extract_wikilinks(body)` | Pull `[[slug]]`, `[[notebook/slug]]`, `[[project/notebook/slug]]`, and `[[slug|alias]]` targets from a note body |
| `get_backlinks(user_id, project, notebook, slug)` | Reverse wikilink lookup — notes that point to this one |
| `find_dead_wikilinks(user_id)` | Static scan for `[[…]]` targets that don't resolve |
| `rebuild_index(user_id)` | Regenerate `INDEX.md` from the current tree (auto-called on every mutation) |
| `read_index(user_id)` | Return current `INDEX.md` |
| `read_schema(user_id)` / `write_schema(user_id, content)` | `LIBRARY.md` I/O |

### Raw / outputs

| Function | Purpose |
|---|---|
| `raw_capture(user_id, title, content, source_url?)` | Drop an unsorted item into the junk drawer |
| `list_raw(user_id)` / `get_raw(user_id, slug)` / `delete_raw(user_id, slug)` | Raw capture CRUD |
| `promote_raw(user_id, raw_slug, project, notebook, new_title?)` | Promote a raw item into a real note (adds `promoted_from` provenance, removes the raw file) |
| `write_output(user_id, title, content, kind?)` | Save a generated brief/report/answer to `outputs/` |
| `list_outputs(user_id)` / `get_output(user_id, slug)` | Outputs CRUD |

### Tags (normalized)

| Function | Purpose |
|---|---|
| `list_tag_tree(user_id)` | Nested tag tree with `count` + `total` + `children` |
| `list_notes_by_tag_prefix(user_id, prefix)` | Notes matching a tag prefix or any descendant |

Every tag is canonicalized on write and read: leading `#` stripped,
lowercased, whitespace trimmed, `//` collapsed to `/`, empty segments
dropped, lists deduped.  Queries work case-insensitively —
`list_notes_by_tag_prefix(user, "#Math")` matches the same notes as
`list_notes_by_tag_prefix(user, "math")`.

### Refine

| Function | Purpose |
|---|---|
| `refine_note(user_id, project, notebook, slug, instructions)` | Low-tier LLM rewrites the body; returns `{before, after}` without applying |
| `apply_refine(user_id, project, notebook, slug, new_content)` | Apply an approved refinement (bypasses `prax_may_edit` because the UI click is consent) |

### Health check

| Function | Purpose |
|---|---|
| `run_health_check(user_id)` | Static + LLM audit of the whole library; writes a full report to `outputs/health-check-{date}.md` |
| `schedule_health_check(user_id, cron_expr?, channel?, timezone?)` | Create a recurring health check via `scheduler_service.create_schedule` |

Default cron is `"0 9 * * 1"` (Mondays at 09:00).  The scheduled prompt
asks Prax to run `library_health_check` and summarize the findings in
<200 words — full report still lands in `outputs/`.

### Tasks (`prax/services/library_tasks.py`)

| Function | Purpose |
|---|---|
| `list_columns(user_id, project)` | Kanban columns |
| `add_column(user_id, project, name)` | Add a column |
| `rename_column(user_id, project, column_id, new_name)` | Rename |
| `remove_column(user_id, project, column_id)` | Remove empty column |
| `reorder_columns(user_id, project, order)` | Reorder columns |
| `list_tasks(user_id, project, column?)` | List tasks, optionally filtered by column |
| `get_task(user_id, project, task_id)` | Full task with activity log + comments |
| `create_task(user_id, project, title, description?, column?, author?, assignees?, due_date?, reminder_enabled?, reminder_channel?, checklist?)` | Create a task + optional reminder |
| `update_task(user_id, project, task_id, ...)` | Update (reschedules reminder if due date / channel / enabled changes) |
| `move_task(user_id, project, task_id, new_column, editor?)` | Move between columns — cancels reminder on move to `done` |
| `delete_task(user_id, project, task_id)` | Delete + cancel reminder |
| `add_comment(user_id, project, task_id, text, actor?)` | Append a comment (also logged in activity) |
| `task_summary(user_id, project)` | Per-column counts for dashboards |

### Reminder lifecycle

When a task is created with a `due_date` and `reminder_enabled: true`
(the default), `library_tasks` automatically calls
`scheduler_service.create_reminder` over the project's configured
channel (`all` by default, overridable per-project or per-task).
The resulting `reminder_id` is stored on the task.

- **Update due_date / reminder_channel / reminder_enabled** → old
  reminder cancelled, new one scheduled
- **Move to `done` column** → reminder cancelled
- **Delete task** → reminder cancelled

This makes the Kanban a real lightweight ticketing system: put a date
on a card and you'll be pinged; mark it done and the ping disappears.

## Agent tools (`prax/agent/library_tools.py`)

30 tools registered in the knowledge spoke (and the course spoke,
alongside the legacy `course_*` tools).

**Projects**: `library_project_create`, `library_projects_list`,
`library_project_update`, `library_create_learning_project`

**Notebooks**: `library_notebook_create`, `library_notebooks_list`,
`library_notebook_sequence`, `library_notebook_reorder`

**Notes**: `library_note_create`, `library_note_read`,
`library_note_update`, `library_note_move`, `library_note_mark`,
`library_notes_list`

**Raw / outputs / health**: `library_raw_capture`, `library_raw_list`,
`library_raw_promote`, `library_outputs_write`, `library_outputs_list`,
`library_health_check`, `library_schedule_health_check`

**Kanban**: `library_task_add`, `library_tasks_list`,
`library_task_move`, `library_task_update`, `library_task_delete`,
`library_task_comment`, `library_column_add`, `library_column_rename`,
`library_column_remove`

Agent-created notes always get `author: prax`.  Human-authored notes
come in through the TeamWork UI (the `POST /library/notes` endpoint
defaults to `author: human`).

The `library_note_update` tool enforces the permission gate — it
refuses to edit human-authored notes unless `prax_may_edit: true` is
set.  When Prax hits this, it stops and asks the user to unlock.

## HTTP API

All routes are prefixed `/teamwork/library`.  TeamWork proxies them
through `../teamwork/src/teamwork/routers/library.py` at the standard
`/api` prefix.

### Tree + projects

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/library` | Full tree (projects → notebooks → notes metadata) |
| `POST` | `/library/projects` | Create project |
| `GET` | `/library/projects/{p}` | Project meta + progress |
| `PATCH` | `/library/projects/{p}` | Update project meta |
| `DELETE` | `/library/projects/{p}` | Delete empty project |

### Notebooks + notes

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/library/projects/{p}/notebooks` | Create notebook |
| `PATCH` | `/library/projects/{p}/notebooks/{n}` | Update notebook meta (sequenced, current_slug) |
| `POST` | `/library/projects/{p}/notebooks/{n}/reorder` | Batch reorder notes |
| `DELETE` | `/library/projects/{p}/notebooks/{n}` | Delete empty notebook |
| `POST` | `/library/notes` | Create note (defaults to `author: human` — the UI is the caller) |
| `GET` | `/library/notes/{p}/{n}/{slug}` | Read note |
| `PATCH` | `/library/notes/{p}/{n}/{slug}` | Update note |
| `DELETE` | `/library/notes/{p}/{n}/{slug}` | Delete note |
| `PATCH` | `/library/notes/{p}/{n}/{slug}/move` | Move note |
| `PATCH` | `/library/notes/{p}/{n}/{slug}/editable` | Toggle `prax_may_edit` |
| `PATCH` | `/library/notes/{p}/{n}/{slug}/status` | Mark todo/done |
| `GET` | `/library/notes/{p}/{n}/{slug}/backlinks` | Notes that wikilink to this one |
| `POST` | `/library/notes/{p}/{n}/{slug}/refine` | Quick refine (preview, not applied) |
| `POST` | `/library/notes/{p}/{n}/{slug}/apply-refine` | Apply approved refinement |
| `POST` | `/library/notes/{p}/{n}/{slug}/refine-via-agent` | Full chat-agent refinement with tool access |

### Schema + index + tags

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/library/schema` | `LIBRARY.md` content |
| `PUT` | `/library/schema` | Overwrite `LIBRARY.md` |
| `GET` | `/library/index` | `INDEX.md` content |
| `GET` | `/library/tags` | Nested tag tree with counts |
| `GET` | `/library/notes/by-tag?prefix=math/algebra` | Notes matching a tag prefix |

### Raw / outputs / health

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/library/raw` | List raw captures |
| `POST` | `/library/raw` | Capture a raw item |
| `GET` | `/library/raw/{slug}` | Fetch a raw capture |
| `DELETE` | `/library/raw/{slug}` | Delete a raw capture |
| `POST` | `/library/raw/{slug}/promote` | Promote to notebook |
| `GET` | `/library/outputs` | List generated outputs |
| `GET` | `/library/outputs/{slug}` | Fetch an output |
| `POST` | `/library/health-check` | Run the audit |
| `POST` | `/library/health-check/schedule` | Schedule the audit on a recurring cron |

### Kanban

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/library/projects/{p}/tasks` | List tasks (optionally `?column=...`) |
| `POST` | `/library/projects/{p}/tasks` | Create task |
| `GET` | `/library/projects/{p}/tasks/{id}` | Full task detail |
| `PATCH` | `/library/projects/{p}/tasks/{id}` | Update task |
| `DELETE` | `/library/projects/{p}/tasks/{id}` | Delete task |
| `PATCH` | `/library/projects/{p}/tasks/{id}/move` | Move to column |
| `POST` | `/library/projects/{p}/tasks/{id}/comment` | Add comment |
| `GET` | `/library/projects/{p}/tasks/columns` | List columns |
| `POST` | `/library/projects/{p}/tasks/columns` | Add column |
| `PATCH` | `/library/projects/{p}/tasks/columns/{id}` | Rename column |
| `DELETE` | `/library/projects/{p}/tasks/columns/{id}` | Remove empty column |
| `POST` | `/library/projects/{p}/tasks/columns/reorder` | Reorder columns |

## Hugo publishing (`prax/services/hugo_publishing.py`)

Note and course publishing share a single Hugo site at
`workspaces/{user}/courses/_site/` (historical — the URL scheme is
`/courses/…` for courses and `/notes/…` for notes).  The shared
primitives live in `hugo_publishing.py`:

| Symbol | Purpose |
|---|---|
| `KATEX_HEAD`, `THEME_CSS` | KaTeX + Mermaid boot + theme CSS |
| `courses_dir(root)` | Path to the user's `courses/` directory (creates it) |
| `hugo_site_dir(root)` | Path to the shared Hugo site inside `courses/_site/` |
| `ensure_hugo_site(root, base_url, site_title?)` | Create the Hugo skeleton (config + layouts) if missing |
| `run_hugo(site)` | Invoke the local `hugo` binary to build the site |
| `get_course_site_public_dir(user_id)` | Return the built `public/` dir for a specific user |
| `find_course_site_public_dir(path)` | Scan all workspaces to find which one contains a given path (used by the public `/courses/<path>` Flask route) |

`note_service.publish_notes` and `course_service.build_course_site`
both import from this module.  `course_service.generate_hugo_content`
stays in `course_service` because it's course-specific.
`course_service.py` keeps `# noqa: F401` re-exports of the shared
names for back-compat with code that still does
`from prax.services.course_service import hugo_site_dir`.

## TeamWork UI (`LibraryPanel.tsx`)

Users reach the Library via the dedicated Library rail icon in the
left navigation.  The **Home** rail icon next to it shows active
projects as cards with status pills and progress bars — click one to
jump into its project view in the Library.

### Sidebar

Quick-access pseudo-nodes at the top:

- **Schema (LIBRARY.md)** — opens the schema editor
- **Index** — renders the auto-maintained `INDEX.md`
- **Raw** — browses raw captures (with a nested list when selected)
- **Outputs** — browses generated briefings / reports / answers
- **Graph** — opens the force-directed graph view
- **Health check** — opens the audit runner

Below those: the project → notebook → note tree.  Notes are draggable
to move between notebooks; drop targets highlight during dragover.
Clicking a project name opens the **project detail view** (metadata
editor + Kanban); clicking a notebook name opens the **notebook
view** (sequenced or free-form); clicking a note opens the note view.

### Project detail view

- Header with title + status pill + kind tag + target date + pin
  toggle + edit button
- Inline metadata editor (status, kind, target date, reminder
  channel, description)
- **Kanban board** — columns with drag-and-drop between columns,
  inline "Add task" at the bottom of each column, column rename /
  delete / add, task count per column
- Task cards show the author icon, title, due date (red if overdue),
  reminder bell, assignee chips, comment count
- Click any card to open the **task side panel**

### Task side panel

- Title, author, assignees, due date (with reminder toggle)
- Description (markdown)
- Checklist rendering
- Comment composer
- Full activity log with actor badges (`human` / `prax`)

### Notebook view

When a notebook has `sequenced: true`:

- Progress bar (done / total)
- Ordered lesson list with done checkboxes
- Current-lesson highlight
- **Next lesson** button
- Drag-to-reorder (updates `lesson_order` in batch)
- **Set current** action on non-current lessons
- Sequenced toggle in the header to switch to free-form

Free-form notebooks show a sorted title list with the same controls
minus the ordering.

### Note view

- **Author badge**: emerald/user for human, indigo/sparkle for agent
- **Wikilink rendering**: `[[slug]]`, `[[notebook/slug]]`, or
  `[[project/notebook/slug]]` patterns render as clickable indigo
  pills
- **Backlinks panel** below the note body with every note that links
  here
- Edit / delete / move / create actions in the header
- **Human-note action bar**: on notes you wrote, shows
  `Lock / Unlock for Prax` and `Ask Prax to refine` buttons
- **Refine flow** offers two paths:
  - **Quick refine** — cheap low-tier LLM, diff preview with
    Apply/Cancel
  - **Full agent** — routes through the chat agent with access to
    web search, arxiv lookup, knowledge graph, and all `library_*`
    tools; saves directly (no preview)

### Raw captures browser

Clicking "Raw" expands the list of pending captures.  Click any item
to view it.  The main pane shows a **Promote to notebook** action
that opens a project + notebook picker — on promote, the raw file is
deleted and a new note is created in the target notebook with a
`promoted_from` field linking back to the original (and `source_url`
copied through if present).

**Auto-capture from external channels**: inbound SMS and Discord
messages containing HTTP(S) URLs are auto-captured into `library/raw/`
before the agent runs.  Prax is told about the captured slug via a
system hint and can offer to promote it naturally.  PDFs are excluded
(they have their own flow via `pdf_service`).

### Outputs browser

Shows all generated briefings / reports / answers.  Each output has a
`kind` tag (`brief`, `report`, `answer`, `health-check`,
`news-briefing`) and a generation timestamp.  The daily morning
briefing and the health-check report both live here, keeping
agent-generated content out of the source-of-truth wiki.

### Graph view

Force-directed graph of every note + wikilink edge:

- **Forces**: Coulomb-like repulsion between every node pair, spring
  attraction along each wikilink edge (rest length ≈ 90px), weak
  center gravity, velocity friction at 0.85/tick
- **Termination**: at most 400 iterations per re-seed, then freezes
  (no CPU burn on the idle view)
- **Pan**: drag the empty canvas
- **Zoom**: mouse wheel, clamped to [0.2×, 3×]
- **Drag a node**: pins it in place — the simulation keeps running
  around it but doesn't move the pinned node itself
- **Double-click a pinned node** to unpin
- **Unpin all** button in the top-right when any node is pinned
- **Click without drag** (3px movement threshold) opens the note
- **Project filter** and **author filter** in the header
- **Reset view** button
- Emerald circles = human-authored, indigo = agent-authored
- Pinned nodes get an amber ring + small amber dot

No new dependencies — the physics sim runs in React state via
`requestAnimationFrame`.

### Health check

Press **Run now** to execute the audit.  The report shows:

- **Static checks** (no LLM): total notes, dead wikilinks, empty
  notebooks, orphan notes (no links in or out), short notes
  (<50 words) — each clickable to jump to the offending note
- **LLM analysis** (cheap low-tier call): contradictions between
  notes, unsourced claims, gap topics — each finding links to the
  relevant note(s)

A full markdown report is also saved to
`outputs/health-check-{date}.md` so you can review asynchronously.

Press **Schedule** in the header to open an inline form with preset
cron options (Mondays 09:00, Sundays 09:00, 1st of month 09:00,
Mon/Wed/Fri 09:00, Fridays 18:00) and a channel dropdown.  The
recurring job is created via `scheduler_service.create_schedule` and
shows up in the Scheduler panel — you can pause, edit, or delete it
from there.

### Schema editor

Click "Schema (LIBRARY.md)" in the sidebar to open a textarea bound
to `LIBRARY.md`.  Save to overwrite.  Prax re-reads `LIBRARY.md` at
the start of every library-related turn so human edits take effect on
the next interaction.

## Why this shape

The design addresses four specific failure modes from Prax's pre-Library era:

1. **Hallucinated briefings.** A knowledge base that treats raw
   captures and synthesized wiki entries as the same thing gives the
   agent nothing to ground on.  The raw / outputs split means
   synthesized content can be checked against raw sources, and the
   auditor can enforce "if this response cites external facts, at
   least one raw source must have been read."
2. **Spoke over-routing to memory.** The flat `notes/` dir gave the
   model no organizing principle, so everything got routed to "memory"
   as a catch-all.  A hierarchy with obvious human affordances makes
   the boundary concrete.
3. **No sense of ownership.** Before author provenance, the whole
   notes system felt like it belonged to Prax — humans were reluctant
   to add their own writing because they couldn't tell what was
   theirs.  The `author` field and the emerald/indigo badges make it
   obvious whose writing is whose.
4. **No trust primitive for collaboration.** Without `prax_may_edit`,
   the only safe default is "Prax cannot touch anything the human
   wrote," which makes the agent useless for refining or expanding
   the human's notes.  The opt-in flag gives humans a knob to turn on
   collaboration per-note, and the pending-engagement queue lets Prax
   proactively help when the human unlocks something.

## See also

- [`IDEAS_BACKLOG.md`](IDEAS_BACKLOG.md) — broader backlog of ideas
  adapted from Claude customer stories + cookbook recipes
- [`infrastructure/content-publishing.md`](infrastructure/content-publishing.md)
  — Hugo publishing flow for the SMS/Discord note delivery path
- Andrej Karpathy's "Second Brain" post (widely shared April 2026)
  for the three-folder idea and the schema-file pattern
