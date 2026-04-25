"""Library — hierarchical knowledge base with Project → Notebook → Note.

The layout inside each user's workspace::

    library/
    ├── LIBRARY.md            # schema / rules / user interests
    ├── raw/                  # unsorted captures (junk drawer)
    ├── outputs/              # generated briefs, reports, answers
    └── projects/
        └── {project}/
            ├── .project.yaml
            └── notebooks/
                └── {notebook}/
                    ├── .notebook.yaml
                    └── {note}.md

Design inspiration
------------------

The three-folder split (``raw`` / wiki / ``outputs``) is adapted from
Andrej Karpathy's personal knowledge base pattern — raw captures go to a
junk drawer, the organized wiki is maintained by the agent, and generated
reports live in a separate folder so they never pollute the source-of-truth
wiki.  We extended that pattern with a **Project → Notebook** hierarchy so
one library can span multiple life areas (personal, business, school) and
stay navigable.

Provenance
----------

Every note carries an ``author`` field (``"human"`` or ``"prax"``) and a
``prax_may_edit`` flag.  Prax may freely edit notes it authored itself,
but human-authored notes are read-only until the human explicitly flips
``prax_may_edit`` to ``True`` — this is how humans opt into collaboration
without risking their own writing being overwritten.
"""
from __future__ import annotations

import logging
import mimetypes
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from prax.services.workspace_service import workspace_root

logger = logging.getLogger(__name__)

LIBRARY_DIR = "library"
# On-disk folder name for spaces.  Renamed from "projects" to "spaces"
# in 2026-04 to disambiguate from TeamWork's top-level "project"
# concept — the hierarchy is now TeamWork > Project > Space > Notebook
# > Note.  The user confirmed early-stage data loss is acceptable, so
# no migration script ships with this rename.
SPACES_DIR = "spaces"
NOTEBOOKS_DIR = "notebooks"
# "raw" on disk, labeled "Inbox" in the UI — the junk drawer for
# unsorted captures (clips, auto-captured URLs, screenshots) that
# haven't been promoted into a notebook yet.
RAW_DIR = "raw"
# Long-term archive for PDFs / docs the user wants to keep but NOT
# actively synthesize.  Each archive entry is stored as markdown with
# frontmatter recording original filename, size, and source_url; the
# original binary (if any) can be kept in the user's workspace
# ``archive/`` dir and referenced from the library meta.
ARCHIVE_DIR = "archive"
OUTPUTS_DIR = "outputs"
SPACE_META = ".space.yaml"
NOTEBOOK_META = ".notebook.yaml"
LIBRARY_MD = "LIBRARY.md"

# The seed schema file — written once when a library is first created.
# Humans are free to edit it; Prax reads it at the start of library work to
# understand the user's interests and rules.
_DEFAULT_LIBRARY_MD = """# Library

This is your workspace — a single place to organize anything you want
to think about, track, or work toward.  Projects, courses, hobbies,
business initiatives, personal tracking: it all lives here.

## Shape

- **Projects** — anything goal-directed.  Life areas (Personal), work
  initiatives (Q2 launch), learning focuses (Learn French), creative
  projects (knit an Aran sweater), operational tracking (business
  ops) — whatever you want to organize.  Each project has a `status`
  (active / paused / completed / archived), a freeform `kind` label
  (learning / initiative / creative / ops / life_area / ...), and
  optional target date, pinned flag, and reminder channel.
- **Notebooks** — topics or phases within a project.  Normal
  notebooks are free-form collections.  Flip `sequenced: true` on a
  notebook to turn it into an ordered sequence with progress
  tracking (lessons, chapters, steps, onboarding flows).
- **Notes** — individual markdown pages inside notebooks.

Plus each project can have:

- **A Kanban task board** (columns + cards with activity log,
  assignees, due dates, and reminder integration) for tracking work
- **Raw / outputs folders** at the library level for unsorted
  captures and generated content

## Layout

- `raw/` — unsorted captures (clips, imports, screenshots).  Not yet
  classified.  Promote to a notebook when they're ready.
- `projects/{project}/notebooks/{notebook}/*.md` — the organized
  wiki.
- `projects/{project}/.tasks.yaml` — Kanban board for that project.
- `outputs/` — generated briefs, reports, answers.

## Collaboration rules

- Notes you authored yourself are read-only to Prax unless you flip
  `prax_may_edit` to `true` on the individual note.  Flip it on to
  invite Prax to refine, expand, or fact-check your writing.
- **Tasks** are different: both you and Prax can freely create, move,
  and edit any task.  The activity log on each task records who did
  what so you can always audit the history.
- When Prax edits a note, `last_edited_by` is set to `prax` so you
  can always see who touched a note last.

## Scope — Kanban is for you, not Prax

The Kanban board is **your** project management tool.  Prax has a
separate, private to-do mechanism (`agent_plan`) that he uses for his
own multi-step work within a single turn.  The two systems are kept
apart on purpose:

- **You** use the Kanban for work items that live for days or weeks —
  "ship Feature X", "book the trip", "finish chapter 3".
- **Prax** uses `agent_plan` for ephemeral within-turn working memory
  — "fetch the news, then summarize, then email the result".

Prax only adds tasks to the Kanban when you explicitly ask for
something tracked there.  He never mirrors his internal tool-call
sequence onto your board, and he won't clutter it with ephemeral
subgoals.  You can see Prax's current working plan in the chat view's
read-only "Currently working on" card whenever an `agent_plan` is
active.

## Your interests

_Write a few sentences about what you care about, what you're
building, and what you're trying to learn.  Prax reads this at the
start of every library-related turn so the agent prioritizes what to
capture and how to organize it._

## Inspiration

The three-folder split (raw / wiki / outputs) is adapted from Andrej
Karpathy's "Second Brain" personal knowledge base pattern, extended
with a Project → Notebook hierarchy so one library can span multiple
life areas and stay navigable.  The per-project Kanban board takes
its shape from Trello / GitHub Projects — columns, cards, and
activity logs — but ties directly into your task reminders so
cards with due dates actually ping you.
"""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _library_root(user_id: str) -> Path:
    return Path(workspace_root(user_id)) / LIBRARY_DIR


def _space_path(user_id: str, project: str) -> Path:
    return _library_root(user_id) / SPACES_DIR / project


def _notebook_path(user_id: str, project: str, notebook: str) -> Path:
    return _space_path(user_id, project) / NOTEBOOKS_DIR / notebook


def _note_path(user_id: str, project: str, notebook: str, slug: str) -> Path:
    return _notebook_path(user_id, project, notebook) / f"{slug}.md"


