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


def courses_dir(root: str) -> str:
    d = os.path.join(root, "courses")
    os.makedirs(d, exist_ok=True)
    return d


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

_HUGO_CONFIG = """\
baseURL = "{base_url}"
languageCode = "en-us"
title = "{site_title}"

[markup]
  [markup.goldmark]
    [markup.goldmark.renderer]
      unsafe = true

[params]
  description = "Personal courses by Prax"
"""

KATEX_HEAD = """\
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body,{delimiters:[
    {left:'$$',right:'$$',display:true},
    {left:'$',right:'$',display:false},
    {left:'\\\\[',right:'\\\\]',display:true},
    {left:'\\\\(',right:'\\\\)',display:false}
  ]});"></script>
<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
  // Hugo renders ```mermaid as <pre><code class="language-mermaid">.
  // Convert these to <div class="mermaid"> so mermaid.js can process them.
  document.querySelectorAll('code.language-mermaid').forEach(el => {
    const div = document.createElement('div');
    div.className = 'mermaid';
    div.textContent = el.textContent;
    el.closest('pre').replaceWith(div);
  });
  mermaid.initialize({startOnLoad:true, theme:'default'});
</script>
"""

THEME_CSS = """\
  :root {
    --bg: #ffffff; --text: #1a1a1a; --text-muted: #666; --link: #0056b3;
    --code-bg: #f4f4f4; --border: #ddd; --border-light: #eee;
    --blockquote-bg: #f0f7ff; --blockquote-border: #0056b3;
    --table-header: #f4f4f4; --table-stripe: #fafafa;
    --tag-bg: #e9ecef; --progress-bg: #e9ecef;
    --h1-border: #333;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1a1a2e; --text: #e0e0e0; --text-muted: #999; --link: #6db3f2;
      --code-bg: #2a2a3e; --border: #444; --border-light: #333;
      --blockquote-bg: #1e2a3a; --blockquote-border: #6db3f2;
      --table-header: #2a2a3e; --table-stripe: #222238;
      --tag-bg: #333348; --progress-bg: #333348;
      --h1-border: #888;
    }
    img { opacity: 0.9; }
  }
  body { max-width: 48rem; margin: 2rem auto; padding: 0 1rem; font-family: system-ui, sans-serif; line-height: 1.6; color: var(--text); background: var(--bg); }
  h1 { border-bottom: 2px solid var(--h1-border); padding-bottom: .3rem; }
  h2 { margin-top: 2rem; }
  pre { background: var(--code-bg); padding: 1rem; overflow-x: auto; border-radius: 4px; }
  code { background: var(--code-bg); padding: 0.15rem 0.3rem; border-radius: 3px; font-size: 0.9em; }
  pre code { background: none; padding: 0; }
  a { color: var(--link); }
  blockquote { border-left: 4px solid var(--blockquote-border); margin: 1.5rem 0; padding: 1rem 1.2rem; background: var(--blockquote-bg); border-radius: 0 4px 4px 0; }
  blockquote strong { color: var(--blockquote-border); }
  .mermaid { margin: 1.5rem 0; text-align: center; }
  table { border-collapse: collapse; width: 100%; margin: 1.5rem 0; }
  th, td { border: 1px solid var(--border); padding: 0.6rem 0.8rem; text-align: left; }
  th { background: var(--table-header); font-weight: 600; }
  tr:nth-child(even) { background: var(--table-stripe); }
  .meta { color: var(--text-muted); font-size: 0.9em; margin-bottom: 2rem; }
  .progress { background: var(--progress-bg); border-radius: 4px; overflow: hidden; height: 8px; margin: 1rem 0; }
  .progress-bar { background: #28a745; height: 100%; }
  img { max-width: 100%; height: auto; }
"""

_HUGO_SINGLE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ .Title }}</title>
""" + KATEX_HEAD + """\
<style>
""" + THEME_CSS + """\
  .nav { margin: 2rem 0; padding: 1rem 0; border-top: 1px solid var(--border); display: flex; justify-content: space-between; }
