"""Note service — persistent notes published as Hugo pages.

Stores notes under ``workspace_root/notes/<slug>.md`` with YAML front-matter.
Hugo content is generated into the shared course site at
``workspace_root/courses/_site/content/notes/``.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    return slugify(text, fallback="note")


def _notes_dir(root: str) -> str:
    d = os.path.join(root, "notes")
    os.makedirs(d, exist_ok=True)
    return d


def _note_path(root: str, slug: str) -> str:
    return safe_join(_notes_dir(root), f"{slug}.md")


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
    meta.setdefault("related", [])
    meta.setdefault("created_at", "")
    meta.setdefault("updated_at", "")
    meta["content"] = content
    return meta


def _write_note(path: str, title: str, content: str, tags: list[str],
                created_at: str | None = None,
                related: list[str] | None = None) -> dict:
    now = datetime.now(UTC).isoformat()
    meta: dict = {
        "title": title,
        "tags": tags,
        "created_at": created_at or now,
        "updated_at": now,
    }
    if related:
        meta["related"] = related
    front = yaml.dump(meta, default_flow_style=False, sort_keys=False, allow_unicode=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"---\n{front}---\n\n{content}\n")
    meta["slug"] = os.path.splitext(os.path.basename(path))[0]
    meta["content"] = content
    meta.setdefault("related", [])
    return meta


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_note(user_id: str, title: str, content: str,
                tags: list[str] | None = None) -> dict:
    """Create a new note. Returns note metadata including slug."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        slug = _slugify(title)

        # Deduplicate slug.
        base_slug = slug
        counter = 2
        while os.path.isfile(_note_path(root, slug)):
            slug = f"{base_slug}-{counter}"
            counter += 1

        path = _note_path(root, slug)
        meta = _write_note(path, title, content, tags or [])
        git_commit(root, f"Create note: {title}")
        return meta


def update_note(user_id: str, slug: str, content: str | None = None,
                title: str | None = None, tags: list[str] | None = None,
                related: list[str] | None = None) -> dict:
    """Update an existing note. Only provided fields are changed."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
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
            related=related if related is not None else existing.get("related", []),
        )
        git_commit(root, f"Update note: {meta['title']}")
        return meta


def get_note(user_id: str, slug: str) -> dict:
    """Read a single note."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        path = _note_path(root, slug)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Note not found: {slug}")
        meta = _parse_note(path)
    # Touch access time (outside the lock — access log has its own I/O).
    try:
        from prax.services import access_log
        access_log.touch(user_id, "note", slug)
    except Exception:
        pass
    return meta


