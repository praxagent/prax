"""Course/tutor service — persistent course state in the user workspace.

Stores courses under ``workspace_root/courses/<course_id>/``:

    courses/
      python_fundamentals/
        course.yaml       — metadata, plan, progress, assessment
        tutor_notes.md    — Prax's private observations about the student
        materials/        — generated quizzes, lesson summaries, etc.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

import yaml

from prax.services.workspace_service import (
    ensure_workspace,
    get_lock,
    git_commit,
    safe_join,
)
from prax.utils.text import slugify

logger = logging.getLogger(__name__)

_COURSE_FILE = "course.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    return slugify(text, separator="_", fallback="course")


# Re-exported from hugo_publishing — kept here for back-compat.
from prax.services.hugo_publishing import courses_dir  # noqa: E402, F401


def _course_dir(root: str, course_id: str) -> str:
    return safe_join(courses_dir(root), course_id)


def _read_course(course_path: str) -> dict:
    yaml_path = os.path.join(course_path, _COURSE_FILE)
    if not os.path.isfile(yaml_path):
        raise FileNotFoundError(f"Course not found: {course_path}")
    with open(yaml_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_course(course_path: str, data: dict) -> None:
    data["updated_at"] = datetime.now(UTC).isoformat()
    yaml_path = os.path.join(course_path, _COURSE_FILE)
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_course(user_id: str, subject: str, title: str = "") -> dict:
    """Create a new course and return its initial metadata."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        course_id = _slugify(title or subject)

        # Deduplicate if slug already exists.
        base_id = course_id
        counter = 2
        while os.path.isdir(os.path.join(courses_dir(root), course_id)):
            course_id = f"{base_id}_{counter}"
            counter += 1

        course_path = _course_dir(root, course_id)
        os.makedirs(course_path, exist_ok=True)
        os.makedirs(os.path.join(course_path, "materials"), exist_ok=True)

        now = datetime.now(UTC).isoformat()
        data = {
            "id": course_id,
            "title": title or subject,
            "subject": subject,
            "status": "assessing",
            "level": None,
            "created_at": now,
            "updated_at": now,
            "assessment": {
                "questions": [],
                "answers": [],
                "determined_level": None,
            },
            "plan": {
                "modules": [],
                "current_module": 0,
            },
            "progress": {
                "modules_completed": 0,
                "total_modules": 0,
                "pace": "normal",
            },
        }
        _write_course(course_path, data)
        git_commit(root, f"Create course: {data['title']}")
        return data


def get_course(user_id: str, course_id: str) -> dict:
    """Read a course's full metadata."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        data = _read_course(_course_dir(root, course_id))
    try:
        from prax.services import access_log
        access_log.touch(user_id, "course", course_id)
    except Exception:
        pass
    return data


def list_courses(user_id: str) -> list[dict]:
    """List all courses with summary info.

    Sorted by most-recently-accessed first, then created_at desc.
    """
    from prax.services import access_log

    with get_lock(user_id):
        root = ensure_workspace(user_id)
        courses_root = courses_dir(root)
        results = []
        if not os.path.isdir(courses_root):
            return results
        access_map = access_log.get_all(user_id, "course")
        for entry in os.listdir(courses_root):
            course_path = os.path.join(courses_root, entry)
            if not os.path.isdir(course_path):
                continue
            try:
                data = _read_course(course_path)
                results.append({
                    "id": data["id"],
                    "title": data["title"],
                    "subject": data["subject"],
                    "status": data["status"],
                    "level": data.get("level"),
                    "progress": data.get("progress", {}),
                    "created_at": data.get("created_at", ""),
                    "accessed_at": access_map.get(data["id"], ""),
                })
            except Exception:
                continue
        results.sort(
            key=lambda c: (c["accessed_at"], c.get("created_at", "")),
            reverse=True,
        )
        return results


def update_course(user_id: str, course_id: str, updates: dict) -> dict:
    """Merge *updates* into course.yaml and return the full updated data."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        course_path = _course_dir(root, course_id)
        data = _read_course(course_path)
        _deep_merge(data, updates)
        _write_course(course_path, data)
        git_commit(root, f"Update course: {data['title']}")
        return data


def save_tutor_notes(user_id: str, course_id: str, content: str) -> str:
    """Write Prax's private tutor notes for a course."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        course_path = _course_dir(root, course_id)
        if not os.path.isdir(course_path):
            raise FileNotFoundError(f"Course not found: {course_id}")
        notes_path = os.path.join(course_path, "tutor_notes.md")
        with open(notes_path, "w", encoding="utf-8") as f:
            f.write(content)
        git_commit(root, f"Update tutor notes: {course_id}")
        return notes_path


def read_tutor_notes(user_id: str, course_id: str) -> str:
    """Read Prax's private tutor notes for a course."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        course_path = _course_dir(root, course_id)
        notes_path = os.path.join(course_path, "tutor_notes.md")
        if not os.path.isfile(notes_path):
            return ""
        with open(notes_path, encoding="utf-8") as f:
            return f.read()


