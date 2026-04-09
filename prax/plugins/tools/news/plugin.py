"""Unified news plugin — briefings, feed tracking, and audio news in one tool.

Replaces the separate npr_podcast, deutschlandfunk, rss_reader, and
news_summary plugins with a single ``news`` tool.

Each user has a ``news_sources.md`` in their workspace that lists their
configured sources (RSS feeds, Hacker News, audio podcasts).  The file is
human-editable markdown.  Defaults are created on first use.
"""
from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import feedparser
import requests
import yaml
from langchain_core.tools import tool

from prax.agent.user_context import current_user_id
from prax.services.workspace_service import (
    ensure_workspace,
    get_lock,
    git_commit,
    safe_join,
)

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "News briefings, RSS feeds, and audio news — one tool for everything"

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "news_sources.md"
_STATE_FILENAME = "news_state.yaml"
_HN_TOP_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
_HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"
_REQUEST_TIMEOUT = 8
_SOURCE_TIMEOUT = 30
_MAX_SEEN_URLS = 200
_SUMMARY_MAX_CHARS = 200

_DEFAULT_CONFIG = """\
# News Sources

Edit this file to customize your news briefing. Each source is a list item
with a name, type, and (for most types) a URL, separated by pipes.

Supported types:
- **rss** — any RSS or Atom feed URL
- **hackernews** — Hacker News top stories (no URL needed)
- **audio** — podcast or radio stream (returns a playback link)

## Sources

- New York Times | rss | https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml
- Hacker News | hackernews
- NPR News Now | audio | npr
- Deutschlandfunk | audio | deutschlandfunk
"""


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def _config_path(workspace_root: str) -> str:
    return safe_join(workspace_root, _CONFIG_FILENAME)


def _state_path(workspace_root: str) -> str:
    return safe_join(workspace_root, _STATE_FILENAME)


def _ensure_config(workspace_root: str) -> None:
    path = _config_path(workspace_root)
    if os.path.isfile(path):
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(_DEFAULT_CONFIG)
    git_commit(workspace_root, "Create default news sources config")


def _load_sources(workspace_root: str) -> list[dict]:
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
        - Name | audio | npr
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
        if src_type in ("rss", "hackernews", "audio"):
            sources.append({"name": name, "type": src_type, "url": url})
    return sources


# ---------------------------------------------------------------------------
# Feed state (for incremental checking)
# ---------------------------------------------------------------------------

def _load_state(workspace_root: str) -> dict:
    path = _state_path(workspace_root)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _save_state(workspace_root: str, data: dict) -> None:
    path = _state_path(workspace_root)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def _truncate(text: str, max_len: int = _SUMMARY_MAX_CHARS) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text).strip().replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _fetch_rss(name: str, url: str, limit: int = 15) -> str:
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
        summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
        summary = _truncate(summary)

        line = f"{i}. **{title}**"
        if summary:
            line += f" — {summary}"
        if link:
            line += f" ([link]({link}))"
        lines.append(line)

    return "\n".join(lines)


def _fetch_rss_incremental(
    name: str, url: str, seen_urls: list[str],
) -> tuple[str, list[str]]:
    """Fetch RSS and return only new items. Returns (formatted_text, new_seen_urls)."""
    try:
        parsed = feedparser.parse(url)
    except Exception as exc:
        return f"**{name}**: Failed to fetch — {exc}", []

    seen_set = set(seen_urls)
    new_items: list[dict] = []
    for entry in parsed.entries:
        link = getattr(entry, "link", "") or getattr(entry, "id", "")
        if not link or link in seen_set:
            continue
        summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
        published = getattr(entry, "published", "") or getattr(entry, "updated", "")
        new_items.append({
            "title": getattr(entry, "title", "(no title)"),
            "link": link,
            "published": published,
            "summary": _truncate(summary),
        })

    new_seen = [item["link"] for item in new_items]

    if not new_items:
        return f"**{name}**: No new items.", new_seen

    lines = [f"**{name}** — {len(new_items)} new item(s):"]
    for item in new_items:
        parts = [f"  - **{item['title']}**"]
        if item["published"]:
            parts.append(f"    Published: {item['published']}")
        parts.append(f"    Link: {item['link']}")
        if item["summary"]:
            parts.append(f"    Summary: {item['summary']}")
        lines.append("\n".join(parts))

    return "\n".join(lines), new_seen


