"""Tests for the RSS feed reader plugin (no network calls)."""
from __future__ import annotations

import os
import threading
from unittest.mock import MagicMock, patch

import pytest
import yaml

from prax.plugins.tools.rss_reader.plugin import (
    _item_id,
    _load_feeds,
    _parse_feed_entries,
    _save_feeds,
    _truncate,
    rss_check,
    rss_list,
    rss_subscribe,
    rss_unsubscribe,
)

# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def workspace(tmp_path):
    """Create a minimal workspace directory and patch workspace helpers."""
    ws_root = str(tmp_path / "workspace")
    os.makedirs(ws_root, exist_ok=True)

    with (
        patch("prax.plugins.tools.rss_reader.plugin.ensure_workspace", return_value=ws_root),
        patch("prax.plugins.tools.rss_reader.plugin.get_lock") as mock_lock,
        patch("prax.plugins.tools.rss_reader.plugin.git_commit"),
        patch("prax.plugins.tools.rss_reader.plugin.current_user_id") as mock_uid,
    ):
        # get_lock must return a real context-manager-capable lock.
        mock_lock.return_value = threading.Lock()
        mock_uid.get.return_value = "test_user"
        yield ws_root


def _write_feeds(ws_root: str, feeds: dict) -> None:
    """Write a feeds.yaml file into the workspace."""
    path = os.path.join(ws_root, "feeds.yaml")
    with open(path, "w") as f:
        yaml.dump(feeds, f)


def _read_feeds(ws_root: str) -> dict:
    """Read feeds.yaml from the workspace."""
    path = os.path.join(ws_root, "feeds.yaml")
    if not os.path.isfile(path):
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


class _FakeEntry:
    """Minimal feedparser entry stand-in with dict-like .get() and attributes."""

    def __init__(self, **kwargs):
        self._data = kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)

    def get(self, key, default=""):
        return self._data.get(key, default)


def _make_entry(title="Test Article", link="https://example.com/1",
                summary="A test article summary.", published="Mon, 01 Jan 2024 00:00:00 GMT"):
    """Create a fake feedparser entry."""
    return _FakeEntry(title=title, link=link, id=link, summary=summary, published=published)


def _make_parsed_feed(entries):
    """Create a mock feedparser.parse() result."""
    result = MagicMock()
    result.entries = entries
    return result


# ---------------------------------------------------------------------------
# Unit tests — helpers
# ---------------------------------------------------------------------------

class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello") == "hello"

    def test_long_text_truncated(self):
        text = "a" * 400
        result = _truncate(text)
        assert len(result) == 303  # 300 + len("...")
        assert result.endswith("...")

    def test_empty_string(self):
        assert _truncate("") == ""

    def test_newlines_replaced(self):
        assert "\n" not in _truncate("line1\nline2")

    def test_exactly_at_limit(self):
        text = "b" * 300
        assert _truncate(text) == text


class TestItemId:
    def test_prefers_link(self):
        entry = {"link": "http://a.com", "id": "http://b.com"}
        assert _item_id(entry) == "http://a.com"

    def test_falls_back_to_id(self):
        entry = {"id": "http://b.com"}
        assert _item_id(entry) == "http://b.com"

    def test_returns_empty_if_nothing(self):
        assert _item_id({}) == ""


class TestLoadSaveFeeds:
    def test_load_empty(self, tmp_path):
        assert _load_feeds(str(tmp_path)) == {}

    def test_round_trip(self, tmp_path):
        root = str(tmp_path)
        data = {"myblog": {"url": "https://blog.example.com/rss", "name": "myblog",
                           "last_checked": None, "seen_urls": []}}
        _save_feeds(root, data)
        loaded = _load_feeds(root)
        assert loaded == data


# ---------------------------------------------------------------------------
# Unit tests — parse_feed_entries (with mocked feedparser)
# ---------------------------------------------------------------------------

