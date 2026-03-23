"""Note service — persistent notes published as Hugo pages.

Stores notes under ``workspace_root/notes/<slug>.md`` with YAML front-matter.
Hugo content is generated into the shared course site at
``workspace_root/courses/_site/content/notes/``.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime

import yaml

from prax.services.workspace_service import (
    _ensure_workspace,
    _get_lock,
    _git_commit,
    _safe_join,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "note"


def _notes_dir(root: str) -> str:
    d = os.path.join(root, "notes")
    os.makedirs(d, exist_ok=True)
    return d


def _note_path(root: str, slug: str) -> str:
    return _safe_join(_notes_dir(root), f"{slug}.md")


def _parse_note(path: str) -> dict:
    """Read a note file and return metadata + content."""
    with open(path, encoding="utf-8") as f:
        raw = f.read()

    # Parse YAML front-matter.
    meta: dict = {}
    content = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1]) or {}
            content = parts[2].strip()

    meta.setdefault("slug", os.path.splitext(os.path.basename(path))[0])
    meta.setdefault("title", meta["slug"])
    meta.setdefault("tags", [])
    meta.setdefault("created_at", "")
    meta.setdefault("updated_at", "")
    meta["content"] = content
    return meta


def _write_note(path: str, title: str, content: str, tags: list[str],
                created_at: str | None = None) -> dict:
    now = datetime.now(UTC).isoformat()
    meta = {
        "title": title,
        "tags": tags,
        "created_at": created_at or now,
        "updated_at": now,
    }
    front = yaml.dump(meta, default_flow_style=False, sort_keys=False, allow_unicode=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"---\n{front}---\n\n{content}\n")
    meta["slug"] = os.path.splitext(os.path.basename(path))[0]
    meta["content"] = content
    return meta


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_note(user_id: str, title: str, content: str,
                tags: list[str] | None = None) -> dict:
    """Create a new note. Returns note metadata including slug."""
    with _get_lock(user_id):
        root = _ensure_workspace(user_id)
        slug = _slugify(title)

        # Deduplicate slug.
        base_slug = slug
        counter = 2
        while os.path.isfile(_note_path(root, slug)):
            slug = f"{base_slug}-{counter}"
            counter += 1

        path = _note_path(root, slug)
        meta = _write_note(path, title, content, tags or [])
        _git_commit(root, f"Create note: {title}")
        return meta


def update_note(user_id: str, slug: str, content: str | None = None,
                title: str | None = None, tags: list[str] | None = None) -> dict:
    """Update an existing note. Only provided fields are changed."""
    with _get_lock(user_id):
        root = _ensure_workspace(user_id)
        path = _note_path(root, slug)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Note not found: {slug}")
        existing = _parse_note(path)
        meta = _write_note(
            path,
            title=title if title is not None else existing["title"],
            content=content if content is not None else existing["content"],
            tags=tags if tags is not None else existing["tags"],
            created_at=existing["created_at"],
        )
        _git_commit(root, f"Update note: {meta['title']}")
        return meta


def get_note(user_id: str, slug: str) -> dict:
    """Read a single note."""
    with _get_lock(user_id):
        root = _ensure_workspace(user_id)
        path = _note_path(root, slug)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Note not found: {slug}")
        return _parse_note(path)


def list_notes(user_id: str) -> list[dict]:
    """List all notes with metadata (no content)."""
    with _get_lock(user_id):
        root = _ensure_workspace(user_id)
        notes_root = _notes_dir(root)
        results = []
        for fname in sorted(os.listdir(notes_root)):
            if not fname.endswith(".md"):
                continue
            try:
                meta = _parse_note(os.path.join(notes_root, fname))
                results.append({
                    "slug": meta["slug"],
                    "title": meta["title"],
                    "tags": meta["tags"],
                    "created_at": meta["created_at"],
                    "updated_at": meta["updated_at"],
                })
            except Exception:
                continue
        return results


def search_notes(user_id: str, query: str) -> list[dict]:
    """Search notes by title, tags, and content. Returns matching notes."""
    with _get_lock(user_id):
        root = _ensure_workspace(user_id)
        notes_root = _notes_dir(root)
        query_lower = query.lower()
        results = []
        for fname in sorted(os.listdir(notes_root)):
            if not fname.endswith(".md"):
                continue
            try:
                meta = _parse_note(os.path.join(notes_root, fname))
                searchable = (
                    meta["title"].lower()
                    + " " + " ".join(meta["tags"]).lower()
                    + " " + meta["content"].lower()
                )
                if query_lower in searchable:
                    # Include a content snippet around the match.
                    idx = meta["content"].lower().find(query_lower)
                    snippet = ""
                    if idx >= 0:
                        start = max(0, idx - 80)
                        end = min(len(meta["content"]), idx + len(query) + 80)
                        snippet = "…" + meta["content"][start:end].strip() + "…"
                    results.append({
                        "slug": meta["slug"],
                        "title": meta["title"],
                        "tags": meta["tags"],
                        "snippet": snippet,
                    })
            except Exception:
                continue
        return results


# ---------------------------------------------------------------------------
# Hugo integration
# ---------------------------------------------------------------------------

from prax.services.course_service import _KATEX_HEAD, _THEME_CSS  # noqa: E402

_HUGO_NOTES_LIST = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ .Title }}</title>
<style>
""" + _THEME_CSS + """\
  .search { width: 100%; padding: 0.6rem; font-size: 1rem; border: 1px solid var(--border); border-radius: 4px; margin-bottom: 1.5rem; box-sizing: border-box; background: var(--bg); color: var(--text); }
  .note { padding: 0.8rem 0; border-bottom: 1px solid var(--border-light); }
  .note a { text-decoration: none; font-weight: 600; }
  .note .tags { font-size: 0.85em; color: var(--text-muted); margin-top: 0.2rem; }
  .note .tags span { background: var(--tag-bg); padding: 0.1rem 0.5rem; border-radius: 3px; margin-right: 0.3rem; }
  .note .date { font-size: 0.8em; color: var(--text-muted); }
  .empty { color: var(--text-muted); font-style: italic; display: none; }
</style>
</head>
<body>
<h1>{{ .Title }}</h1>
<input type="text" class="search" placeholder="Search notes..." id="search">
<div id="notes">
{{ range .Pages.ByLastmod.Reverse }}
<div class="note" data-search="{{ lower .Title }} {{ delimit (.Params.tags | default slice) " " | lower }}">
  <a href="{{ .RelPermalink }}">{{ .Title }}</a>
  {{ with .Params.tags }}<div class="tags">{{ range . }}<span>{{ . }}</span>{{ end }}</div>{{ end }}
  <div class="date">{{ .Params.updated_at }}</div>
</div>
{{ end }}
</div>
<div class="empty" id="empty">No matching notes.</div>
<script>
document.getElementById('search').addEventListener('input', function() {
  var q = this.value.toLowerCase();
  var notes = document.querySelectorAll('.note');
  var visible = 0;
  notes.forEach(function(n) {
    var match = !q || n.dataset.search.includes(q);
    n.style.display = match ? '' : 'none';
    if (match) visible++;
  });
  document.getElementById('empty').style.display = visible ? 'none' : 'block';
});
</script>
</body>
</html>
"""