def delete_note(user_id: str, slug: str) -> dict:
    """Delete a note by slug. Returns metadata of the deleted note."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
        path = _note_path(root, slug)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Note not found: {slug}")
        meta = _parse_note(path)
        os.remove(path)
        git_commit(root, f"Delete note: {meta['title']}")
        return {"slug": slug, "title": meta["title"], "deleted": True}


def note_versions(user_id: str, slug: str, limit: int = 5) -> list[dict]:
    """Return up to *limit* recent versions of a note from git history."""
    import subprocess

    with get_lock(user_id):
        root = ensure_workspace(user_id)
        rel_path = os.path.join("notes", f"{slug}.md")
        abs_path = os.path.join(root, rel_path)

        # If the file doesn't exist at all, check if it ever existed.
        result = subprocess.run(
            ["git", "log", f"-{limit}", "--pretty=format:%H\t%ai\t%s", "--follow", "--", rel_path],
            cwd=root, capture_output=True, text=True,
        )
        if result.returncode != 0 or not result.stdout.strip():
            if not os.path.isfile(abs_path):
                raise FileNotFoundError(f"Note not found: {slug}")
            return []

        versions = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t", 2)
            if len(parts) < 3:
                continue
            versions.append({
                "commit": parts[0],
                "date": parts[1],
                "message": parts[2],
            })
        return versions


def get_note_version(user_id: str, slug: str, commit: str) -> dict:
    """Retrieve the content of a note at a specific git commit."""
    import subprocess

    with get_lock(user_id):
        root = ensure_workspace(user_id)
        rel_path = os.path.join("notes", f"{slug}.md")

        result = subprocess.run(
            ["git", "show", f"{commit}:{rel_path}"],
            cwd=root, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise FileNotFoundError(
                f"Version not found: {slug} at {commit[:8]}"
            )

        # Parse the old version's content.
        raw = result.stdout
        meta: dict = {}
        content = raw
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                meta = yaml.safe_load(parts[1]) or {}
                content = parts[2].strip()

        meta.setdefault("slug", slug)
        meta.setdefault("title", slug)
        meta["content"] = content
        meta["commit"] = commit
        return meta


def restore_note_version(user_id: str, slug: str, commit: str) -> dict:
    """Restore a note to a specific git version."""
    old_version = get_note_version(user_id, slug, commit)
    return update_note(
        user_id, slug,
        content=old_version.get("content", ""),
        title=old_version.get("title"),
        tags=old_version.get("tags"),
        related=old_version.get("related"),
    )


def list_notes(user_id: str) -> list[dict]:
    """List all notes with metadata (no content).

    Sorted by most-recently-accessed first, falling back to creation date.
    """
    from prax.services import access_log

    with get_lock(user_id):
        root = ensure_workspace(user_id)
        notes_root = _notes_dir(root)
        access_map = access_log.get_all(user_id, "note")
        results = []
        for fname in os.listdir(notes_root):
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
                    "accessed_at": access_map.get(meta["slug"], ""),
                })
            except Exception:
                continue
        # Sort by access time desc, then created_at desc.
        results.sort(
            key=lambda n: (n["accessed_at"], n["created_at"]),
            reverse=True,
        )
        return results


def search_notes(user_id: str, query: str) -> list[dict]:
    """Search notes by title, tags, and content. Returns matching notes."""
    with get_lock(user_id):
        root = ensure_workspace(user_id)
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


def save_and_publish(
    user_id: str,
    title: str,
    content: str,
    tags: list[str] | None = None,
    source_url: str | None = None,
) -> dict:
    """Create a note and publish it in one step.

    This is the unified ingest endpoint — all document pipelines
    (URL, PDF, arXiv, manual) should use this instead of calling
    create_note + publish_notes separately.

    Returns ``{"slug", "title", "url"}`` on success or ``{"error": ...}``.
    """
    if source_url:
        content = f"**Source:** [{source_url}]({source_url})\n\n---\n\n{content}"

    meta = create_note(user_id, title, content, tags or [])

    # Publish if NGROK is available; degrade gracefully if not.
    from prax.utils.ngrok import get_ngrok_url

    base_url = get_ngrok_url()
    if not base_url:
        logger.info("NGROK_URL not configured — note saved locally, skipping publish")
        return {
            "slug": meta["slug"],
            "title": meta["title"],
            "url": "(saved locally — NGROK_URL not configured for web publishing)",
        }

    result = publish_notes(user_id, base_url, slug=meta["slug"])
    if "error" in result:
        # Note is saved — Hugo publish is best-effort for the web page URL.
        logger.warning("Hugo publish failed for %s: %s", meta["slug"], result["error"])
        return {
            "slug": meta["slug"],
            "title": meta["title"],
            "url": f"(saved — web page not rebuilt: {result['error']})",
        }
    return {
        "slug": meta["slug"],
        "title": meta["title"],
        "url": result["url"],
    }


# ---------------------------------------------------------------------------
# Hugo integration
# ---------------------------------------------------------------------------

from prax.services.hugo_publishing import KATEX_HEAD, THEME_CSS  # noqa: E402

_HUGO_NOTES_LIST = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ .Title }}</title>
<style>
""" + THEME_CSS + """\
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
""" + KATEX_HEAD + """\
<style>
""" + THEME_CSS + """\
  .meta .tags span { background: var(--tag-bg); padding: 0.1rem 0.5rem; border-radius: 3px; margin-right: 0.3rem; font-size: 0.85em; }
  .related { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--border); }
  .related h2 { font-size: 1.1em; margin-bottom: 0.5rem; }
  .related ul { list-style: none; padding: 0; }
  .related li { padding: 0.3rem 0; }
  .related a { text-decoration: none; }
  .related a:hover { text-decoration: underline; }
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
{{ with .Params.related }}
<div class="related">
  <h2>Related Notes</h2>
  <ul>
  {{ range . }}<li><a href="../{{ . }}/">{{ . }}</a></li>{{ end }}
  </ul>
</div>
{{ end }}
</body>
</html>
"""


def _generate_hugo_notes(root: str) -> None:
    """Generate Hugo content files for all notes in the workspace."""
    from prax.services.hugo_publishing import hugo_site_dir

    site = hugo_site_dir(root)
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
        related_yaml = yaml.dump(meta.get("related", []), default_flow_style=True).strip()
        page = (
            f'---\n'
            f'title: "{meta["title"]}"\n'
            f'tags: {tags_yaml}\n'
            f'related: {related_yaml}\n'
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
    from prax.services.course_service import generate_hugo_content
    from prax.services.hugo_publishing import (
        courses_dir,
        ensure_hugo_site,
        hugo_site_dir,
        run_hugo,
    )

    with get_lock(user_id):
        root = ensure_workspace(user_id)
        site = ensure_hugo_site(root, base_url)

        # Generate notes content.
        _generate_hugo_notes(root)

        # Also regenerate all course content so the site stays complete.
        courses_root = courses_dir(root)
        for entry in sorted(os.listdir(courses_root)):
            if entry.startswith("_"):
                continue
            cp = os.path.join(courses_root, entry)
            yaml_path = os.path.join(cp, "course.yaml")
            if os.path.isdir(cp) and os.path.isfile(yaml_path):
                generate_hugo_content(root, entry)

        git_commit(root, f"Publish notes{': ' + slug if slug else ''}")

    err = run_hugo(site if 'site' in dir() else hugo_site_dir(root))
    if err:
        return err

    # Verify the generated HTML actually exists before returning a URL.
    # Without this check, we'd return a URL that 404s on click.
    if slug:
        expected_path = os.path.join(site, "public", "notes", slug, "index.html")
        if not os.path.exists(expected_path):
            # Try the un-slugged notes index as a sanity check.
            notes_index = os.path.join(site, "public", "notes", "index.html")
            if not os.path.exists(notes_index):
                return {
                    "error": (
                        f"Hugo build completed but output not found at "
                        f"{expected_path} — note is saved but the web page "
                        f"is not accessible. Check Hugo site config."
                    ),
                }
            return {
                "error": (
                    f"Hugo built the site but the specific note page for "
                    f"'{slug}' was not generated. The note is saved; check "
                    f"that the markdown frontmatter is valid."
                ),
            }

    url_slug = f"notes/{slug}/" if slug else "notes/"
    url = f"{base_url.rstrip('/')}/{url_slug}"
    return {"url": url}


# News briefings were removed — generated content lives in library/outputs/
# now, managed by prax.services.library_service.write_output().