</style>
</head>
<body>
<a href="{{ .CurrentSection.RelPermalink }}">&larr; Course Home</a>
<h1>{{ .Title }}</h1>
{{ with .Params.module_number }}<div class="meta">Module {{ . }}{{ with $.Params.status }} &middot; {{ . }}{{ end }}</div>{{ end }}
{{ .Content }}
<div class="nav">
  {{ with .NextInSection }}<a href="{{ .RelPermalink }}">&larr; {{ .Title }}</a>{{ else }}<span></span>{{ end }}
  {{ with .PrevInSection }}<a href="{{ .RelPermalink }}">{{ .Title }} &rarr;</a>{{ else }}<span></span>{{ end }}
</div>
</body>
</html>
"""

_HUGO_LIST = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ .Title }}</title>
""" + KATEX_HEAD + """\
<style>
""" + THEME_CSS + """\
  .module { padding: 0.8rem 0; border-bottom: 1px solid var(--border-light); }
  .module a { text-decoration: none; font-weight: 600; }
  .module .status { font-size: 0.85em; color: var(--text-muted); }
</style>
</head>
<body>
<h1>{{ .Title }}</h1>
{{ .Content }}
{{ range .Pages.ByParam "module_number" }}
<div class="module">
  <a href="{{ .RelPermalink }}">Module {{ .Params.module_number }}: {{ .Title }}</a>
  <div class="status">{{ with .Params.status }}{{ . }}{{ end }}</div>
</div>
{{ end }}
</body>
</html>
"""


def hugo_site_dir(root: str) -> str:
    """Return the path to the shared Hugo site directory."""
    return os.path.join(root, "courses", "_site")


def ensure_hugo_site(root: str, base_url: str) -> str:
    """Create the Hugo site skeleton if it doesn't exist. Returns site dir."""
    site = hugo_site_dir(root)
    content_dir = os.path.join(site, "content")
    layouts_dir = os.path.join(site, "layouts", "_default")
    os.makedirs(content_dir, exist_ok=True)
    os.makedirs(layouts_dir, exist_ok=True)

    # Write config.  Hugo's baseURL must include /courses/ because
    # Flask serves the built site at /courses/<path>.
    config_path = os.path.join(site, "hugo.toml")
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(_HUGO_CONFIG.format(
            base_url=base_url.rstrip("/") + "/courses/",
            site_title="Courses",
        ))

    # Write minimal theme templates.
    with open(os.path.join(layouts_dir, "single.html"), "w", encoding="utf-8") as f:
        f.write(_HUGO_SINGLE)
    with open(os.path.join(layouts_dir, "list.html"), "w", encoding="utf-8") as f:
        f.write(_HUGO_LIST)

    return site


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


def run_hugo(site: str) -> dict | None:
    """Run Hugo to build the site. Returns error dict or None on success.

    Hugo runs locally (not routed through the sandbox) because
    ensure_hugo_site writes files directly to the local filesystem.
    Routing through the sandbox caused cross-container filesystem issues.
    """
    import shutil
    import subprocess as _sp

    if not shutil.which("hugo"):
        return {"error": "Hugo is not installed — notes are saved but the web page was not rebuilt."}

    if not os.path.isdir(site):
        return {"error": f"Hugo site directory not found: {site}"}

    result = _sp.run(
        ["hugo", "--source", site, "--destination", os.path.join(site, "public")],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        return {"error": f"Hugo build failed: {result.stderr[:500]}"}
    return None


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


def get_course_site_public_dir(user_id: str) -> str | None:
    """Return the path to the Hugo public/ dir if it exists for a specific user."""
    from prax.services.workspace_service import workspace_root
    root = workspace_root(user_id)
    public = os.path.join(hugo_site_dir(root), "public")
    return public if os.path.isdir(public) else None


def find_course_site_public_dir(path: str) -> str | None:
    """Find a Hugo public/ dir that contains *path* by scanning all user workspaces.

    Course pages are public (shared via ngrok links), so we don't require
    authentication — we just need to locate which user's built site has the
    requested file.  Returns the public dir, or None.
    """
    from prax.settings import settings as _settings

    workspace_base = _settings.workspace_dir
    if not os.path.isdir(workspace_base):
        return None

    for user_dir in os.listdir(workspace_base):
        public = os.path.join(workspace_base, user_dir, "courses", "_site", "public")
        if not os.path.isdir(public):
            continue
        candidate = os.path.join(public, path)
        # Match either a file directly or a directory (will resolve to index.html).
        if os.path.exists(candidate):
            return public
        # Also check with index.html appended for directory paths.
        if os.path.isdir(candidate) or os.path.isfile(os.path.join(candidate, "index.html")):
            return public

    return None


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