def ensure_library(user_id: str) -> Path:
    """Create the library skeleton if missing. Idempotent."""
    root = _library_root(user_id)
    (root / RAW_DIR).mkdir(parents=True, exist_ok=True)
    (root / ARCHIVE_DIR).mkdir(parents=True, exist_ok=True)
    (root / OUTPUTS_DIR).mkdir(parents=True, exist_ok=True)
    (root / SPACES_DIR).mkdir(parents=True, exist_ok=True)
    lib_md = root / LIBRARY_MD
    if not lib_md.exists():
        lib_md.write_text(_DEFAULT_LIBRARY_MD, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").strip().lower())
    return s.strip("-")[:64] or "untitled"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a YAML-frontmatter markdown file into (meta, body)."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            try:
                meta = yaml.safe_load(text[4:end]) or {}
            except yaml.YAMLError:
                meta = {}
            body = text[end + 5:]
            return meta, body
    return {}, text


def _serialize_frontmatter(meta: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n" + body


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Wikilinks — [[slug]] or [[project/notebook/slug]] extraction
# ---------------------------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|[^\[\]]+?)?\]\]")


def extract_wikilinks(body: str) -> list[str]:
    """Extract [[wikilink]] targets from a note body.

    Supports both bare slugs (``[[sleep-optimization]]``) and fully
    qualified paths (``[[personal/health/sleep-optimization]]``).  Aliased
    form ``[[slug|display text]]`` strips the alias and keeps the target.
    Returns a deduplicated list preserving first-seen order.
    """
    seen: set[str] = set()
    out: list[str] = []
    for match in _WIKILINK_RE.finditer(body or ""):
        target = match.group(1).strip()
        if target and target not in seen:
            seen.add(target)
            out.append(target)
    return out


def _normalize_wikilink_target(
    target: str,
    current_project: str,
    current_notebook: str,
) -> tuple[str, str, str]:
    """Resolve a wikilink target to a (project, notebook, slug) triple.

    - ``slug`` → assumes current project + notebook
    - ``notebook/slug`` → assumes current project
    - ``project/notebook/slug`` → fully qualified
    """
    parts = target.split("/")
    if len(parts) == 1:
        return current_project, current_notebook, parts[0]
    if len(parts) == 2:
        return current_project, parts[0], parts[1]
    return parts[0], parts[1], parts[2]


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def create_space(
    user_id: str,
    name: str,
    description: str = "",
    *,
    kind: str = "",
    status: str = "active",
    target_date: str | None = None,
    pinned: bool = False,
    tasks_enabled: bool = True,
    reminder_channel: str = "all",
) -> dict[str, Any]:
    """Create a new project.

    A project is a top-level container for *any* goal-directed effort —
    a life area (Personal), an initiative (Q2 launch), a learning focus
    (Learn French), a creative project (Aran sweater), whatever.  The
    ``kind`` field is a freeform label suggesting the intent so the UI
    can filter/group — the system never enforces a specific set.

    ``status`` starts at ``"active"``; allowed values are
    ``active | paused | completed | archived``.

    ``tasks_enabled`` controls whether the project gets a Kanban board
    (defaults ``True``).  ``reminder_channel`` is the default delivery
    channel for any task due-date reminders fired from this project
    (``all | sms | discord | teamwork``).
    """
    ensure_library(user_id)
    slug = _slugify(name)
    proj_dir = _space_path(user_id, slug)
    if proj_dir.exists():
        return {"error": f"Project '{slug}' already exists"}
    proj_dir.mkdir(parents=True)
    (proj_dir / NOTEBOOKS_DIR).mkdir()
    now = _now_iso()
    meta = {
        "slug": slug,
        "name": name,
        "description": description,
        "kind": kind,
        "status": status,
        "target_date": target_date or "",
        "started_at": now,
        "pinned": bool(pinned),
        "tasks_enabled": bool(tasks_enabled),
        "reminder_channel": reminder_channel,
        # Color theme — a hue value (0–360) that shifts the accent
        # color for this space.  0 = red, 40 = amber, 155 = emerald,
        # 200 = sky, 240 = indigo (default), 270 = violet, 350 = rose.
        # null/empty = use the global default (indigo).
        "theme_hue": None,
        "created_at": now,
        "updated_at": now,
    }
    (proj_dir / SPACE_META).write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    rebuild_index(user_id)
    logger.info("library: created space %s for user %s", slug, user_id)

    # Auto-generate a cover image for the new space if enabled.
    # Toggle via AUTO_GENERATE_COVER env var (default: true).
    import os
    auto_cover = os.environ.get("AUTO_GENERATE_COVER", "true").lower() not in ("false", "0", "no")
    if auto_cover:
        try:
            generate_space_cover(user_id, slug)
            logger.info("library: auto-generated cover for space %s", slug)
        except Exception:
            logger.debug("Auto-generate cover failed for %s (non-fatal)", slug, exc_info=True)

    return {"status": "created", "project": meta}


_PROJECT_STATUSES = {"active", "paused", "completed", "archived"}
_REMINDER_CHANNELS = {"all", "sms", "discord", "teamwork"}


def update_space(
    user_id: str,
    project: str,
    *,
    name: str | None = None,
    description: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    target_date: str | None = None,
    pinned: bool | None = None,
    tasks_enabled: bool | None = None,
    reminder_channel: str | None = None,
    theme_hue: int | None = None,
) -> dict[str, Any]:
    """Update any subset of space metadata fields.

    ``theme_hue`` is an integer 0–360 representing the accent hue for
    this space.  When set, the UI shifts all accent colors to this hue
    while the user is inside the space.  Pass ``-1`` to clear (revert
    to the global default).
    """
    proj_dir = _space_path(user_id, project)
    if not proj_dir.exists():
        return {"error": f"Space '{project}' not found"}
    meta_file = proj_dir / SPACE_META
    try:
        meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) or {}
    except Exception:
        meta = {}

    if status is not None:
        if status not in _PROJECT_STATUSES:
            return {"error": f"Invalid status '{status}'. Use one of {sorted(_PROJECT_STATUSES)}"}
        meta["status"] = status
    if reminder_channel is not None:
        if reminder_channel not in _REMINDER_CHANNELS:
            return {"error": f"Invalid reminder_channel '{reminder_channel}'. Use one of {sorted(_REMINDER_CHANNELS)}"}
        meta["reminder_channel"] = reminder_channel
    if name is not None:
        meta["name"] = name
    if description is not None:
        meta["description"] = description
    if kind is not None:
        meta["kind"] = kind
    if target_date is not None:
        meta["target_date"] = target_date
    if pinned is not None:
        meta["pinned"] = bool(pinned)
    if tasks_enabled is not None:
        meta["tasks_enabled"] = bool(tasks_enabled)
    if theme_hue is not None:
        if theme_hue < 0:
            meta["theme_hue"] = None  # clear → use global default
        else:
            meta["theme_hue"] = max(0, min(360, int(theme_hue)))

    meta["updated_at"] = _now_iso()
    meta_file.write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    rebuild_index(user_id)
    return {"status": "updated", "project": meta}


_LESSON_DRAFTING_PLACEHOLDER = (
    "_Prax is drafting this lesson in the background — refresh in a "
    "minute to see the content. (If it doesn't appear, ask Prax to "
    "`refine` or `expand` this lesson.)_"
)


def _expand_lesson_one(
    user_id: str,
    project_slug: str,
    notebook_slug: str,
    lesson_slug: str,
    *,
    course_title: str,
    subject: str,
    lesson_title: str,
    lesson_idx: int,
    total: int,
    sibling_titles: list[str],
) -> None:
    """Draft a single lesson body via LLM and write it to the note.

    Runs inside a worker thread.  Logs and swallows exceptions so one
    failure can't take down the rest of the fan-out.
    """
    try:
        from prax.agent.llm_factory import build_llm
        from prax.agent.user_context import current_user_id
        current_user_id.set(user_id)

        prior_block = (
            "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(sibling_titles[:lesson_idx]))
            if lesson_idx > 0
            else "  (none — this is the first lesson)"
        )
        upcoming_block = (
            "\n".join(
                f"  {lesson_idx + i + 2}. {t}"
                for i, t in enumerate(sibling_titles[lesson_idx + 1:])
            )
            if lesson_idx < total - 1
            else "  (none — this is the last lesson)"
        )

        prompt = (
            f"You are drafting lesson {lesson_idx + 1} of {total} for a course on {subject}.\n\n"
            f"Course title: {course_title}\n"
            f"Lesson title: {lesson_title}\n\n"
            f"Prior lessons (already covered, do not repeat):\n{prior_block}\n\n"
            f"Upcoming lessons (do not preempt):\n{upcoming_block}\n\n"
            "Write a focused 250–450 word lesson body in markdown. Include:\n"
            "- A 1–2 sentence overview of what this lesson covers\n"
            "- 3–5 key concepts as a bulleted list, each with a short explanation\n"
            "- A `## Practice` section with 2–3 concrete exercises or reflection prompts\n\n"
            "Don't repeat the lesson title as a heading — the title is shown above the content.\n"
            "Don't use placeholder phrases like \"this lesson covers\" — get straight to substance.\n"
            "Stay under ~450 words."
        )
        llm = build_llm(config_key="library_lesson_draft", default_tier="medium")
        result = llm.invoke(prompt)
        body = (result.content if hasattr(result, "content") else str(result)).strip()
        if not body:
            return
        update_note(
            user_id, project_slug, notebook_slug, lesson_slug,
            content=body, editor="prax",
        )
        logger.info(
            "library: drafted lesson %s/%s (%d/%d)",
            project_slug, lesson_slug, lesson_idx + 1, total,
        )
    except Exception:
        logger.exception(
            "library: failed to draft lesson %s/%s", project_slug, lesson_slug,
        )


def expand_lesson_stubs(
    user_id: str,
    project_slug: str,
    notebook_slug: str,
    *,
    course_title: str,
    subject: str,
    lessons: list[dict],
    max_workers: int = 4,
) -> None:
    """Fan out lesson-body LLM drafts in a background daemon thread.

    Returns immediately.  Inside the spawned thread, a
    :class:`ThreadPoolExecutor` drafts up to ``max_workers`` lessons
    concurrently — each one writes its result back via
    :func:`update_note` when it completes, so the frontend sees them
    fill in progressively on the next refetch.

    LLM rate-limit safety comes from ``max_workers`` (default 4).
    """
    if not lessons:
        return

    titles = [lesson.get("title") or "" for lesson in lessons]

    def _runner() -> None:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="lesson-draft") as pool:
            for idx, lesson in enumerate(lessons):
                pool.submit(
                    _expand_lesson_one,
                    user_id, project_slug, notebook_slug, lesson.get("slug", ""),
                    course_title=course_title,
                    subject=subject,
                    lesson_title=lesson.get("title") or "",
                    lesson_idx=idx,
                    total=len(lessons),
                    sibling_titles=titles,
                )

    import contextvars
    import threading
    ctx = contextvars.copy_context()
    threading.Thread(
        target=ctx.run, args=(_runner,), daemon=True,
        name=f"expand-lessons-{project_slug}",
    ).start()


def create_learning_space(
    user_id: str,
    subject: str,
    title: str = "",
    modules: list[dict] | None = None,
    *,
    description: str = "",
    target_date: str | None = None,
    notebook_name: str = "Lessons",
    target_space: str = "",
    expand: bool = True,
) -> dict[str, Any]:
    """Create a new learning project with a sequenced Lessons notebook.

    This is the Library-native way to create what would previously have
    been a course.  The result is a normal Library project with
    ``kind="learning"``, a sequenced notebook, and one ordered note per
    module (each starting at ``status: todo``).  Everything works with
    the standard library tools afterwards — no special course endpoints
    needed.

    Args:
        user_id: The user's workspace identifier.
        subject: The topic (used as fallback title and tag).
        title: Human-readable project title.  Defaults to the subject
            if not provided.
        modules: Optional list of module dicts, each with ``title`` and
            optional ``description`` / ``topics``.  If omitted, the
            notebook is created empty.
        description: Optional project description.
        target_date: Optional ISO date for completion target.
        notebook_name: Name of the sequenced notebook — defaults to
            ``"Lessons"``.  Use ``"Modules"`` or ``"Chapters"`` if that
            fits your domain better.

    Returns a dict with ``project``, ``notebook``, ``lessons`` (list of
    created note metadata), and ``status``.
    """
    project_title = title or subject.strip() or "Untitled course"
    if target_space:
        existing = get_space(user_id, target_space)
        if existing is None:
            return {"error": f"target_space '{target_space}' does not exist"}
        proj_result = {"project": existing}
        project_slug = existing["slug"]
    else:
        proj_result = create_space(
            user_id,
            name=project_title,
            description=description or f"Learning project: {subject}",
            kind="learning",
            target_date=target_date,
        )
        if "error" in proj_result:
            return proj_result
        project_slug = proj_result["project"]["slug"]

    nb_result = create_notebook(
        user_id,
        project_slug,
        notebook_name,
        description=f"Sequenced lessons for {subject}",
        sequenced=True,
    )
    if "error" in nb_result:
        return nb_result
    notebook_slug = nb_result["notebook"]["slug"]

    lessons: list[dict] = []
    for idx, module in enumerate(modules or []):
        mod_title = module.get("title") or f"Lesson {idx + 1}"
        mod_body_parts: list[str] = []
        if module.get("description"):
            mod_body_parts.append(str(module["description"]))
        if module.get("topics"):
            mod_body_parts.append("## Topics\n")
            for t in module["topics"]:
                mod_body_parts.append(f"- {t}")
        if expand:
            mod_body_parts.append("\n\n" + _LESSON_DRAFTING_PLACEHOLDER)
        else:
            mod_body_parts.append(
                "\n\n_This lesson is a stub — ask Prax to expand it or write your own._"
            )
        note_result = create_note(
            user_id,
            title=mod_title,
            content="\n".join(mod_body_parts),
            project=project_slug,
            notebook=notebook_slug,
            author="prax",
            tags=[subject.lower()] if subject else [],
            lesson_order=idx,
            status="todo",
        )
        if "note" in note_result:
            lessons.append(note_result["note"])

    # Set current lesson to the first one so the UI highlights it immediately.
    if lessons:
        update_notebook(
            user_id, project_slug, notebook_slug,
            current_slug=lessons[0]["slug"],
        )

    logger.info(
        "library: created learning project %s with %d lessons for user %s",
        project_slug, len(lessons), user_id,
    )

    if expand and lessons:
        expand_lesson_stubs(
            user_id, project_slug, notebook_slug,
            course_title=project_title,
            subject=subject,
            lessons=lessons,
        )

    return {
        "status": "created",
        "project": proj_result["project"],
        "notebook": nb_result["notebook"],
        "lessons": lessons,
        "expanding": bool(expand and lessons),
    }


def get_space(user_id: str, project: str) -> dict | None:
    """Return a single space's metadata (plus notebook + note counts)."""
    proj_dir = _space_path(user_id, project)
    if not proj_dir.exists():
        return None
    meta_file = proj_dir / SPACE_META
    try:
        meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) or {}
    except Exception:
        meta = {}
    meta.setdefault("slug", project)
    meta.setdefault("name", project)
    meta.setdefault("status", "active")
    meta.setdefault("kind", "")
    meta.setdefault("pinned", False)
    meta.setdefault("tasks_enabled", True)
    meta.setdefault("reminder_channel", "all")
    # Cover image lives at ``.cover.{ext}`` inside the space dir.
    # The meta stores the filename so the frontend can bust caches.
    cover_name = _find_cover_filename(proj_dir)
    if cover_name:
        meta["cover_image"] = cover_name
    # Include full notebook + note metadata so the SpacePage can render
    # without a second round-trip.  Same shape as get_tree() per-space.
    notebooks: list[dict] = []
    for nb in list_notebooks(user_id, project):
        nb_notes = list_notes(user_id, project, nb["slug"])
        done = sum(1 for n in nb_notes if n.get("status") == "done")
        nb_entry = {
            **nb,
            "progress_percent": round(100 * done / len(nb_notes)) if nb_notes else 0,
            "notes": [
                {
                    "slug": n.get("slug"),
                    "title": n.get("title"),
                    "author": n.get("author", "prax"),
                    "prax_may_edit": n.get("prax_may_edit", False),
                    "tags": n.get("tags", []),
                    "wikilinks": n.get("wikilinks", []),
                    "lesson_order": n.get("lesson_order", 0),
                    "status": n.get("status", "todo"),
                    "updated_at": n.get("updated_at"),
                }
                for n in nb_notes
            ],
        }
        notebooks.append(nb_entry)
    meta["notebooks"] = notebooks
    meta["notebook_count"] = len(notebooks)
    all_notes = [n for nb in notebooks for n in nb["notes"]]
    meta["note_count"] = len(all_notes)
    done_total = sum(1 for n in all_notes if n.get("status") == "done")
    meta["progress_percent"] = round(100 * done_total / len(all_notes)) if all_notes else 0
    return meta


