"""Shared Hugo publishing primitives for notes and courses.

This module owns the Hugo site skeleton (`courses/_site/`), the KaTeX +
Mermaid + theme CSS templates, and the build/scan helpers used by both
``note_service.publish_notes()`` and ``course_service.build_course_site()``.

**Why the site lives under ``courses/_site/``:** historical.  The original
course publishing landed there first and note publishing was bolted on
alongside because both produce Hugo markdown pages served through the
same ngrok tunnel.  Courses and notes are now independent data concepts
but they still share one Hugo build so the output URL scheme stays
stable (``/courses/<slug>/`` for courses, ``/notes/<slug>/`` for notes).

If we ever want to split them into two sites we can rename this module's
``hugo_site_dir`` root and update the URL templates — nothing else would
need to change.

Before Phase 5 this code lived inside ``course_service.py`` and
``note_service`` reached across the module boundary to import private
helpers.  Extracting here makes the dependency explicit and lets either
module evolve without breaking the other.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Templates (theme + layouts)
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
  description = "Personal courses and notes"
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


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def courses_dir(root: str) -> str:
    """Return the root where course directories live (``{root}/courses/``)."""
    d = os.path.join(root, "courses")
    os.makedirs(d, exist_ok=True)
    return d


def hugo_site_dir(root: str) -> str:
    """Return the path to the shared Hugo site directory."""
    return os.path.join(root, "courses", "_site")


def ensure_hugo_site(root: str, base_url: str, site_title: str = "Courses") -> str:
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
            site_title=site_title,
        ))

    # Write minimal theme templates.
    with open(os.path.join(layouts_dir, "single.html"), "w", encoding="utf-8") as f:
        f.write(_HUGO_SINGLE)
    with open(os.path.join(layouts_dir, "list.html"), "w", encoding="utf-8") as f:
        f.write(_HUGO_LIST)

    return site


# ---------------------------------------------------------------------------
# Build + locate
# ---------------------------------------------------------------------------

def run_hugo(site: str) -> dict | None:
    """Run Hugo to build the site. Returns error dict or None on success.

    Hugo runs locally (not routed through the sandbox) because
    ensure_hugo_site writes files directly to the local filesystem.
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


def get_course_site_public_dir(user_id: str) -> str | None:
    """Return the path to the Hugo public/ dir if it exists for a specific user."""
    from prax.services.workspace_service import workspace_root
    root = workspace_root(user_id)
    public = os.path.join(hugo_site_dir(root), "public")
    return public if os.path.isdir(public) else None


def find_course_site_public_dir(path: str) -> str | None:
    """Find a Hugo public/ dir that contains *path* by scanning all user workspaces.

    Course + note pages are public (shared via ngrok links), so we don't
    require authentication — we just need to locate which user's built
    site has the requested file.  Returns the public dir, or None.
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
        if os.path.exists(candidate):
            return public
        if os.path.isdir(candidate) or os.path.isfile(os.path.join(candidate, "index.html")):
            return public

    return None