class TestParseFeedEntries:
    @patch("prax.plugins.tools.rss_reader.plugin.feedparser")
    def test_returns_new_items(self, mock_fp):
        entry = _make_entry()
        mock_fp.parse.return_value = _make_parsed_feed([entry])

        items = _parse_feed_entries("https://example.com/rss", [])
        assert len(items) == 1
        assert items[0]["title"] == "Test Article"
        assert items[0]["link"] == "https://example.com/1"

    @patch("prax.plugins.tools.rss_reader.plugin.feedparser")
    def test_dedup_skips_seen(self, mock_fp):
        entry = _make_entry(link="https://example.com/1")
        mock_fp.parse.return_value = _make_parsed_feed([entry])

        items = _parse_feed_entries("https://example.com/rss", ["https://example.com/1"])
        assert len(items) == 0

    @patch("prax.plugins.tools.rss_reader.plugin.feedparser")
    def test_summary_truncated(self, mock_fp):
        long_summary = "x" * 500
        entry = _make_entry(summary=long_summary)
        mock_fp.parse.return_value = _make_parsed_feed([entry])

        items = _parse_feed_entries("https://example.com/rss", [])
        assert len(items[0]["summary"]) == 303

    @patch("prax.plugins.tools.rss_reader.plugin.feedparser")
    def test_empty_feed(self, mock_fp):
        mock_fp.parse.return_value = _make_parsed_feed([])
        items = _parse_feed_entries("https://example.com/rss", [])
        assert items == []


# ---------------------------------------------------------------------------
# Integration tests — tool functions (workspace mocked)
# ---------------------------------------------------------------------------

class TestRssSubscribe:
    def test_subscribe_new_feed(self, workspace):
        result = rss_subscribe.invoke({"url": "https://blog.example.com/rss", "name": "myblog"})
        assert "Subscribed" in result
        assert "myblog" in result
        feeds = _read_feeds(workspace)
        assert "myblog" in feeds
        assert feeds["myblog"]["url"] == "https://blog.example.com/rss"

    def test_subscribe_duplicate_rejected(self, workspace):
        rss_subscribe.invoke({"url": "https://blog.example.com/rss", "name": "myblog"})
        result = rss_subscribe.invoke({"url": "https://blog.example.com/rss2", "name": "myblog"})
        assert "already exists" in result

    def test_subscribe_auto_name(self, workspace):
        result = rss_subscribe.invoke({"url": "https://blog.example.com/feed/rss"})
        assert "Subscribed" in result
        feeds = _read_feeds(workspace)
        assert any("blog.example.com" in name for name in feeds)


class TestRssUnsubscribe:
    def test_unsubscribe_existing(self, workspace):
        rss_subscribe.invoke({"url": "https://a.com/rss", "name": "feed_a"})
        result = rss_unsubscribe.invoke({"name": "feed_a"})
        assert "Unsubscribed" in result
        feeds = _read_feeds(workspace)
        assert "feed_a" not in feeds

    def test_unsubscribe_nonexistent(self, workspace):
        result = rss_unsubscribe.invoke({"name": "nope"})
        assert "No feed named" in result


class TestRssList:
    def test_list_empty(self, workspace):
        result = rss_list.invoke({})
        assert "No feeds" in result

    def test_list_with_feeds(self, workspace):
        rss_subscribe.invoke({"url": "https://a.com/rss", "name": "alpha"})
        rss_subscribe.invoke({"url": "https://b.com/rss", "name": "beta"})
        result = rss_list.invoke({})
        assert "alpha" in result
        assert "beta" in result
        assert "2 subscribed" in result