# ---------------------------------------------------------------------------
# Space cover images — either user-uploaded or Prax-generated
# ---------------------------------------------------------------------------
#
# Cover images are stored at ``library/spaces/{slug}/.cover.{ext}``
# with ext in {png, jpg, jpeg, webp}.  Only one cover is kept per
# space; writing a new one replaces the old.  The meta doesn't store
# binary data — just the filename, so the frontend can use it as a
# cache-busting token.

_COVER_EXTS = ("png", "jpg", "jpeg", "webp")


def _find_cover_filename(space_dir: Path) -> str | None:
    """Return the cover image filename (e.g. ``.cover.png``) if present."""
    for ext in _COVER_EXTS:
        path = space_dir / f".cover.{ext}"
        if path.exists():
            return path.name
    return None


def save_space_cover(
    user_id: str,
    project: str,
    image_bytes: bytes,
    extension: str,
) -> dict[str, Any]:
    """Write a cover image for ``project`` to ``.cover.{ext}``.

    ``extension`` should be one of ``png``, ``jpg``, ``jpeg``, ``webp``
    — anything else is rejected.  Any existing cover (of any extension)
    is removed so only one cover exists per space at a time.
    """
    ext = (extension or "").lower().lstrip(".")
    if ext not in _COVER_EXTS:
        return {"error": f"Unsupported cover extension '{extension}'"}
    proj_dir = _space_path(user_id, project)
    if not proj_dir.exists():
        return {"error": f"Space '{project}' not found"}
    # Remove any existing cover of any extension.
    for old_ext in _COVER_EXTS:
        old = proj_dir / f".cover.{old_ext}"
        if old.exists():
            old.unlink()
    path = proj_dir / f".cover.{ext}"
    path.write_bytes(image_bytes)
    rebuild_index(user_id)
    logger.info("library: saved cover for space %s (%s, %d bytes)",
                project, ext, len(image_bytes))
    return {"status": "saved", "filename": path.name}


def get_space_cover_path(user_id: str, project: str) -> Path | None:
    """Return the filesystem path to a space's cover image, or None."""
    proj_dir = _space_path(user_id, project)
    if not proj_dir.exists():
        return None
    fn = _find_cover_filename(proj_dir)
    if not fn:
        return None
    return proj_dir / fn


def delete_space_cover(user_id: str, project: str) -> dict[str, Any]:
    """Remove a space's cover image."""
    proj_dir = _space_path(user_id, project)
    if not proj_dir.exists():
        return {"error": f"Space '{project}' not found"}
    removed = False
    for ext in _COVER_EXTS:
        old = proj_dir / f".cover.{ext}"
        if old.exists():
            old.unlink()
            removed = True
    if not removed:
        return {"error": "No cover image to delete"}
    rebuild_index(user_id)
    return {"status": "deleted"}


def generate_space_cover(
    user_id: str,
    project: str,
    prompt_hint: str = "",
    dark_mode: bool = True,  # kept for API compat but ignored
) -> dict[str, Any]:
    """Generate a cover image for ``project`` via the image-gen API.

    Uses OpenAI's image generation (DALL-E family) when
    ``OPENAI_KEY`` is set.  Builds a prompt from the space's name,
    description, and kind, optionally augmented by ``prompt_hint``,
    then saves the result as a PNG cover.  Returns an error dict if
    image generation isn't available (no API key, unsupported
    provider, or generation failure).
    """
    from prax.settings import settings as _settings

    if not _settings.openai_key:
        return {"error": "Image generation requires OPENAI_KEY in settings."}

    meta = get_space(user_id, project)
    if not meta:
        return {"error": f"Space '{project}' not found"}

    name = meta.get("name", project)
    description = meta.get("description", "")
    kind = meta.get("kind", "")
    prompt_parts = [
        f"A clean, minimalist cover illustration for a knowledge "
        f"workspace called '{name}'.",
    ]
    if description:
        prompt_parts.append(f"The workspace is about: {description}.")
    if kind:
        prompt_parts.append(f"Kind: {kind}.")
    if prompt_hint:
        prompt_parts.append(f"Style hint: {prompt_hint}.")
    prompt_parts.append(
        "Abstract, modern illustration with medium-toned colors that "
        "work on both light and dark backgrounds. Avoid pure white or "
        "pure black areas. Soft gradient background, no text, no "
        "people, no logos. 16:9 aspect ratio."
    )
    full_prompt = " ".join(prompt_parts)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=_settings.openai_key)
        # Prefer gpt-image-1.5 (if the setting points at an image
        # model), else fall back to dall-e-3.
        model = _settings.vision_model or "dall-e-3"
        if "image" not in model and "dall" not in model:
            model = "dall-e-3"
        response = client.images.generate(
            model=model,
            prompt=full_prompt,
            size="1536x1024",
            n=1,
        )
        # OpenAI client returns either URL or base64 data depending on
        # model/version.  Handle both.
        item = response.data[0]
        if getattr(item, "b64_json", None):
            import base64
            image_bytes = base64.b64decode(item.b64_json)
        elif getattr(item, "url", None):
            import requests
            r = requests.get(item.url, timeout=60)
            r.raise_for_status()
            image_bytes = r.content
        else:
            return {"error": "Image API returned no data"}
    except Exception as exc:
        logger.exception("Failed to generate cover for space %s", project)
        return {"error": f"Image generation failed: {exc}"}

    save_result = save_space_cover(user_id, project, image_bytes, "png")
    if "error" in save_result:
        return save_result
    return {
        "status": "generated",
        "filename": save_result["filename"],
        "prompt": full_prompt,
    }


def list_spaces(user_id: str) -> list[dict]:
    """List all projects in the user's library with notebook counts."""
    ensure_library(user_id)
    root = _library_root(user_id) / SPACES_DIR
    projects: list[dict] = []
    if not root.exists():
        return projects
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        meta_file = p / SPACE_META
        if meta_file.exists():
            try:
                meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                meta = {}
        else:
            meta = {}
        meta.setdefault("slug", p.name)
        meta.setdefault("name", p.name)
        meta.setdefault("status", "active")
        meta.setdefault("kind", "")
        meta.setdefault("pinned", False)
        meta.setdefault("tasks_enabled", True)
        meta.setdefault("reminder_channel", "all")
        cover_name = _find_cover_filename(p)
        if cover_name:
            meta["cover_image"] = cover_name
        nb_dir = p / NOTEBOOKS_DIR
        meta["notebook_count"] = sum(1 for n in nb_dir.iterdir() if n.is_dir()) if nb_dir.exists() else 0
        projects.append(meta)
    # Pinned first, then by name
    projects.sort(key=lambda m: (not m.get("pinned"), m.get("name", m["slug"])))
    return projects


def delete_space(
    user_id: str,
    project: str,
    *,
    archive_notes: bool = False,
) -> dict[str, Any]:
    """Delete a space and its tasks.

    If ``archive_notes`` is True, all notes from every notebook in the
    space are moved to ``library/archive/`` before the space directory
    is deleted.  This preserves the intellectual content while removing
    the organizational container.

    Tasks (``.tasks.yaml``) are always deleted with the space — they're
    ephemeral work-tracking, not knowledge worth preserving.
    """
    proj = _space_path(user_id, project)
    if not proj.exists():
        return {"error": f"Space '{project}' not found"}

    archived_count = 0
    if archive_notes:
        # Move every note to library/archive/ before nuking the dir
        for nb in list_notebooks(user_id, project):
            for note in list_notes(user_id, project, nb["slug"]):
                note_data = get_note(user_id, project, nb["slug"], note["slug"])
                if note_data:
                    archive_capture(
                        user_id,
                        title=note_data["meta"].get("title") or note["slug"],
                        content=note_data.get("content", ""),
                        tags=note_data["meta"].get("tags") or [],
                    )
                    archived_count += 1

    import shutil
    shutil.rmtree(proj)
    rebuild_index(user_id)
    logger.info(
        "library: deleted space %s for user %s (archived %d notes)",
        project, user_id, archived_count,
    )
    return {
        "status": "deleted",
        "project": project,
        "archived_notes": archived_count,
    }


# ---------------------------------------------------------------------------
# Notebooks
# ---------------------------------------------------------------------------

def create_notebook(
    user_id: str,
    project: str,
    name: str,
    description: str = "",
    *,
    sequenced: bool = False,
) -> dict[str, Any]:
    """Create a new notebook inside a project.

    ``sequenced=True`` marks the notebook as ordered — notes are displayed
    in ``lesson_order`` and track a ``status`` (todo/done) for progress
    tracking.  Normal notebooks are unordered collections.
    """
    proj = _space_path(user_id, project)
    if not proj.exists():
        return {"error": f"Project '{project}' not found"}
    slug = _slugify(name)
    nb = _notebook_path(user_id, project, slug)
    if nb.exists():
        return {"error": f"Notebook '{slug}' already exists in project '{project}'"}
    nb.mkdir(parents=True)
    meta = {
        "slug": slug,
        "name": name,
        "description": description,
        "project": project,
        "sequenced": bool(sequenced),
        "current_slug": "",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    (nb / NOTEBOOK_META).write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    rebuild_index(user_id)
    logger.info(
        "library: created notebook %s/%s (sequenced=%s) for user %s",
        project, slug, sequenced, user_id,
    )
    return {"status": "created", "notebook": meta}


def update_notebook(
    user_id: str,
    project: str,
    notebook: str,
    *,
    name: str | None = None,
    description: str | None = None,
    sequenced: bool | None = None,
    current_slug: str | None = None,
) -> dict[str, Any]:
    """Update notebook metadata (rename, toggle sequenced, set current lesson)."""
    nb = _notebook_path(user_id, project, notebook)
    if not nb.exists():
        return {"error": f"Notebook '{project}/{notebook}' not found"}
    meta_file = nb / NOTEBOOK_META
    try:
        meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) or {}
    except Exception:
        meta = {}

    if name is not None:
        meta["name"] = name
    if description is not None:
        meta["description"] = description
    if sequenced is not None:
        meta["sequenced"] = bool(sequenced)
        # When switching ON, seed lesson_order on existing notes if missing.
        if sequenced:
            notes = list_notes(user_id, project=project, notebook=notebook)
            next_order = 0
            for n in sorted(notes, key=lambda x: x.get("lesson_order", 10**9)):
                if "lesson_order" in n and isinstance(n["lesson_order"], int):
                    next_order = max(next_order, n["lesson_order"] + 1)
                else:
                    slug_ = n.get("slug", "")
                    path = _note_path(user_id, project, notebook, slug_)
                    if not path.exists():
                        continue
                    text = path.read_text(encoding="utf-8")
                    note_meta, body = _parse_frontmatter(text)
                    note_meta["lesson_order"] = next_order
                    note_meta.setdefault("status", "todo")
                    path.write_text(_serialize_frontmatter(note_meta, body), encoding="utf-8")
                    next_order += 1
    if current_slug is not None:
        meta["current_slug"] = current_slug

    meta["updated_at"] = _now_iso()
    meta_file.write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    rebuild_index(user_id)
    return {"status": "updated", "notebook": meta}


def get_notebook(user_id: str, project: str, notebook: str) -> dict | None:
    """Return notebook metadata (without its notes)."""
    nb = _notebook_path(user_id, project, notebook)
    if not nb.exists():
        return None
    meta_file = nb / NOTEBOOK_META
    try:
        meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) or {}
    except Exception:
        meta = {}
    meta.setdefault("slug", notebook)
    meta.setdefault("name", notebook)
    meta.setdefault("project", project)
    meta.setdefault("sequenced", False)
    meta.setdefault("current_slug", "")
    meta["note_count"] = sum(1 for _ in nb.glob("*.md"))
    return meta