def save_material(
    user_id: str, course_id: str, filename: str, content: str,
) -> str:
    """Save a course material file (quiz, lesson summary, etc.)."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        course_path = _course_dir(root, course_id)
        if not os.path.isfile(os.path.join(course_path, _COURSE_FILE)):
            raise FileNotFoundError(f"Course not found: {course_id}")
        materials_dir = os.path.join(course_path, "materials")
        os.makedirs(materials_dir, exist_ok=True)
        filepath = safe_join(materials_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        git_commit(root, f"Save material: {course_id}/{filename}")
        return filepath


def read_material(user_id: str, course_id: str, filename: str) -> str:
    """Read a course material file."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        course_path = _course_dir(root, course_id)
        filepath = safe_join(os.path.join(course_path, "materials"), filename)
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"Material not found: {course_id}/{filename}")
        with open(filepath, encoding="utf-8") as f:
            return f.read()


# ---------------------------------------------------------------------------
# Hugo blog publishing
# ---------------------------------------------------------------------------

# Hugo publishing primitives moved to prax.services.hugo_publishing in
# Phase 5.  They are re-exported here so existing callers keep working.
from prax.services.hugo_publishing import (  # noqa: F401
    KATEX_HEAD,
    THEME_CSS,
    ensure_hugo_site,
    hugo_site_dir,
)


def generate_hugo_content(root: str, course_id: str) -> None:
    """Generate Hugo markdown posts from a course's data and materials."""
    course_path = _course_dir(root, course_id)
    data = _read_course(course_path)

    site = hugo_site_dir(root)
    content_dir = os.path.join(site, "content", course_id)
    os.makedirs(content_dir, exist_ok=True)

    # Course index page.
    plan = data.get("plan", {})
    modules = plan.get("modules", [])
    progress = data.get("progress", {})
    done = progress.get("modules_completed", 0)
    total = progress.get("total_modules", 0) or len(modules)
    pct = int(done / total * 100) if total else 0

    index_content = f"""---
title: "{data['title']}"
---

**Subject:** {data['subject']}
**Level:** {data.get('level') or 'TBD'}
**Status:** {data['status']}
**Progress:** {done}/{total} modules

<div class="progress"><div class="progress-bar" style="width:{pct}%"></div></div>
"""
    with open(os.path.join(content_dir, "_index.md"), "w", encoding="utf-8") as f:
        f.write(index_content)

    # Module pages.
    materials_dir = os.path.join(course_path, "materials")
    for m in modules:
        num = m["number"]
        title = m["title"]
        status = m.get("status", "pending")
        topics = m.get("topics", [])

        # Look for a matching material file.  Prefer exact names first,
        # then fall back to any file starting with "module_{num}".
        material_body = ""
        preferred = [f"module_{num}.md", f"module_{num}_lesson.md"]
        for candidate in preferred:
            mat_path = os.path.join(materials_dir, candidate)
            if os.path.isfile(mat_path):
                with open(mat_path, encoding="utf-8") as f:
                    material_body = f.read()
                break

        if not material_body and os.path.isdir(materials_dir):
            # Glob for any module_{num}_*.md file.
            prefix = f"module_{num}_"
            for fname in sorted(os.listdir(materials_dir)):
                if fname.startswith(prefix) and fname.endswith(".md"):
                    with open(os.path.join(materials_dir, fname), encoding="utf-8") as f:
                        material_body = f.read()
                    break

        if not material_body and topics:
            material_body = "**Topics:** " + ", ".join(topics)

        page = f"""---
title: "{title}"
module_number: {num}
status: "{status}"
weight: {num}
---

{material_body}
"""
        slug = slugify(title)
        with open(os.path.join(content_dir, f"{num:02d}-{slug}.md"), "w", encoding="utf-8") as f:
            f.write(page)


# run_hugo is re-exported from hugo_publishing for back-compat.
from prax.services.hugo_publishing import run_hugo  # noqa: E402, F401


def build_course_site(user_id: str, course_id: str, base_url: str) -> dict:
    """Generate Hugo content for a course and rebuild the whole site.

    All courses are sections in a single Hugo site — one build serves them all.
    Returns dict with 'url' or 'error'.
    """
    with get_lock(user_id):
        root = ensure_workspace(user_id)

        # Validate course exists.
        course_path = _course_dir(root, course_id)
        _read_course(course_path)  # raises if not found

        site = ensure_hugo_site(root, base_url)

        # Regenerate content for ALL courses so the site is always complete.
        courses_root = courses_dir(root)
        for entry in sorted(os.listdir(courses_root)):
            if entry.startswith("_"):
                continue  # skip _site
            cp = os.path.join(courses_root, entry)
            if os.path.isdir(cp) and os.path.isfile(os.path.join(cp, _COURSE_FILE)):
                generate_hugo_content(root, entry)

        git_commit(root, f"Generate Hugo content: {course_id}")

    err = run_hugo(site)
    if err:
        return err

    url = f"{base_url.rstrip('/')}/courses/{course_id}/"
    return {"url": url, "public_dir": os.path.join(site, "public")}


# get_course_site_public_dir + find_course_site_public_dir are
# re-exported from hugo_publishing for back-compat.
from prax.services.hugo_publishing import (  # noqa: E402, F401
    find_course_site_public_dir,
    get_course_site_public_dir,
)

# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, updates: dict) -> None:
    """Recursively merge *updates* into *base* in-place."""
    for key, value in updates.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ---------------------------------------------------------------------------
# Deprecated aliases — old underscore-prefixed names kept for backward compat.
# New code should import the public names above.
# ---------------------------------------------------------------------------
_KATEX_HEAD = KATEX_HEAD
_THEME_CSS = THEME_CSS
_hugo_site_dir = hugo_site_dir
_courses_dir = courses_dir
_ensure_hugo_site = ensure_hugo_site
_generate_hugo_content = generate_hugo_content
_run_hugo = run_hugo
