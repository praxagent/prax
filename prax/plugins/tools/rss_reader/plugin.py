"""RSS/Atom feed reader plugin -- subscribe, track, and check feeds."""
from __future__ import annotations

import os
from datetime import UTC, datetime

import feedparser
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
PLUGIN_DESCRIPTION = (
    "Subscribe to RSS/Atom feeds, list subscriptions, and check for new items"
)

_MAX_SEEN_URLS = 200
_SUMMARY_MAX_CHARS = 300
_FEEDS_FILENAME = "feeds.yaml"


# ---------------------------------------------------------------------------
# Feed state helpers
# ---------------------------------------------------------------------------

def _feeds_path(workspace_root: str) -> str:
    """Return the absolute path to feeds.yaml inside the workspace."""
    return safe_join(workspace_root, _FEEDS_FILENAME)


def _load_feeds(workspace_root: str) -> dict:
    """Load feeds.yaml, returning an empty dict if it does not exist."""
    path = _feeds_path(workspace_root)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _save_feeds(workspace_root: str, data: dict) -> None:
    """Write feeds.yaml to the workspace."""
    path = _feeds_path(workspace_root)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def _truncate(text: str, max_len: int = _SUMMARY_MAX_CHARS) -> str:
    """Truncate text to *max_len* characters, appending '...' if trimmed."""
    if not text:
        return ""
    text = text.strip().replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _item_id(entry) -> str:
    """Return a stable identifier for a feed entry (link preferred, then id)."""
    return entry.get("link") or entry.get("id") or ""


def _parse_feed_entries(feed_url: str, seen_urls: list[str]) -> list[dict]:
    """Parse a feed and return new (unseen) items."""
    parsed = feedparser.parse(feed_url)
    seen_set = set(seen_urls)
    new_items: list[dict] = []
    for entry in parsed.entries:
        uid = _item_id(entry)
        if not uid or uid in seen_set:
            continue
        summary = ""
        if hasattr(entry, "summary"):
            summary = entry.summary
        elif hasattr(entry, "description"):
            summary = entry.description
        published = ""
        if hasattr(entry, "published"):
            published = entry.published
        elif hasattr(entry, "updated"):
            published = entry.updated
        new_items.append({
            "title": getattr(entry, "title", "(no title)"),
            "link": uid,
            "published": published,
            "summary": _truncate(summary),
        })
    return new_items


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def rss_subscribe(url: str, name: str = "") -> str:
    """Subscribe to an RSS or Atom feed.

    The feed will be stored in your workspace so you can check for new items
    later with rss_check.

    Args:
        url: The URL of the RSS/Atom feed.
        name: A short friendly name for the feed. If omitted, one is derived
              from the URL.
    """
    uid = current_user_id.get() or "unknown"
    if not name:
        # Derive a name from the URL domain + path.
        from urllib.parse import urlparse

        parts = urlparse(url)
        name = (parts.netloc + parts.path).strip("/").replace("/", "_")
        # Trim to something reasonable.
        if len(name) > 60:
            name = name[:60]

    with get_lock(uid):
        root = ensure_workspace(uid)
        feeds = _load_feeds(root)

        if name in feeds:
            return f"A feed named '{name}' already exists. Use rss_unsubscribe first."

        feeds[name] = {
            "url": url,
            "name": name,
            "last_checked": None,
            "seen_urls": [],
        }
        _save_feeds(root, feeds)
        git_commit(root, f"Subscribe to RSS feed: {name}")

    return f"Subscribed to '{name}' ({url})."


@tool
def rss_unsubscribe(name: str) -> str:
    """Remove an RSS/Atom feed subscription.

    Args:
        name: The name of the feed to remove (as shown by rss_list).
    """
    uid = current_user_id.get() or "unknown"

    with get_lock(uid):
        root = ensure_workspace(uid)
        feeds = _load_feeds(root)

        if name not in feeds:
            available = ", ".join(feeds.keys()) if feeds else "(none)"
            return f"No feed named '{name}'. Subscribed feeds: {available}"

        del feeds[name]
        _save_feeds(root, feeds)
        git_commit(root, f"Unsubscribe from RSS feed: {name}")

    return f"Unsubscribed from '{name}'."


@tool
def rss_list() -> str:
    """List all subscribed RSS/Atom feeds with their last-checked time."""
    uid = current_user_id.get() or "unknown"

    root = ensure_workspace(uid)
    feeds = _load_feeds(root)

    if not feeds:
        return "No feeds subscribed. Use rss_subscribe to add one."

    lines: list[str] = []
    for name, info in feeds.items():
        last = info.get("last_checked") or "never"
        lines.append(f"- **{name}**: {info['url']}  (last checked: {last})")
    return f"{len(feeds)} subscribed feed(s):\n" + "\n".join(lines)


@tool
def rss_check(name: str = "") -> str:
    """Check one or all feeds for new items since the last check.

    Returns new items with title, link, published date, and summary.
    Items are marked as seen so they won't appear again.

    Args:
        name: The name of a specific feed to check. If empty, checks all feeds.
    """
    uid = current_user_id.get() or "unknown"

    with get_lock(uid):
        root = ensure_workspace(uid)
        feeds = _load_feeds(root)

        if not feeds:
            return "No feeds subscribed. Use rss_subscribe to add one."

        names_to_check = [name] if name else list(feeds.keys())

        # Validate requested name.
        if name and name not in feeds:
            available = ", ".join(feeds.keys())
            return f"No feed named '{name}'. Subscribed feeds: {available}"

        all_results: list[str] = []
        now = datetime.now(UTC).isoformat()

        for feed_name in names_to_check:
            info = feeds[feed_name]
            seen_urls = info.get("seen_urls", [])

            try:
                new_items = _parse_feed_entries(info["url"], seen_urls)
            except Exception as e:
                all_results.append(f"**{feed_name}**: Error fetching feed: {e}")
                continue

            if new_items:
                # Update seen_urls, capping at _MAX_SEEN_URLS.
                new_seen = [item["link"] for item in new_items]
                info["seen_urls"] = (new_seen + seen_urls)[:_MAX_SEEN_URLS]
                info["last_checked"] = now

                header = f"**{feed_name}** -- {len(new_items)} new item(s):"
                item_lines = []
                for item in new_items:
                    parts = [f"  - **{item['title']}**"]
                    if item["published"]:
                        parts.append(f"    Published: {item['published']}")
                    parts.append(f"    Link: {item['link']}")
                    if item["summary"]:
                        parts.append(f"    Summary: {item['summary']}")
                    item_lines.append("\n".join(parts))
                all_results.append(header + "\n" + "\n".join(item_lines))
            else:
                info["last_checked"] = now
                all_results.append(f"**{feed_name}**: No new items.")

        _save_feeds(root, feeds)
        git_commit(root, "Check RSS feeds")

    return "\n\n".join(all_results)


def register():
    # Consolidated into the unified 'news' plugin.
    return []