_HUGO_NOTES_SINGLE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ .Title }}</title>
""" + _KATEX_HEAD + """\
<style>
""" + _THEME_CSS + """\
  .meta .tags span { background: var(--tag-bg); padding: 0.1rem 0.5rem; border-radius: 3px; margin-right: 0.3rem; font-size: 0.85em; }
</style>
</head>
<body>
<a href="{{ .CurrentSection.RelPermalink }}">&larr; All Notes</a>
<h1>{{ .Title }}</h1>
<div class="meta">
  {{ with .Params.tags }}<div class="tags">{{ range . }}<span>{{ . }}</span>{{ end }}</div>{{ end }}
  {{ with .Params.updated_at }}Last updated: {{ . }}{{ end }}
</div>
{{ .Content }}
</body>
</html>
"""


def _generate_hugo_notes(root: str) -> None:
    """Generate Hugo content files for all notes in the workspace."""
    from prax.services.course_service import _hugo_site_dir

    site = _hugo_site_dir(root)
    notes_content_dir = os.path.join(site, "content", "notes")
    os.makedirs(notes_content_dir, exist_ok=True)

    # Write section index.
    index_path = os.path.join(notes_content_dir, "_index.md")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write('---\ntitle: "Notes"\n---\n')

    # Write notes layout templates.
    notes_layout_dir = os.path.join(site, "layouts", "notes")
    os.makedirs(notes_layout_dir, exist_ok=True)
    with open(os.path.join(notes_layout_dir, "list.html"), "w", encoding="utf-8") as f:
        f.write(_HUGO_NOTES_LIST)
    with open(os.path.join(notes_layout_dir, "single.html"), "w", encoding="utf-8") as f:
        f.write(_HUGO_NOTES_SINGLE)

    # Generate a page for each note.
    notes_root = _notes_dir(root)
    for fname in sorted(os.listdir(notes_root)):
        if not fname.endswith(".md"):
            continue
        try:
            meta = _parse_note(os.path.join(notes_root, fname))
        except Exception:
            continue

        tags_yaml = yaml.dump(meta["tags"], default_flow_style=True).strip()
        page = (
            f'---\n'
            f'title: "{meta["title"]}"\n'
            f'tags: {tags_yaml}\n'
            f'updated_at: "{meta["updated_at"]}"\n'
            f'date: "{meta["created_at"]}"\n'
            f'---\n\n'
            f'{meta["content"]}\n'
        )
        slug = meta["slug"]
        with open(os.path.join(notes_content_dir, f"{slug}.md"), "w", encoding="utf-8") as f:
            f.write(page)


def publish_notes(user_id: str, base_url: str, slug: str | None = None) -> dict:
    """Generate Hugo content for notes and rebuild the site.

    Returns dict with 'url' or 'error'.
    """
    from prax.services.course_service import (
        _courses_dir,
        _ensure_hugo_site,
        _generate_hugo_content,
        _hugo_site_dir,
        _run_hugo,
    )

    with _get_lock(user_id):
        root = _ensure_workspace(user_id)
        site = _ensure_hugo_site(root, base_url)

        # Generate notes content.
        _generate_hugo_notes(root)

        # Also regenerate all course content so the site stays complete.
        courses_root = _courses_dir(root)
        for entry in sorted(os.listdir(courses_root)):
            if entry.startswith("_"):
                continue
            cp = os.path.join(courses_root, entry)
            yaml_path = os.path.join(cp, "course.yaml")
            if os.path.isdir(cp) and os.path.isfile(yaml_path):
                _generate_hugo_content(root, entry)

        _git_commit(root, f"Publish notes{': ' + slug if slug else ''}")

    err = _run_hugo(site if 'site' in dir() else _hugo_site_dir(root))
    if err:
        return err

    url_slug = f"notes/{slug}/" if slug else "notes/"
    url = f"{base_url.rstrip('/')}/{url_slug}"
    return {"url": url}