class TestRssCheck:
    @patch("prax.plugins.tools.rss_reader.plugin.feedparser")
    def test_check_single_feed_new_items(self, mock_fp, workspace):
        _write_feeds(workspace, {
            "myblog": {
                "url": "https://blog.example.com/rss",
                "name": "myblog",
                "last_checked": None,
                "seen_urls": [],
            },
        })
        entry = _make_entry(title="New Post", link="https://blog.example.com/new-post")
        mock_fp.parse.return_value = _make_parsed_feed([entry])

        result = rss_check.invoke({"name": "myblog"})
        assert "New Post" in result
        assert "1 new item" in result

        # Verify seen_urls updated.
        feeds = _read_feeds(workspace)
        assert "https://blog.example.com/new-post" in feeds["myblog"]["seen_urls"]

    @patch("prax.plugins.tools.rss_reader.plugin.feedparser")
    def test_check_dedup_on_second_run(self, mock_fp, workspace):
        _write_feeds(workspace, {
            "myblog": {
                "url": "https://blog.example.com/rss",
                "name": "myblog",
                "last_checked": None,
                "seen_urls": ["https://blog.example.com/old-post"],
            },
        })
        entry = _make_entry(title="Old Post", link="https://blog.example.com/old-post")
        mock_fp.parse.return_value = _make_parsed_feed([entry])

        result = rss_check.invoke({"name": "myblog"})
        assert "No new items" in result

    @patch("prax.plugins.tools.rss_reader.plugin.feedparser")
    def test_check_all_feeds(self, mock_fp, workspace):
        _write_feeds(workspace, {
            "feed_a": {
                "url": "https://a.com/rss", "name": "feed_a",
                "last_checked": None, "seen_urls": [],
            },
            "feed_b": {
                "url": "https://b.com/rss", "name": "feed_b",
                "last_checked": None, "seen_urls": [],
            },
        })
        entry_a = _make_entry(title="Post A", link="https://a.com/1")
        entry_b = _make_entry(title="Post B", link="https://b.com/1")

        def side_effect(url):
            if "a.com" in url:
                return _make_parsed_feed([entry_a])
            return _make_parsed_feed([entry_b])

        mock_fp.parse.side_effect = side_effect

        result = rss_check.invoke({"name": ""})
        assert "Post A" in result
        assert "Post B" in result

    @patch("prax.plugins.tools.rss_reader.plugin.feedparser")
    def test_check_nonexistent_feed(self, mock_fp, workspace):
        result = rss_check.invoke({"name": "nope"})
        assert "No feeds subscribed" in result or "No feed named" in result

    @patch("prax.plugins.tools.rss_reader.plugin.feedparser")
    def test_seen_urls_capped_at_200(self, mock_fp, workspace):
        _write_feeds(workspace, {
            "myblog": {
                "url": "https://blog.example.com/rss",
                "name": "myblog",
                "last_checked": None,
                "seen_urls": [f"https://old.com/{i}" for i in range(190)],
            },
        })
        entries = [_make_entry(title=f"Post {i}", link=f"https://blog.example.com/{i}")
                   for i in range(20)]
        mock_fp.parse.return_value = _make_parsed_feed(entries)

        rss_check.invoke({"name": "myblog"})
        feeds = _read_feeds(workspace)
        assert len(feeds["myblog"]["seen_urls"]) <= 200

    @patch("prax.plugins.tools.rss_reader.plugin.feedparser")
    def test_check_updates_last_checked(self, mock_fp, workspace):
        _write_feeds(workspace, {
            "myblog": {
                "url": "https://blog.example.com/rss",
                "name": "myblog",
                "last_checked": None,
                "seen_urls": [],
            },
        })
        mock_fp.parse.return_value = _make_parsed_feed([])

        rss_check.invoke({"name": "myblog"})
        feeds = _read_feeds(workspace)
        assert feeds["myblog"]["last_checked"] is not None


class TestRssCheckEdgeCases:
    @patch("prax.plugins.tools.rss_reader.plugin.feedparser")
    def test_check_feed_parse_error(self, mock_fp, workspace):
        _write_feeds(workspace, {
            "bad_feed": {
                "url": "https://bad.example.com/rss",
                "name": "bad_feed",
                "last_checked": None,
                "seen_urls": [],
            },
        })
        mock_fp.parse.side_effect = Exception("connection refused")

        result = rss_check.invoke({"name": "bad_feed"})
        assert "Error" in result

    def test_check_empty_subscriptions(self, workspace):
        result = rss_check.invoke({"name": ""})
        assert "No feeds subscribed" in result
