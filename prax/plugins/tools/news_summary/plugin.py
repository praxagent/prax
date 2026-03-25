"""Executive news summary — scan configured sources and return a briefing.

Each user has a ``news_exec_summary.md`` in their workspace that lists their
news sources.  The file is human-editable markdown.  Defaults to NYT and
Hacker News if the file doesn't exist.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

import feedparser
import requests
from langchain_core.tools import tool

from prax.agent.user_context import current_user_id
from prax.services.workspace_service import ensure_workspace, get_lock, git_commit, safe_join

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "Scan news sources and produce an executive briefing"

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "news_exec_summary.md"
_HN_TOP_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
_HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"
_REQUEST_TIMEOUT = 12

_DEFAULT_CONFIG = """\
# Executive Summary — News Sources

Edit this file to add or remove news sources for your daily briefing.
Each source is a list item with a name, type, and URL separated by pipes.

Supported types:
- **rss** — any RSS or Atom feed URL
- **hackernews** — Hacker News top stories (no URL needed)

## Sources

- New York Times | rss | https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml
- Hacker News | hackernews
"""


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def _config_path(workspace_root: str) -> str:
    return safe_join(workspace_root, _CONFIG_FILENAME)


def _load_sources(workspace_root: str) -> list[dict]:
    """Parse the markdown config into a list of source dicts."""
    import os

    path = _config_path(workspace_root)
    if not os.path.isfile(path):
        return _parse_sources(_DEFAULT_CONFIG)

    with open(path, encoding="utf-8") as f:
        return _parse_sources(f.read())


def _parse_sources(text: str) -> list[dict]:
    """Extract source entries from markdown list items.

    Formats:
        - Name | rss | https://...
        - Name | hackernews
    """
    sources: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        parts = [p.strip() for p in line[2:].split("|")]
        if len(parts) < 2:
            continue
        name = parts[0]
        src_type = parts[1].lower()
        url = parts[2] if len(parts) > 2 else ""
        if src_type in ("rss", "hackernews"):
            sources.append({"name": name, "type": src_type, "url": url})
    return sources


def _ensure_config(workspace_root: str) -> None:
    """Create the default config file if it doesn't exist."""
    import os

    path = _config_path(workspace_root)
    if os.path.isfile(path):
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(_DEFAULT_CONFIG)
    git_commit(workspace_root, "Create default news executive summary config")


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def _fetch_rss(name: str, url: str, limit: int = 15) -> str:
    """Fetch an RSS feed and return formatted headlines."""
    try:
        parsed = feedparser.parse(url)
    except Exception as exc:
        return f"**{name}**: Failed to fetch — {exc}"

    if not parsed.entries:
        return f"**{name}**: No entries found."

    lines = [f"### {name}"]
    for i, entry in enumerate(parsed.entries[:limit], 1):
        title = getattr(entry, "title", "(no title)")
        link = getattr(entry, "link", "")
        summary = getattr(entry, "summary", "")
        # Strip HTML tags from summary
        summary = re.sub(r"<[^>]+>", "", summary).strip()
        if len(summary) > 200:
            summary = summary[:200] + "..."

        line = f"{i}. **{title}**"
        if summary:
            line += f" — {summary}"
        if link:
            line += f" ([link]({link}))"
        lines.append(line)

    return "\n".join(lines)


def _fetch_hackernews(name: str, limit: int = 15) -> str:
    """Fetch Hacker News top stories via the Firebase API."""
    try:
        resp = requests.get(_HN_TOP_URL, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        story_ids = resp.json()[:limit]
    except Exception as exc:
        return f"**{name}**: Failed to fetch — {exc}"

    lines = [f"### {name}"]
    for i, sid in enumerate(story_ids, 1):
        try:
            item = requests.get(
                _HN_ITEM_URL.format(sid), timeout=_REQUEST_TIMEOUT,
            ).json()
        except Exception:
            continue

        title = item.get("title", "(no title)")
        url = item.get("url", "")
        score = item.get("score", 0)
        comments = item.get("descendants", 0)
        hn_link = f"https://news.ycombinator.com/item?id={sid}"

        line = f"{i}. **{title}** — {score} pts, {comments} comments"
        if url:
            line += f" ([article]({url}))"
        line += f" ([discuss]({hn_link}))"
        lines.append(line)

    return "\n".join(lines)


def _fetch_source(source: dict) -> str:
    """Dispatch to the right fetcher based on source type."""
    if source["type"] == "hackernews":
        return _fetch_hackernews(source["name"])
    if source["type"] == "rss":
        return _fetch_rss(source["name"], source["url"])
    return f"**{source['name']}**: Unknown source type '{source['type']}'"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def news_exec_summary() -> str:
    """Scan all configured news sources and return an executive briefing.

    Reads the user's ``news_exec_summary.md`` for their source list.
    If the file doesn't exist, creates it with defaults (NYT + Hacker News).
    The user can edit this file to add or remove sources at any time.

    Returns a formatted summary suitable for creating a note or reading aloud.
    """
    uid = current_user_id.get()
    if not uid:
        return "Error: no active user context."

    with get_lock(uid):
        root = ensure_workspace(uid)
        _ensure_config(root)
        sources = _load_sources(root)

    if not sources:
        return (
            "No news sources configured. Edit `news_exec_summary.md` in your "
            "workspace to add sources."
        )

    now = datetime.now(UTC).strftime("%A, %B %d, %Y at %H:%M UTC")
    sections = [f"## Executive News Briefing\n*{now}*\n"]

    for source in sources:
        try:
            section = _fetch_source(source)
        except Exception as exc:
            logger.exception("Failed to fetch source %s", source["name"])
            section = f"**{source['name']}**: Error — {exc}"
        sections.append(section)

    return "\n\n".join(sections)


@tool
def news_sources_list() -> str:
    """Show the currently configured news sources.

    Lists what's in the user's ``news_exec_summary.md`` config file.
    """
    uid = current_user_id.get()
    if not uid:
        return "Error: no active user context."

    with get_lock(uid):
        root = ensure_workspace(uid)
        _ensure_config(root)
        sources = _load_sources(root)

    if not sources:
        return "No sources configured. Edit `news_exec_summary.md` to add some."

    lines = ["**Configured News Sources:**\n"]
    for s in sources:
        url_part = f" — {s['url']}" if s["url"] else ""
        lines.append(f"- **{s['name']}** ({s['type']}){url_part}")
    lines.append(
        "\nEdit `news_exec_summary.md` in your workspace to add or remove sources."
    )
    return "\n".join(lines)


def register():
    # Consolidated into the unified 'news' plugin.
    return []