def list_notebooks(user_id: str, project: str | None = None) -> list[dict]:
    """List notebooks — across the whole library or within a single project."""
    ensure_library(user_id)
    root = _library_root(user_id) / SPACES_DIR
    if not root.exists():
        return []
    projects = [root / project] if project else [p for p in sorted(root.iterdir()) if p.is_dir()]
    out: list[dict] = []
    for proj in projects:
        if not proj.exists() or not proj.is_dir():
            continue
        nb_root = proj / NOTEBOOKS_DIR
        if not nb_root.exists():
            continue
        for nb in sorted(nb_root.iterdir()):
            if not nb.is_dir():
                continue
            meta_file = nb / NOTEBOOK_META
            if meta_file.exists():
                try:
                    meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) or {}
                except yaml.YAMLError:
                    meta = {}
            else:
                meta = {}
            meta.setdefault("slug", nb.name)
            meta.setdefault("name", nb.name)
            meta.setdefault("project", proj.name)
            meta.setdefault("sequenced", False)
            meta.setdefault("current_slug", "")
            meta["note_count"] = sum(1 for _ in nb.glob("*.md"))
            out.append(meta)
    return out


def delete_notebook(user_id: str, project: str, notebook: str) -> dict[str, Any]:
    """Delete an empty notebook. Refuses if it still has notes."""
    nb = _notebook_path(user_id, project, notebook)
    if not nb.exists():
        return {"error": f"Notebook '{project}/{notebook}' not found"}
    notes = list(nb.glob("*.md"))
    if notes:
        return {"error": f"Notebook '{project}/{notebook}' still has {len(notes)} notes"}
    import shutil
    shutil.rmtree(nb)
    return {"status": "deleted", "notebook": f"{project}/{notebook}"}


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def create_note(
    user_id: str,
    title: str,
    content: str,
    project: str,
    notebook: str,
    *,
    author: str = "prax",
    tags: list[str] | None = None,
    prax_may_edit: bool | None = None,
    lesson_order: int | None = None,
    status: str = "todo",
) -> dict[str, Any]:
    """Create a note inside a notebook.

    ``author`` is ``"prax"`` for agent-created notes and ``"human"`` for
    notes the user wrote themselves via the UI.  ``prax_may_edit`` defaults
    to ``True`` for Prax-authored notes (Prax can always fix its own work)
    and ``False`` for human-authored notes (safe default — the human must
    opt in before Prax can modify their writing).
    """
    if author not in ("human", "prax"):
        return {"error": f"Invalid author '{author}' — must be 'human' or 'prax'"}
    nb_dir = _notebook_path(user_id, project, notebook)
    if not nb_dir.exists():
        return {"error": f"Notebook '{project}/{notebook}' not found"}

    base_slug = _slugify(title)
    slug = base_slug
    note_path = nb_dir / f"{slug}.md"
    # Suffix on collision so two notes with the same title both survive.
    if note_path.exists():
        slug = f"{base_slug}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        note_path = nb_dir / f"{slug}.md"

    if prax_may_edit is None:
        prax_may_edit = (author == "prax")

    now = _now_iso()
    wikilinks = extract_wikilinks(content)

    # Auto-assign lesson_order at the end of the notebook if not provided
    # (only meaningful when the notebook is sequenced, but we set it
    # unconditionally so toggling sequenced later doesn't need to backfill).
    if lesson_order is None:
        existing = list_notes(user_id, project=project, notebook=notebook)
        orders = [n.get("lesson_order") for n in existing if isinstance(n.get("lesson_order"), int)]
        lesson_order = (max(orders) + 1) if orders else 0

    meta = {
        "title": title,
        "slug": slug,
        "author": author,
        "project": project,
        "notebook": notebook,
        "prax_may_edit": bool(prax_may_edit),
        "last_edited_by": author,
        "tags": _normalize_tags(tags),
        "wikilinks": wikilinks,
        "lesson_order": int(lesson_order),
        "status": status if status in ("todo", "done") else "todo",
        "created_at": now,
        "updated_at": now,
    }
    note_path.write_text(_serialize_frontmatter(meta, content), encoding="utf-8")
    rebuild_index(user_id)
    logger.info(
        "library: created note %s/%s/%s (author=%s) for user %s",
        project, notebook, slug, author, user_id,
    )
    return {"status": "created", "note": meta}


def get_note(
    user_id: str,
    project: str,
    notebook: str,
    slug: str,
) -> dict[str, Any] | None:
    """Return a single note as ``{meta, content}`` or ``None`` if missing."""
    path = _note_path(user_id, project, notebook, slug)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    meta.setdefault("slug", slug)
    meta.setdefault("project", project)
    meta.setdefault("notebook", notebook)
    return {"meta": meta, "content": body}


def list_notes(
    user_id: str,
    project: str | None = None,
    notebook: str | None = None,
) -> list[dict]:
    """List notes across the library, optionally scoped to one project/notebook.

    Within a single notebook scope, notes are returned sorted by
    ``lesson_order`` if the notebook is sequenced, otherwise by title.
    When the scope spans multiple notebooks, the ordering is by file
    name on disk (stable but not semantic).
    """
    ensure_library(user_id)
    root = _library_root(user_id) / SPACES_DIR
    if not root.exists():
        return []
    out: list[dict] = []
    for proj_dir in sorted(root.iterdir()):
        if not proj_dir.is_dir():
            continue
        if project and proj_dir.name != project:
            continue
        nb_root = proj_dir / NOTEBOOKS_DIR
        if not nb_root.exists():
            continue
        for nb_dir in sorted(nb_root.iterdir()):
            if not nb_dir.is_dir():
                continue
            if notebook and nb_dir.name != notebook:
                continue
            notes_in_nb: list[dict] = []
            for note_file in sorted(nb_dir.glob("*.md")):
                try:
                    text = note_file.read_text(encoding="utf-8")
                except OSError:
                    continue
                meta, _body = _parse_frontmatter(text)
                meta.setdefault("slug", note_file.stem)
                meta.setdefault("title", meta.get("slug"))
                meta.setdefault("project", proj_dir.name)
                meta.setdefault("notebook", nb_dir.name)
                meta.setdefault("author", "prax")
                meta.setdefault("status", "todo")
                notes_in_nb.append(meta)
            # Sort by lesson_order when scoped to one sequenced notebook
            if notebook:
                nb_meta = get_notebook(user_id, proj_dir.name, nb_dir.name) or {}
                if nb_meta.get("sequenced"):
                    notes_in_nb.sort(key=lambda n: (n.get("lesson_order", 10**9), n.get("slug", "")))
            out.extend(notes_in_nb)
    return out


def reorder_notes(
    user_id: str,
    project: str,
    notebook: str,
    slug_order: list[str],
) -> dict[str, Any]:
    """Rewrite ``lesson_order`` on a batch of notes to match ``slug_order``.

    Any note in the notebook not listed keeps its existing order (pushed
    after the reordered notes).
    """
    nb = _notebook_path(user_id, project, notebook)
    if not nb.exists():
        return {"error": f"Notebook '{project}/{notebook}' not found"}

    order_map = {slug: idx for idx, slug in enumerate(slug_order)}
    notes = list_notes(user_id, project=project, notebook=notebook)
    # Any note not in slug_order keeps its relative order after the listed ones
    max_explicit = len(slug_order)
    for n in notes:
        slug = n.get("slug", "")
        if slug in order_map:
            new_order = order_map[slug]
        else:
            new_order = max_explicit
            max_explicit += 1
        path = _note_path(user_id, project, notebook, slug)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        meta["lesson_order"] = new_order
        meta["updated_at"] = _now_iso()
        path.write_text(_serialize_frontmatter(meta, body), encoding="utf-8")
    rebuild_index(user_id)
    return {"status": "reordered", "count": len(slug_order)}


def set_note_status(
    user_id: str,
    project: str,
    notebook: str,
    slug: str,
    status: str,
) -> dict[str, Any]:
    """Mark a note as todo or done (used for sequenced notebook progress)."""
    if status not in ("todo", "done"):
        return {"error": f"Invalid status '{status}'. Use 'todo' or 'done'."}
    path = _note_path(user_id, project, notebook, slug)
    if not path.exists():
        return {"error": f"Note '{project}/{notebook}/{slug}' not found"}
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    meta["status"] = status
    meta["updated_at"] = _now_iso()
    path.write_text(_serialize_frontmatter(meta, body), encoding="utf-8")

    # If the notebook is sequenced and current_slug was this note, advance
    # to the next todo when marking done.
    if status == "done":
        nb_meta = get_notebook(user_id, project, notebook) or {}
        if nb_meta.get("sequenced") and nb_meta.get("current_slug") == slug:
            notes = list_notes(user_id, project=project, notebook=notebook)
            next_todo = next(
                (n for n in notes if n.get("status") == "todo"), None,
            )
            update_notebook(
                user_id, project, notebook,
                current_slug=(next_todo.get("slug") if next_todo else ""),
            )
    rebuild_index(user_id)
    return {"status": "updated", "note_status": status}


def update_note(
    user_id: str,
    project: str,
    notebook: str,
    slug: str,
    *,
    content: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    editor: str = "prax",
    override_permission: bool = False,
) -> dict[str, Any]:
    """Update a note's content / title / tags.

    If ``editor`` is ``"prax"`` and the note is human-authored, the update
    is refused unless ``prax_may_edit`` is true on the note OR
    ``override_permission`` is explicitly set.  ``override_permission`` is
    only used by the UI's refine action, which the human initiates.
    """
    path = _note_path(user_id, project, notebook, slug)
    if not path.exists():
        return {"error": f"Note '{project}/{notebook}/{slug}' not found"}
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)

    if editor == "prax" and meta.get("author") == "human":
        if not meta.get("prax_may_edit") and not override_permission:
            return {
                "error": (
                    f"Note '{slug}' is human-authored and prax_may_edit is false. "
                    "Ask the user to enable editing before modifying it."
                )
            }

    if content is not None:
        body = content
        meta["wikilinks"] = extract_wikilinks(body)
    if title is not None:
        meta["title"] = title
    if tags is not None:
        meta["tags"] = _normalize_tags(tags)
    meta["updated_at"] = _now_iso()
    meta["last_edited_by"] = editor

    path.write_text(_serialize_frontmatter(meta, body), encoding="utf-8")
    rebuild_index(user_id)
    logger.info("library: updated note %s/%s/%s by %s", project, notebook, slug, editor)
    return {"status": "updated", "note": meta}


def delete_note(
    user_id: str,
    project: str,
    notebook: str,
    slug: str,
) -> dict[str, Any]:
    path = _note_path(user_id, project, notebook, slug)
    if not path.exists():
        return {"error": f"Note '{project}/{notebook}/{slug}' not found"}
    path.unlink()
    rebuild_index(user_id)
    return {"status": "deleted", "slug": slug}


def move_note(
    user_id: str,
    from_project: str,
    from_notebook: str,
    slug: str,
    to_project: str,
    to_notebook: str,
) -> dict[str, Any]:
    """Move a note to a different notebook (and optionally project)."""
    src = _note_path(user_id, from_project, from_notebook, slug)
    dst_nb = _notebook_path(user_id, to_project, to_notebook)
    if not src.exists():
        return {"error": f"Source note '{from_project}/{from_notebook}/{slug}' not found"}
    if not dst_nb.exists():
        return {"error": f"Destination notebook '{to_project}/{to_notebook}' not found"}
    dst = dst_nb / f"{slug}.md"
    if dst.exists():
        return {"error": f"A note named '{slug}' already exists in '{to_project}/{to_notebook}'"}

    text = src.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    meta["project"] = to_project
    meta["notebook"] = to_notebook
    meta["updated_at"] = _now_iso()
    dst.write_text(_serialize_frontmatter(meta, body), encoding="utf-8")
    src.unlink()
    rebuild_index(user_id)
    logger.info(
        "library: moved note %s from %s/%s to %s/%s",
        slug, from_project, from_notebook, to_project, to_notebook,
    )
    return {"status": "moved", "note": meta}