def _fetch_hn_item(sid: int) -> dict | None:
    """Fetch a single HN story. Returns None on failure."""
    try:
        return requests.get(
            _HN_ITEM_URL.format(sid), timeout=_REQUEST_TIMEOUT,
        ).json()
    except Exception:
        return None


def _fetch_hackernews(name: str, limit: int = 15) -> str:
    try:
        resp = requests.get(_HN_TOP_URL, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        story_ids = resp.json()[:limit]
    except Exception as exc:
        return f"**{name}**: Failed to fetch — {exc}"

    # Fetch all stories in parallel instead of sequentially
    items: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_hn_item, sid): sid for sid in story_ids}
        for future in as_completed(futures, timeout=_SOURCE_TIMEOUT):
            sid = futures[future]
            result = future.result()
            if result:
                items[sid] = result

    lines = [f"### {name}"]
    for i, sid in enumerate(story_ids, 1):
        item = items.get(sid)
        if not item:
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


def _fetch_audio(name: str, url: str) -> str:
    """Fetch an audio news source. Returns a playback link."""
    key = url.strip().lower()
    if key == "npr":
        try:
            from prax.readers.news.npr_top_hour import get_latest_npr_podcast
            result = get_latest_npr_podcast()
            return f"### {name}\n🎧 [Listen to latest episode]({result})" if result else f"**{name}**: Unable to fetch."
        except Exception as exc:
            return f"**{name}**: Error — {exc}"
    elif key == "deutschlandfunk":
        try:
            from prax.readers.news.deutschlandfunk_radio import deutschlandfunk_process
            result = deutschlandfunk_process("agent")
            return f"### {name}\n🎧 [Listen to latest broadcast]({result})" if result else f"**{name}**: Unable to fetch."
        except Exception as exc:
            return f"**{name}**: Error — {exc}"
    elif url.startswith("http"):
        # Generic audio URL — just return the link
        return f"### {name}\n🎧 [Listen]({url})"
    else:
        return f"**{name}**: Unknown audio source '{url}'"


def _fetch_source_briefing(source: dict) -> str:
    """Dispatch to the right fetcher for a full briefing."""
    if source["type"] == "hackernews":
        return _fetch_hackernews(source["name"])
    if source["type"] == "rss":
        return _fetch_rss(source["name"], source["url"])
    if source["type"] == "audio":
        return _fetch_audio(source["name"], source["url"])
    return f"**{source['name']}**: Unknown source type '{source['type']}'"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _build_briefing_content(sources: list[dict]) -> str:
    """Build the markdown content for a full briefing.

    Fetches all sources in parallel with a per-source timeout so one slow
    source can't block the entire briefing.
    """
    now = datetime.now(UTC).strftime("%A, %B %d, %Y at %H:%M UTC")
    sections = [f"*{now}*\n"]

    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_fetch_source_briefing, src): src["name"]
            for src in sources
        }
        for future in as_completed(futures, timeout=_SOURCE_TIMEOUT * 2):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                logger.exception("Failed to fetch source %s", name)
                results[name] = f"**{name}**: Error — {exc}"

    # Preserve original source order
    for source in sources:
        sections.append(results.get(
            source["name"],
            f"**{source['name']}**: Timed out.",
        ))

    return "\n\n".join(sections)


def _summarize_briefing(content: str) -> str:
    """Extract a compact digest from the full briefing for the agent.

    Returns source names + top ~3 headlines each so the agent can curate
    a conversational summary without dumping the entire briefing.
    """
    lines = []
    current_source = None
    count = 0
    for line in content.splitlines():
        if line.startswith("### "):
            current_source = line
            count = 0
            lines.append(current_source)
        elif line and line[0].isdigit() and ". **" in line:
            count += 1
            if count <= 3:
                # Strip link markdown to keep it compact
                lines.append(line)
            elif count == 4:
                lines.append("  _(+ more — see full briefing)_")
    return "\n".join(lines)


def _do_briefing(user_id: str, sources: list[dict]) -> str:
    """Full executive summary — persisted to library/outputs/."""
    content = _build_briefing_content(sources)
    today = datetime.now(UTC).strftime("%B %-d, %Y")
    title = f"News Briefing — {today}"
    digest = _summarize_briefing(content)

    try:
        from prax.services.library_service import write_output
        result = write_output(user_id, title, content, kind="news-briefing")
        slug = result.get("output", {}).get("slug", "")
        return (
            f"Saved to library/outputs/ as `{slug}`.\n\n"
            f"Digest:\n{digest}"
        )
    except Exception as exc:
        logger.exception("Failed to save news briefing to library outputs")
        return f"Briefing generated but saving failed: {exc}\n\n{content}"