def set_prax_may_edit(
    user_id: str,
    project: str,
    notebook: str,
    slug: str,
    editable: bool,
) -> dict[str, Any]:
    """Flip the ``prax_may_edit`` flag on a note.

    Humans use this to invite Prax to collaborate on a note they wrote
    themselves.  Turning it on does not itself trigger any edits — it
    simply unlocks the permission gate in ``update_note``.

    When the flag is flipped from ``False`` to ``True`` on a
    human-authored note, the note is also added to the **pending
    engagement queue** — a small per-user file that the orchestrator
    drains at the start of each turn to proactively offer suggestions
    ("Hey, I just read your note, want me to add/expand/refine it?").
    Turning the flag back off removes any queued engagement.
    """
    path = _note_path(user_id, project, notebook, slug)
    if not path.exists():
        return {"error": f"Note '{project}/{notebook}/{slug}' not found"}
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    previously = bool(meta.get("prax_may_edit"))
    meta["prax_may_edit"] = bool(editable)
    meta["updated_at"] = _now_iso()
    path.write_text(_serialize_frontmatter(meta, body), encoding="utf-8")

    # Proactive engagement: only queue on human-authored notes flipping
    # from locked → unlocked.  Prax-authored notes don't need the queue
    # (Prax already has free editing access).
    author = meta.get("author", "prax")
    if author == "human":
        if editable and not previously:
            _enqueue_engagement(user_id, project, notebook, slug, meta)
        elif not editable:
            _drain_engagement(user_id, project, notebook, slug)

    return {"status": "updated", "prax_may_edit": bool(editable)}


# ---------------------------------------------------------------------------
# Pending engagement queue — proactive prax_may_edit hook
# ---------------------------------------------------------------------------

_ENGAGEMENT_FILE = ".pending_engagements.yaml"


def _engagement_path(user_id: str) -> Path:
    return _library_root(user_id) / _ENGAGEMENT_FILE


def _load_engagements(user_id: str) -> list[dict]:
    path = _engagement_path(user_id)
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return data


def _save_engagements(user_id: str, entries: list[dict]) -> None:
    path = _engagement_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(entries, sort_keys=False), encoding="utf-8")


def _enqueue_engagement(
    user_id: str,
    project: str,
    notebook: str,
    slug: str,
    meta: dict,
) -> None:
    """Add a note to the pending-engagement queue.  Dedupes on triple."""
    entries = _load_engagements(user_id)
    key = (project, notebook, slug)
    if any((e.get("project"), e.get("notebook"), e.get("slug")) == key for e in entries):
        return
    entries.append({
        "project": project,
        "notebook": notebook,
        "slug": slug,
        "title": meta.get("title", slug),
        "queued_at": _now_iso(),
    })
    _save_engagements(user_id, entries)
    logger.info(
        "library: queued proactive engagement for %s/%s/%s (user=%s)",
        project, notebook, slug, user_id,
    )


def _drain_engagement(
    user_id: str,
    project: str,
    notebook: str,
    slug: str,
) -> None:
    """Remove a specific note from the pending-engagement queue."""
    entries = _load_engagements(user_id)
    key = (project, notebook, slug)
    filtered = [
        e for e in entries
        if (e.get("project"), e.get("notebook"), e.get("slug")) != key
    ]
    if len(filtered) != len(entries):
        _save_engagements(user_id, filtered)


def pop_pending_engagements(user_id: str) -> list[dict]:
    """Return and clear the pending-engagement queue.

    Called by the orchestrator at the start of each turn.  The entries
    returned should be woven into the system-prompt context so Prax
    knows which human notes were just unlocked and can offer to
    refine/expand them in the next response.
    """
    entries = _load_engagements(user_id)
    if entries:
        _save_engagements(user_id, [])
    return entries


def peek_pending_engagements(user_id: str) -> list[dict]:
    """Return the pending-engagement queue without clearing it."""
    return _load_engagements(user_id)


# ---------------------------------------------------------------------------
# Tree (for the UI)
# ---------------------------------------------------------------------------

def _normalize_tag(tag: str) -> str:
    """Canonicalize a tag string.

    - Strip surrounding whitespace
    - Strip a leading ``#`` (``#math`` → ``math``)
    - Lowercase
    - Collapse any double-slashes into single slashes so ``math//algebra``
      round-trips to ``math/algebra``
    - Drop empty path segments
    """
    if not tag:
        return ""
    s = str(tag).strip().lstrip("#").lower().strip()
    if not s:
        return ""
    parts = [seg.strip() for seg in s.split("/") if seg.strip()]
    return "/".join(parts)


def _normalize_tags(tags: list[str] | None) -> list[str]:
    """Normalize a list of tags; strips empties and dedupes while
    preserving first-seen order."""
    if not tags:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        norm = _normalize_tag(t)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def _tag_path(tag: str) -> list[str]:
    """Split a nested tag like ``math/algebra/linear`` into its segments.

    Accepts un-normalized input — leading ``#``, mixed case, extra
    whitespace are all tolerated.
    """
    normalized = _normalize_tag(tag)
    return [seg for seg in normalized.split("/") if seg]


def list_tag_tree(user_id: str) -> dict[str, Any]:
    """Walk every note and aggregate tags into a nested tree.

    Returns a nested dict where each node has ``count`` (notes tagged
    with this exact path), ``total`` (notes tagged with this path or
    any descendant), and ``children`` (same shape, keyed by the next
    segment).  Supports nested tags like ``math/algebra/linear``.

    Example::

        {
          "count": 0,
          "total": 5,
          "children": {
            "math": {
              "count": 1,
              "total": 4,
              "children": {
                "algebra": {
                  "count": 0,
                  "total": 3,
                  "children": {
                    "linear": {"count": 2, "total": 2, "children": {}},
                    "abstract": {"count": 1, "total": 1, "children": {}}
                  }
                }
              }
            }
          }
        }

    Called by the UI's tag-browser sidebar.
    """
    root: dict[str, Any] = {"count": 0, "total": 0, "children": {}}
    for note in list_notes(user_id):
        tags = note.get("tags") or []
        if not tags:
            continue
        for tag in tags:
            segments = _tag_path(tag)
            if not segments:
                continue
            cursor = root
            cursor["total"] += 1
            for i, seg in enumerate(segments):
                if seg not in cursor["children"]:
                    cursor["children"][seg] = {"count": 0, "total": 0, "children": {}}
                cursor = cursor["children"][seg]
                cursor["total"] += 1
                if i == len(segments) - 1:
                    cursor["count"] += 1
    return root


def list_notes_by_tag_prefix(user_id: str, tag_prefix: str) -> list[dict]:
    """Return all notes whose tags include ``tag_prefix`` or any nested
    descendant (e.g., ``math`` matches ``math/algebra/linear``).
    """
    prefix_segs = _tag_path(tag_prefix)
    if not prefix_segs:
        return list_notes(user_id)
    out: list[dict] = []
    for note in list_notes(user_id):
        for tag in note.get("tags") or []:
            segs = _tag_path(tag)
            if len(segs) >= len(prefix_segs) and segs[: len(prefix_segs)] == prefix_segs:
                out.append(note)
                break
    return out


def get_tree(user_id: str) -> dict[str, Any]:
    """Return the full library tree: spaces → notebooks → notes metadata.

    Used by the TeamWork Library panel to render its sidebar.
    """
    ensure_library(user_id)
    spaces: list[dict] = []
    for sp in list_spaces(user_id):
        notebooks: list[dict] = []
        for nb in list_notebooks(user_id, sp["slug"]):
            notes = list_notes(user_id, sp["slug"], nb["slug"])
            done = sum(1 for n in notes if n.get("status") == "done")
            nb_entry = {**nb, "progress_percent": round(100 * done / len(notes)) if notes else 0, "notes": [
                {
                    "slug": n.get("slug"),
                    "title": n.get("title"),
                    "author": n.get("author", "prax"),
                    "prax_may_edit": n.get("prax_may_edit", False),
                    "tags": n.get("tags", []),
                    "wikilinks": n.get("wikilinks", []),
                    "lesson_order": n.get("lesson_order", 0),
                    "status": n.get("status", "todo"),
                    "updated_at": n.get("updated_at"),
                }
                for n in notes
            ]}
            notebooks.append(nb_entry)
        # Progress on the space rolls up from sequenced notebooks only;
        # non-sequenced notebooks have no "done" concept.
        seq_notes = [
            n for nb_entry in notebooks if nb_entry.get("sequenced")
            for n in nb_entry["notes"]
        ]
        if seq_notes:
            seq_done = sum(1 for n in seq_notes if n.get("status") == "done")
            space_progress = round(100 * seq_done / len(seq_notes))
        else:
            space_progress = 0
        spaces.append({**sp, "progress_percent": space_progress, "notebooks": notebooks})
    return {"spaces": spaces}


# ---------------------------------------------------------------------------
# Backlinks — reverse wikilink lookup
# ---------------------------------------------------------------------------

def get_backlinks(
    user_id: str,
    project: str,
    notebook: str,
    slug: str,
) -> list[dict]:
    """Return notes that link to the given (project, notebook, slug).

    Matches wikilinks in both short form (``[[slug]]``) and fully qualified
    form (``[[project/notebook/slug]]``).  Short-form matches count as
    backlinks only when the linking note is in the same notebook.
    """
    target_triple = (project, notebook, slug)
    backlinks: list[dict] = []
    for note in list_notes(user_id):
        source_project = note.get("project", "")
        source_notebook = note.get("notebook", "")
        source_slug = note.get("slug", "")
        if (source_project, source_notebook, source_slug) == target_triple:
            continue  # skip self-links
        links = note.get("wikilinks") or []
        for link in links:
            resolved = _normalize_wikilink_target(link, source_project, source_notebook)
            if resolved == target_triple:
                backlinks.append({
                    "project": source_project,
                    "notebook": source_notebook,
                    "slug": source_slug,
                    "title": note.get("title") or source_slug,
                    "author": note.get("author", "prax"),
                })
                break
    return backlinks


def find_dead_wikilinks(user_id: str) -> list[dict]:
    """Walk every note and report wikilinks that don't resolve to a real note.

    Returns entries of the form ``{source_project, source_notebook,
    source_slug, dead_target}``.  Cheap, no LLM — pure static scan.
    """
    # Build a set of existing note triples
    existing: set[tuple[str, str, str]] = set()
    all_notes = list_notes(user_id)
    for n in all_notes:
        existing.add(
            (n.get("project", ""), n.get("notebook", ""), n.get("slug", ""))
        )

    dead: list[dict] = []
    for note in all_notes:
        source_project = note.get("project", "")
        source_notebook = note.get("notebook", "")
        source_slug = note.get("slug", "")
        for link in note.get("wikilinks") or []:
            resolved = _normalize_wikilink_target(link, source_project, source_notebook)
            if resolved not in existing:
                dead.append({
                    "source_project": source_project,
                    "source_notebook": source_notebook,
                    "source_slug": source_slug,
                    "dead_target": link,
                })
    return dead


# ---------------------------------------------------------------------------
# INDEX.md — auto-maintained table of contents
# ---------------------------------------------------------------------------

def rebuild_index(user_id: str) -> None:
    """Regenerate library/INDEX.md from the current tree.

    Called automatically on every note/notebook/project mutation.  The file
    is owned by the agent — humans can read it but should not edit by hand
    (their edits will be overwritten on the next mutation).
    """
    try:
        root = _library_root(user_id)
        if not root.exists():
            return
        lines = [
            "# Library Index",
            "",
            "_Auto-maintained by Prax — do not edit by hand._",
            f"_Last rebuilt: {_now_iso()}_",
            "",
        ]
        projects = list_spaces(user_id)
        if not projects:
            lines.append("_No projects yet. Use the Library panel to create one._")
        for proj in projects:
            lines.append(f"## {proj.get('name', proj['slug'])}")
            if proj.get("description"):
                lines.append(f"_{proj['description']}_")
            lines.append("")
            notebooks = list_notebooks(user_id, proj["slug"])
            if not notebooks:
                lines.append("_(empty)_")
                lines.append("")
                continue
            for nb in notebooks:
                lines.append(f"### {nb.get('name', nb['slug'])}")
                if nb.get("description"):
                    lines.append(f"_{nb['description']}_")
                notes = list_notes(user_id, proj["slug"], nb["slug"])
                if not notes:
                    lines.append("- _(empty)_")
                else:
                    for n in notes:
                        author = n.get("author", "prax")
                        badge = "👤" if author == "human" else "🤖"
                        title = n.get("title") or n.get("slug")
                        lines.append(
                            f"- {badge} [[{proj['slug']}/{nb['slug']}/{n.get('slug')}]] "
                            f"— {title}"
                        )
                lines.append("")
        (root / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        logger.exception("library: failed to rebuild INDEX.md")


def read_index(user_id: str) -> str:
    """Return the current INDEX.md content, rebuilding if missing."""
    root = _library_root(user_id)
    idx = root / "INDEX.md"
    if not idx.exists():
        rebuild_index(user_id)
    return idx.read_text(encoding="utf-8") if idx.exists() else ""


# ---------------------------------------------------------------------------
# Schema — LIBRARY.md read/write
# ---------------------------------------------------------------------------

def read_schema(user_id: str) -> str:
    """Return the user's LIBRARY.md content, seeding the default if missing."""
    ensure_library(user_id)
    path = _library_root(user_id) / LIBRARY_MD
    return path.read_text(encoding="utf-8") if path.exists() else _DEFAULT_LIBRARY_MD


def write_schema(user_id: str, content: str) -> dict[str, Any]:
    """Overwrite LIBRARY.md with the provided content."""
    ensure_library(user_id)
    path = _library_root(user_id) / LIBRARY_MD
    path.write_text(content, encoding="utf-8")
    logger.info("library: schema updated for user %s (%d chars)", user_id, len(content))
    return {"status": "updated", "bytes": len(content)}


# ---------------------------------------------------------------------------
# Raw captures — the junk drawer
# ---------------------------------------------------------------------------

def raw_capture(
    user_id: str,
    title: str,
    content: str,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Save an unsorted capture to library/raw/.

    The raw folder is Karpathy's "junk drawer" — it holds unprocessed
    source material that hasn't been classified into a notebook yet.
    Raw entries are *not* eligible as wiki sources until promoted with
    ``promote_raw``.
    """
    ensure_library(user_id)
    raw_dir = _library_root(user_id) / RAW_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    slug = f"{ts}-{_slugify(title)}"
    path = raw_dir / f"{slug}.md"
    meta = {
        "slug": slug,
        "title": title,
        "source_url": source_url or "",
        "captured_at": _now_iso(),
        "kind": "raw",
    }
    path.write_text(_serialize_frontmatter(meta, content), encoding="utf-8")
    logger.info("library: captured raw item %s for user %s", slug, user_id)
    return {"status": "captured", "raw": meta}


def list_raw(user_id: str) -> list[dict]:
    """List all raw captures (newest first)."""
    ensure_library(user_id)
    raw_dir = _library_root(user_id) / RAW_DIR
    if not raw_dir.exists():
        return []
    out: list[dict] = []
    for f in sorted(raw_dir.glob("*.md"), reverse=True):
        try:
            meta, _ = _parse_frontmatter(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        meta.setdefault("slug", f.stem)
        meta.setdefault("title", meta.get("slug"))
        out.append(meta)
    return out


def get_raw(user_id: str, slug: str) -> dict | None:
    """Fetch a single raw capture."""
    path = _library_root(user_id) / RAW_DIR / f"{slug}.md"
    if not path.exists():
        return None
    meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    meta.setdefault("slug", slug)
    return {"meta": meta, "content": body}


def promote_raw(
    user_id: str,
    raw_slug: str,
    project: str,
    notebook: str,
    new_title: str | None = None,
) -> dict[str, Any]:
    """Move a raw capture into a notebook as a real note.

    The resulting note is marked ``author="prax"`` and carries a
    ``promoted_from`` reference pointing at the original raw slug so the
    provenance chain stays intact.
    """
    raw = get_raw(user_id, raw_slug)
    if raw is None:
        return {"error": f"Raw item '{raw_slug}' not found"}

    title = new_title or raw["meta"].get("title", raw_slug)
    result = create_note(
        user_id,
        title=title,
        content=raw["content"],
        project=project,
        notebook=notebook,
        author="prax",
        tags=["promoted-from-raw"],
    )
    if "error" in result:
        return result

    # Annotate the new note with provenance
    note = result["note"]
    path = _note_path(user_id, project, notebook, note["slug"])
    if path.exists():
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        meta["promoted_from"] = raw_slug
        if raw["meta"].get("source_url"):
            meta["source_url"] = raw["meta"]["source_url"]
        path.write_text(_serialize_frontmatter(meta, body), encoding="utf-8")

    # Remove the original raw capture — it now lives in the notebook
    (_library_root(user_id) / RAW_DIR / f"{raw_slug}.md").unlink(missing_ok=True)
    rebuild_index(user_id)
    return {"status": "promoted", "note": result["note"]}


def delete_raw(user_id: str, slug: str) -> dict[str, Any]:
    """Delete a raw capture without promoting it."""
    path = _library_root(user_id) / RAW_DIR / f"{slug}.md"
    if not path.exists():
        return {"error": f"Raw item '{slug}' not found"}
    path.unlink()
    return {"status": "deleted", "slug": slug}


# ---------------------------------------------------------------------------
# Outputs — generated briefs, reports, answers
# ---------------------------------------------------------------------------

def write_output(
    user_id: str,
    title: str,
    content: str,
    kind: str = "brief",
) -> dict[str, Any]:
    """Save a generated output (daily briefing, health check report, etc.)
    to library/outputs/.

    Outputs are kept separate from the wiki so agent-generated content
    never pollutes the source-of-truth notes.  This matches Karpathy's
    ``outputs/`` folder convention.
    """
    ensure_library(user_id)
    out_dir = _library_root(user_id) / OUTPUTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    slug = f"{ts}-{_slugify(title)}"
    path = out_dir / f"{slug}.md"
    meta = {
        "slug": slug,
        "title": title,
        "kind": kind,
        "generated_at": _now_iso(),
    }
    path.write_text(_serialize_frontmatter(meta, content), encoding="utf-8")
    logger.info("library: wrote output %s (kind=%s)", slug, kind)
    return {"status": "written", "output": meta}


def list_outputs(user_id: str) -> list[dict]:
    """List all generated outputs (newest first)."""
    ensure_library(user_id)
    out_dir = _library_root(user_id) / OUTPUTS_DIR
    if not out_dir.exists():
        return []
    out: list[dict] = []
    for f in sorted(out_dir.glob("*.md"), reverse=True):
        try:
            meta, _ = _parse_frontmatter(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        meta.setdefault("slug", f.stem)
        out.append(meta)
    return out


def get_output(user_id: str, slug: str) -> dict | None:
    """Fetch a single output file."""
    path = _library_root(user_id) / OUTPUTS_DIR / f"{slug}.md"
    if not path.exists():
        return None
    meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    meta.setdefault("slug", slug)
    return {"meta": meta, "content": body}


# ---------------------------------------------------------------------------
# Archive — long-term keepers (PDFs, docs, reference material)
# ---------------------------------------------------------------------------
#
# The archive is for material the user wants to RETAIN but NOT
# actively synthesize into the wiki.  Contrast with:
#
# - Inbox (raw/) — unsorted captures waiting to be classified.  User
#   action: promote to a notebook OR archive OR delete.
# - Notebooks — the active knowledge graph.  Everything in here is
#   part of the wiki, eligible for backlinks, subject to the health
#   check, and can be refined.
# - Archive — long-term keepers that the user has explicitly decided
#   should live here instead of in a notebook.  Reference material,
#   scanned documents, saved PDFs, receipts, old papers.  Each entry
#   is stored as a markdown file with YAML frontmatter; the extracted
#   text body is searchable but NOT wiki-eligible.  Optional
#   ``binary_path`` in the frontmatter points at the original file in
#   the workspace archive dir for users who want to keep the
#   source-of-truth PDF alongside the extracted text.

def archive_capture(
    user_id: str,
    title: str,
    content: str,
    *,
    source_url: str | None = None,
    source_filename: str | None = None,
    binary_path: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Save a document to library/archive/ as markdown.

    Used for PDFs and other documents the user wants to keep but not
    actively synthesize.  ``content`` should be the extracted markdown
    representation of the document.  ``binary_path`` (if provided) is
    an absolute path to the original file in the workspace archive
    dir so the UI can offer a "view original" link.

    Unlike ``raw_capture`` (the Inbox), archive entries are meant to
    be long-term and are not eligible for the "promote to notebook"
    flow — if the user later decides archived content should become a
    wiki note, they should create a new note from it via the normal
    create path.
    """
    ensure_library(user_id)
    archive_dir = _library_root(user_id) / ARCHIVE_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    slug = f"{ts}-{_slugify(title)}"
    path = archive_dir / f"{slug}.md"
    meta: dict[str, Any] = {
        "slug": slug,
        "title": title,
        "kind": "archive",
        "archived_at": _now_iso(),
        "tags": tags or [],
    }
    if source_url:
        meta["source_url"] = source_url
    if source_filename:
        meta["source_filename"] = source_filename
    if binary_path:
        meta["binary_path"] = binary_path
    path.write_text(_serialize_frontmatter(meta, content), encoding="utf-8")
    logger.info("library: archived %s for user %s", slug, user_id)
    return {"status": "archived", "archive": meta}


def list_archive(user_id: str) -> list[dict]:
    """List all archive entries (newest first)."""
    ensure_library(user_id)
    archive_dir = _library_root(user_id) / ARCHIVE_DIR
    if not archive_dir.exists():
        return []
    out: list[dict] = []
    for f in sorted(archive_dir.glob("*.md"), reverse=True):
        try:
            meta, _ = _parse_frontmatter(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        meta.setdefault("slug", f.stem)
        meta.setdefault("title", meta.get("slug"))
        out.append(meta)
    return out


def get_archive(user_id: str, slug: str) -> dict | None:
    """Fetch a single archive entry (meta + extracted markdown)."""
    path = _library_root(user_id) / ARCHIVE_DIR / f"{slug}.md"
    if not path.exists():
        return None
    meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    meta.setdefault("slug", slug)
    return {"meta": meta, "content": body}


def delete_archive(user_id: str, slug: str) -> dict:
    """Remove an archive entry.  Does not touch the original binary
    in the workspace archive dir — that's the user's to manage."""
    path = _library_root(user_id) / ARCHIVE_DIR / f"{slug}.md"
    if not path.exists():
        return {"error": f"Archive entry '{slug}' not found"}
    path.unlink()
    logger.info("library: deleted archive entry %s", slug)
    return {"status": "deleted", "slug": slug}


# ---------------------------------------------------------------------------
# Wiki — per-space knowledge base with auto-linking
# ---------------------------------------------------------------------------
#
# Wiki entries are stored as notes in a notebook named "Wiki" (slug
# "wiki") inside each space.  The notebook is created lazily on the
# first wiki entry.  This reuses all existing note infrastructure
# (wikilinks, backlinks, graph, search, health check).

WIKI_NOTEBOOK_NAME = "Wiki"
WIKI_NOTEBOOK_SLUG = "wiki"


def ensure_wiki_notebook(user_id: str, space_slug: str) -> dict:
    """Create the Wiki notebook in a space if it doesn't exist yet."""
    existing = get_notebook(user_id, space_slug, WIKI_NOTEBOOK_SLUG)
    if existing:
        return existing
    result = create_notebook(
        user_id, space_slug, WIKI_NOTEBOOK_NAME,
        description="Deep reference material — concepts, specs, guides. "
        "The source of truth for this space.",
    )
    if "error" in result:
        return result
    return result.get("notebook", result)


def auto_link_wiki_entries(user_id: str, space_slug: str) -> int:
    """Scan wiki entries and add [[wikilinks]] where titles are mentioned.

    For each wiki note, checks whether the body mentions the TITLE of
    any other wiki note in the same notebook.  If it does and no
    ``[[slug]]`` link already exists, inserts the link inline (wraps
    the first occurrence of the title in ``[[slug|title]]``).

    Returns the number of notes that were updated.
    """
    notes = list_notes(user_id, space_slug, WIKI_NOTEBOOK_SLUG)
    if len(notes) < 2:
        return 0

    # Build a title → slug lookup
    title_map: list[tuple[str, str]] = []
    for n in notes:
        title = n.get("title") or ""
        slug = n.get("slug") or ""
        if title and slug:
            title_map.append((title, slug))

    updated = 0
    for note_meta in notes:
        slug = note_meta.get("slug", "")
        full = get_note(user_id, space_slug, WIKI_NOTEBOOK_SLUG, slug)
        if not full:
            continue
        body = full.get("content", "")
        changed = False
        for other_title, other_slug in title_map:
            if other_slug == slug:
                continue  # don't self-link
            # Skip if already linked
            if f"[[{other_slug}]]" in body or f"[[{other_slug}|" in body:
                continue
            # Case-insensitive search for the title
            import re as _re
            pattern = _re.compile(_re.escape(other_title), _re.IGNORECASE)
            if pattern.search(body):
                # Replace the FIRST occurrence with a wikilink
                body = pattern.sub(
                    f"[[{other_slug}|{other_title}]]",
                    body,
                    count=1,
                )
                changed = True
        if changed:
            update_note(
                user_id,
                project=space_slug,
                notebook=WIKI_NOTEBOOK_SLUG,
                slug=slug,
                content=body,
                editor="prax",
                override_permission=True,
            )
            updated += 1

    return updated


# ---------------------------------------------------------------------------
# Refine — LLM-powered note improvement
# ---------------------------------------------------------------------------

def refine_note(
    user_id: str,
    project: str,
    notebook: str,
    slug: str,
    instructions: str,
) -> dict[str, Any]:
    """Ask a small LLM to refine a note based on human instructions.

    The human-initiated refine flow passes ``override_permission=True`` on
    the resulting ``update_note`` call so it works on human-authored notes
    even when ``prax_may_edit`` is false — the explicit button click is
    the consent signal.

    Returns ``{before, after, diff_summary}`` without writing by default
    unless ``auto_apply`` is set (Phase 2 UI does its own approval gate).
    """
    note = get_note(user_id, project, notebook, slug)
    if note is None:
        return {"error": f"Note '{project}/{notebook}/{slug}' not found"}

    try:
        from prax.agent.llm_factory import build_llm
        llm = build_llm(config_key="library_refine", default_tier="low")
    except Exception as exc:
        return {"error": f"Could not build refiner LLM: {exc}"}

    current_body = note["content"]
    current_title = note["meta"].get("title", slug)

    prompt = (
        "You are refining a knowledge-base note for the user. Follow their "
        "instructions precisely. Preserve the note's core facts and voice; "
        "do not fabricate citations, dates, or numbers. Output ONLY the new "
        "markdown body — no preamble, no explanation, no code fences.\n\n"
        f"=== NOTE TITLE ===\n{current_title}\n\n"
        f"=== CURRENT NOTE BODY ===\n{current_body}\n\n"
        f"=== USER INSTRUCTIONS ===\n{instructions}\n\n"
        "=== NEW NOTE BODY ===\n"
    )

    try:
        result = llm.invoke(prompt)
        new_body = (result.content if hasattr(result, "content") else str(result)).strip()
    except Exception as exc:
        return {"error": f"LLM refine failed: {exc}"}

    # Strip accidental code fences the model may wrap the body in
    if new_body.startswith("```"):
        lines = new_body.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        new_body = "\n".join(lines).strip()

    return {
        "status": "refined",
        "before": current_body,
        "after": new_body,
        "title": current_title,
    }


def apply_refine(
    user_id: str,
    project: str,
    notebook: str,
    slug: str,
    new_content: str,
) -> dict[str, Any]:
    """Apply a refined body to a note with ``override_permission=True``.

    Called after the UI's diff preview — the human has approved the
    change, so we bypass the prax_may_edit gate (the explicit approval
    click is the consent signal).
    """
    return update_note(
        user_id,
        project=project,
        notebook=notebook,
        slug=slug,
        content=new_content,
        editor="prax",
        override_permission=True,
    )


# ---------------------------------------------------------------------------
# Health check — Karpathy's monthly audit
# ---------------------------------------------------------------------------

def run_health_check(user_id: str) -> dict[str, Any]:
    """Run the monthly audit across the entire library.

    Two layers:

    1. **Static checks** (no LLM): dead wikilinks, empty notebooks,
       orphan notes (no backlinks and no wikilinks out), very-short notes
       (<50 words) in non-inbox notebooks.
    2. **LLM analysis** (cheap low-tier call): contradictions between
       notes, topics mentioned but never explained, claims lacking a
       source in raw/.  Runs a single batched prompt over a condensed
       representation of the library.

    Also writes a full report to ``outputs/health-check-{date}.md`` so
    the human can review and act on it asynchronously.  This is the
    *compounding-loop guard* from the Karpathy pattern — without it,
    agent-authored content that drifts gets treated as truth in future
    turns.
    """
    ensure_library(user_id)
    report: dict[str, Any] = {
        "generated_at": _now_iso(),
        "static": {},
        "llm": {},
    }

    # Static layer
    all_notes = list_notes(user_id)
    report["static"]["note_count"] = len(all_notes)
    report["static"]["dead_wikilinks"] = find_dead_wikilinks(user_id)

    # Empty notebooks
    empty_notebooks: list[dict] = []
    for nb in list_notebooks(user_id):
        if nb.get("note_count", 0) == 0:
            empty_notebooks.append({
                "project": nb.get("project"),
                "notebook": nb.get("slug"),
                "name": nb.get("name"),
            })
    report["static"]["empty_notebooks"] = empty_notebooks

    # Orphans — notes with no wikilinks out and no backlinks in
    orphans: list[dict] = []
    for n in all_notes:
        wikilinks_out = n.get("wikilinks") or []
        backlinks_in = get_backlinks(
            user_id, n.get("project", ""), n.get("notebook", ""), n.get("slug", ""),
        )
        if not wikilinks_out and not backlinks_in:
            orphans.append({
                "project": n.get("project"),
                "notebook": n.get("notebook"),
                "slug": n.get("slug"),
                "title": n.get("title"),
            })
    report["static"]["orphans"] = orphans

    # Short notes (<50 words) — often stubs that need fleshing out
    short_notes: list[dict] = []
    for n in all_notes:
        note_data = get_note(
            user_id, n.get("project", ""), n.get("notebook", ""), n.get("slug", ""),
        )
        if note_data and len((note_data["content"] or "").split()) < 50:
            short_notes.append({
                "project": n.get("project"),
                "notebook": n.get("notebook"),
                "slug": n.get("slug"),
                "title": n.get("title"),
            })
    report["static"]["short_notes"] = short_notes

    # LLM layer — skip if there's nothing to analyze
    if len(all_notes) == 0:
        report["llm"] = {
            "skipped": True,
            "reason": "library is empty",
        }
    else:
        try:
            from prax.agent.llm_factory import build_llm
            llm = build_llm(config_key="library_health_check", default_tier="low")
        except Exception as exc:
            report["llm"] = {"error": f"Could not build health-check LLM: {exc}"}
            _persist_health_report(user_id, report)
            return report

        # Condense each note to title + first 400 chars
        condensed: list[str] = []
        for n in all_notes[:60]:  # cap for cost
            note_data = get_note(
                user_id, n.get("project", ""), n.get("notebook", ""), n.get("slug", ""),
            )
            if not note_data:
                continue
            snippet = (note_data["content"] or "")[:400].replace("\n", " ")
            condensed.append(
                f"[{n.get('project')}/{n.get('notebook')}/{n.get('slug')}] "
                f"{n.get('title')}: {snippet}"
            )
        joined = "\n\n".join(condensed)

        prompt = (
            "You are auditing a personal knowledge base for quality.  For "
            "each note snippet below, decide whether the library has:\n\n"
            "1. **Contradictions** — two notes that claim opposite things\n"
            "2. **Unsourced claims** — assertions that cite no source\n"
            "3. **Gap topics** — subjects referenced across multiple notes "
            "but never explained on their own page\n\n"
            "Return ONLY valid JSON with this exact shape (no markdown):\n"
            '{"contradictions": [{"note_a": "project/notebook/slug", '
            '"note_b": "project/notebook/slug", "issue": "..."}], '
            '"unsourced": [{"note": "project/notebook/slug", "claim": "..."}], '
            '"gaps": [{"topic": "...", "mentioned_in": ["project/notebook/slug"]}]}'
            "\n\n=== NOTES ===\n"
            f"{joined}\n\n=== JSON OUTPUT ==="
        )

        try:
            result = llm.invoke(prompt)
            raw_text = result.content if hasattr(result, "content") else str(result)
            # Best-effort JSON parse
            import json
            raw_text = raw_text.strip()
            if raw_text.startswith("```"):
                lines = raw_text.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw_text = "\n".join(lines).strip()
            report["llm"] = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            report["llm"] = {
                "error": "LLM returned invalid JSON",
                "details": str(exc),
                "raw": raw_text[:500] if "raw_text" in locals() else "",
            }
        except Exception as exc:
            report["llm"] = {"error": f"Health check LLM call failed: {exc}"}

    _persist_health_report(user_id, report)
    return report


def schedule_health_check(
    user_id: str,
    cron_expr: str = "0 9 * * 1",
    channel: str = "all",
    timezone: str | None = None,
) -> dict[str, Any]:
    """Create a recurring schedule that runs the library health check.

    Defaults to Mondays at 09:00 (the first working day of most weeks).
    The scheduled prompt tells Prax to run ``library_health_check`` and
    summarize the findings over the requested channel.  Deliverables
    land in ``library/outputs/health-check-{date}.md`` automatically.

    This wires into the existing ``scheduler_service`` so the recurring
    job shows up in the Scheduler panel alongside every other reminder
    and cron job.

    Args:
        user_id: Workspace owner.
        cron_expr: Standard 5-field cron (``minute hour day month weekday``).
            Defaults to ``"0 9 * * 1"`` (Monday 09:00).
        channel: Delivery channel — ``all`` / ``sms`` / ``discord`` /
            ``teamwork``.
        timezone: Optional IANA timezone (e.g. ``America/Los_Angeles``).
            Defaults to the user's configured timezone.

    Returns the dict from ``scheduler_service.create_schedule``, which
    includes the created schedule id on success.
    """
    from prax.services import scheduler_service

    description = "Library health check"
    prompt = (
        "Run the library health check and report the findings to the user. "
        "Use `library_health_check` to execute the audit, then summarize: "
        "1) total note count, 2) dead wikilinks (with paths), 3) orphan "
        "notes, 4) LLM-flagged contradictions / unsourced claims / gap "
        "topics. If everything is clean, say so in one sentence. Keep the "
        "summary under 200 words — the full report is auto-saved to "
        "library/outputs/ for later review."
    )

    return scheduler_service.create_schedule(
        user_id=user_id,
        description=description,
        prompt=prompt,
        cron_expr=cron_expr,
        timezone=timezone,
        channel=channel,
    )


def _persist_health_report(user_id: str, report: dict) -> None:
    """Write the health-check report to outputs/ for later review."""
    try:
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        lines = [
            f"# Library health check — {date_str}",
            "",
            f"_Generated: {report.get('generated_at')}_",
            "",
            "## Static checks",
            "",
            f"- Total notes: {report['static'].get('note_count', 0)}",
            f"- Dead wikilinks: {len(report['static'].get('dead_wikilinks', []))}",
            f"- Empty notebooks: {len(report['static'].get('empty_notebooks', []))}",
            f"- Orphan notes: {len(report['static'].get('orphans', []))}",
            f"- Short notes (<50 words): {len(report['static'].get('short_notes', []))}",
            "",
            "### Dead wikilinks",
        ]
        for dl in report["static"].get("dead_wikilinks", [])[:20]:
            lines.append(
                f"- `[[{dl.get('dead_target')}]]` in "
                f"{dl.get('source_project')}/{dl.get('source_notebook')}/"
                f"{dl.get('source_slug')}"
            )
        lines.append("")
        lines.append("## LLM analysis")
        lines.append("")
        llm = report.get("llm", {})
        if "error" in llm or "skipped" in llm:
            lines.append(f"_{llm.get('error') or llm.get('reason', 'skipped')}_")
        else:
            if llm.get("contradictions"):
                lines.append("### Contradictions")
                for c in llm["contradictions"]:
                    lines.append(
                        f"- **{c.get('note_a')}** vs **{c.get('note_b')}**: "
                        f"{c.get('issue')}"
                    )
                lines.append("")
            if llm.get("unsourced"):
                lines.append("### Unsourced claims")
                for u in llm["unsourced"]:
                    lines.append(f"- **{u.get('note')}**: {u.get('claim')}")
                lines.append("")
            if llm.get("gaps"):
                lines.append("### Gap topics")
                for g in llm["gaps"]:
                    mentioned = ", ".join(g.get("mentioned_in", []))
                    lines.append(f"- **{g.get('topic')}** — mentioned in {mentioned}")
                lines.append("")
        write_output(
            user_id,
            title=f"Health check {date_str}",
            content="\n".join(lines) + "\n",
            kind="health-check",
        )
    except Exception:
        logger.exception("library: failed to persist health report")


# ---------------------------------------------------------------------------
# Flashcards — per-space decks stored in flashcards.yaml
# ---------------------------------------------------------------------------

FLASHCARDS_FILE = "flashcards.yaml"


def _flashcards_path(user_id: str, space: str) -> Path:
    return _space_path(user_id, space) / FLASHCARDS_FILE


def _load_flashcards(user_id: str, space: str) -> dict:
    """Load the flashcards.yaml for a space, returning {'decks': [...]}."""
    path = _flashcards_path(user_id, space)
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "decks" in data:
                return data
        except yaml.YAMLError:
            logger.warning("library: corrupt flashcards.yaml in %s/%s", user_id, space)
    return {"decks": []}


def _save_flashcards(user_id: str, space: str, data: dict) -> None:
    """Write the flashcards.yaml for a space."""
    path = _flashcards_path(user_id, space)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def list_flashcard_decks(user_id: str, space: str) -> list[dict]:
    """List all flashcard decks in a space (without card bodies)."""
    data = _load_flashcards(user_id, space)
    result = []
    for deck in data["decks"]:
        result.append({
            "slug": deck["slug"],
            "title": deck["title"],
            "created_at": deck.get("created_at"),
            "updated_at": deck.get("updated_at"),
            "card_count": len(deck.get("cards", [])),
        })
    return result


def get_flashcard_deck(user_id: str, space: str, deck_slug: str) -> dict | None:
    """Get a specific deck with all its cards."""
    data = _load_flashcards(user_id, space)
    for deck in data["decks"]:
        if deck["slug"] == deck_slug:
            return deck
    return None


def create_flashcard_deck(
    user_id: str, space: str, title: str, slug: str | None = None,
) -> dict:
    """Create a new empty flashcard deck."""
    data = _load_flashcards(user_id, space)
    deck_slug = slug or _slugify(title)
    # Check for duplicate slug
    for deck in data["decks"]:
        if deck["slug"] == deck_slug:
            return {"error": f"Deck '{deck_slug}' already exists"}
    now = _now_iso()
    new_deck: dict[str, Any] = {
        "slug": deck_slug,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "cards": [],
    }
    data["decks"].append(new_deck)
    _save_flashcards(user_id, space, data)
    return new_deck


def delete_flashcard_deck(user_id: str, space: str, deck_slug: str) -> bool:
    """Delete a deck and all its cards. Returns True if found and deleted."""
    data = _load_flashcards(user_id, space)
    original_len = len(data["decks"])
    data["decks"] = [d for d in data["decks"] if d["slug"] != deck_slug]
    if len(data["decks"]) == original_len:
        return False
    _save_flashcards(user_id, space, data)
    return True


def add_flashcard(
    user_id: str,
    space: str,
    deck_slug: str,
    front: str,
    back: str,
    tags: list[str] | None = None,
) -> dict:
    """Add a card to a deck. Returns the new card dict or error dict."""
    data = _load_flashcards(user_id, space)
    for deck in data["decks"]:
        if deck["slug"] == deck_slug:
            now = _now_iso()
            card: dict[str, Any] = {
                "id": uuid.uuid4().hex[:8],
                "front": front,
                "back": back,
                "tags": tags or [],
                "created_at": now,
                "last_reviewed": None,
                "confidence": 0,
            }
            deck.setdefault("cards", []).append(card)
            deck["updated_at"] = now
            _save_flashcards(user_id, space, data)
            return card
    return {"error": f"Deck '{deck_slug}' not found"}


def update_flashcard(
    user_id: str,
    space: str,
    deck_slug: str,
    card_id: str,
    front: str | None = None,
    back: str | None = None,
    tags: list[str] | None = None,
    confidence: int | None = None,
    last_reviewed: str | None = None,
) -> dict | None:
    """Update a card's fields. Returns updated card or None if not found."""
    data = _load_flashcards(user_id, space)
    for deck in data["decks"]:
        if deck["slug"] == deck_slug:
            for card in deck.get("cards", []):
                if card["id"] == card_id:
                    if front is not None:
                        card["front"] = front
                    if back is not None:
                        card["back"] = back
                    if tags is not None:
                        card["tags"] = tags
                    if confidence is not None:
                        card["confidence"] = confidence
                    if last_reviewed is not None:
                        card["last_reviewed"] = last_reviewed
                    deck["updated_at"] = _now_iso()
                    _save_flashcards(user_id, space, data)
                    return card
            return None
    return None


def delete_flashcard(
    user_id: str, space: str, deck_slug: str, card_id: str,
) -> bool:
    """Delete a card from a deck. Returns True if found and deleted."""
    data = _load_flashcards(user_id, space)
    for deck in data["decks"]:
        if deck["slug"] == deck_slug:
            original_len = len(deck.get("cards", []))
            deck["cards"] = [
                c for c in deck.get("cards", []) if c["id"] != card_id
            ]
            if len(deck["cards"]) == original_len:
                return False
            deck["updated_at"] = _now_iso()
            _save_flashcards(user_id, space, data)
            return True
    return False


def add_flashcards_bulk(
    user_id: str,
    space: str,
    deck_slug: str,
    cards: list[dict],
) -> list[dict]:
    """Add multiple cards at once. Each dict has front, back, tags.

    Returns list of created card dicts, or a single-element list with
    an error dict if the deck is not found.
    """
    data = _load_flashcards(user_id, space)
    for deck in data["decks"]:
        if deck["slug"] == deck_slug:
            now = _now_iso()
            created = []
            for item in cards:
                card: dict[str, Any] = {
                    "id": uuid.uuid4().hex[:8],
                    "front": item.get("front", ""),
                    "back": item.get("back", ""),
                    "tags": item.get("tags") or [],
                    "created_at": now,
                    "last_reviewed": None,
                    "confidence": 0,
                }
                deck.setdefault("cards", []).append(card)
                created.append(card)
            deck["updated_at"] = now
            _save_flashcards(user_id, space, data)
            return created
    return [{"error": f"Deck '{deck_slug}' not found"}]


# ---------------------------------------------------------------------------
# Space files — per-space file store for reference material
# ---------------------------------------------------------------------------

_FILES_DIR = "files"


def _sanitize_filename(filename: str) -> str:
    """Strip path traversal characters and collapse to a flat filename."""
    # Remove any directory components and null bytes
    name = filename.replace("\x00", "")
    name = Path(name).name  # strips leading dirs / ..
    # Extra safety: remove any remaining .. or /
    name = name.replace("..", "").replace("/", "").replace("\\", "")
    return name.strip() or "unnamed"


def _files_dir(user_id: str, space: str) -> Path:
    return _space_path(user_id, space) / _FILES_DIR


def list_space_files(user_id: str, space: str) -> list[dict]:
    """List all uploaded files in a space.

    Returns ``[{name, size, mime_type, uploaded_at}]``.
    """
    d = _files_dir(user_id, space)
    if not d.exists():
        return []
    results: list[dict] = []
    for p in sorted(d.iterdir()):
        if not p.is_file():
            continue
        mt, _ = mimetypes.guess_type(p.name)
        results.append({
            "name": p.name,
            "size": p.stat().st_size,
            "mime_type": mt or "application/octet-stream",
            "uploaded_at": datetime.fromtimestamp(
                p.stat().st_mtime, tz=UTC,
            ).isoformat(),
        })
    return results


def save_space_file(
    user_id: str,
    space: str,
    filename: str,
    data: bytes,
    mime_type: str = "",
) -> dict:
    """Save an uploaded file to the space's files directory.

    Returns file metadata on success.
    """
    proj_dir = _space_path(user_id, space)
    if not proj_dir.exists():
        return {"error": f"Space '{space}' not found"}
    safe_name = _sanitize_filename(filename)
    d = _files_dir(user_id, space)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / safe_name
    dest.write_bytes(data)
    mt = mime_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    logger.info(
        "library: saved file %s for space %s (%s, %d bytes)",
        safe_name, space, mt, len(data),
    )
    return {
        "name": safe_name,
        "size": len(data),
        "mime_type": mt,
        "uploaded_at": datetime.fromtimestamp(
            dest.stat().st_mtime, tz=UTC,
        ).isoformat(),
    }


def get_space_file(
    user_id: str, space: str, filename: str,
) -> tuple[Path, str] | None:
    """Return ``(path, mime_type)`` for a stored file, or ``None``."""
    safe_name = _sanitize_filename(filename)
    p = _files_dir(user_id, space) / safe_name
    if not p.is_file():
        return None
    mt, _ = mimetypes.guess_type(safe_name)
    return p, mt or "application/octet-stream"


def delete_space_file(user_id: str, space: str, filename: str) -> bool:
    """Delete a file from the space. Returns ``True`` if deleted."""
    safe_name = _sanitize_filename(filename)
    p = _files_dir(user_id, space) / safe_name
    if not p.is_file():
        return False
    p.unlink()
    logger.info("library: deleted file %s from space %s", safe_name, space)
    return True