def _do_check(workspace_root: str, sources: list[dict], feed_name: str) -> str:
    """Incremental check — new items since last check."""
    state = _load_state(workspace_root)
    now = datetime.now(UTC).isoformat()

    rss_sources = [s for s in sources if s["type"] == "rss"]
    if feed_name:
        rss_sources = [s for s in rss_sources if s["name"].lower() == feed_name.lower()]
        if not rss_sources:
            names = ", ".join(s["name"] for s in sources if s["type"] == "rss")
            return f"No RSS feed named '{feed_name}'. Available: {names or '(none)'}"

    if not rss_sources:
        return "No RSS feeds configured. Add some to `news_sources.md`."

    results: list[str] = []
    for source in rss_sources:
        key = source["name"]
        seen = state.get(key, {}).get("seen_urls", [])
        text, new_seen = _fetch_rss_incremental(source["name"], source["url"], seen)
        results.append(text)

        if key not in state:
            state[key] = {}
        state[key]["seen_urls"] = (new_seen + seen)[:_MAX_SEEN_URLS]
        state[key]["last_checked"] = now

    _save_state(workspace_root, state)
    git_commit(workspace_root, "Check news feeds")
    return "\n\n".join(results)


def _do_sources(sources: list[dict]) -> str:
    """List configured sources."""
    if not sources:
        return "No sources configured. Edit `news_sources.md` in your workspace to add some."

    lines = ["**Configured News Sources:**\n"]
    for s in sources:
        url_part = f" — {s['url']}" if s["url"] else ""
        lines.append(f"- **{s['name']}** ({s['type']}){url_part}")
    lines.append("\nEdit `news_sources.md` in your workspace to add or remove sources.")
    return "\n".join(lines)


def _do_listen(sources: list[dict], source_name: str) -> str:
    """Get audio news link for a specific source or all audio sources."""
    audio_sources = [s for s in sources if s["type"] == "audio"]
    if source_name:
        audio_sources = [s for s in audio_sources if s["name"].lower() == source_name.lower()]
        if not audio_sources:
            names = ", ".join(s["name"] for s in sources if s["type"] == "audio")
            return f"No audio source named '{source_name}'. Available: {names or '(none)'}"

    if not audio_sources:
        return "No audio sources configured. Add one to `news_sources.md` (type: audio)."

    results = []
    for source in audio_sources:
        try:
            results.append(_fetch_audio(source["name"], source["url"]))
        except Exception as exc:
            results.append(f"**{source['name']}**: Error — {exc}")
    return "\n\n".join(results)


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

@tool
def news(action: str = "briefing", source: str = "") -> str:
    """Unified news tool — briefings, feed tracking, and audio news.

    Actions:
        briefing — Full executive summary from all configured sources.
                   Includes RSS headlines, Hacker News top stories, and
                   audio news links.  Great for "give me the news" or
                   "what's happening today".
        check    — Incremental: show only NEW items since the last check.
                   Works with RSS feeds.  Optionally filter by source name.
        sources  — List all configured news sources.
        listen   — Get audio news links (NPR, Deutschlandfunk, etc.).
                   Optionally filter by source name.

    Args:
        action: The action to perform (default: "briefing").
        source: Optional source name to filter by (for check and listen).

    Sources are configured in ``news_sources.md`` in the user's workspace.
    The file is created with sensible defaults (NYT, Hacker News, NPR,
    Deutschlandfunk) on first use.  Users can edit it at any time to add
    or remove sources — the tool re-reads it on every call.
    """
    uid = current_user_id.get()
    if not uid:
        return "Error: no active user context."

    # Hold the lock only for config I/O — action functions manage their
    # own locking if needed.
    with get_lock(uid):
        root = ensure_workspace(uid)
        _ensure_config(root)
        sources = _load_sources(root)

    action = action.strip().lower()

    if action == "briefing":
        if not sources:
            return "No news sources configured. Edit `news_sources.md` to add some."
        return _do_briefing(uid, sources)

    if action == "check":
        return _do_check(root, sources, source)

    if action == "sources":
        return _do_sources(sources)

    if action == "listen":
        return _do_listen(sources, source)

    return (
        f"Unknown action '{action}'. "
        "Use: briefing, check, sources, or listen."
    )


def register():
    return [news]
