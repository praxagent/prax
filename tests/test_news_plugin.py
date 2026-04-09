"""Tests for the unified news plugin (no network calls)."""
from __future__ import annotations

import os
import threading
from unittest.mock import MagicMock, patch

import pytest

from prax.plugins.tools.news.plugin import (
    _DEFAULT_CONFIG,
    _build_briefing_content,
    _do_briefing,
    _do_check,
    _do_listen,
    _do_sources,
    _fetch_audio,
    _fetch_hackernews,
    _fetch_rss,
    _fetch_rss_incremental,
    _load_state,
    _parse_sources,
    _save_state,
    _truncate,
    news,
    register,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MODULE = "prax.plugins.tools.news.plugin"


@pytest.fixture()
def workspace(tmp_path):
    ws_root = str(tmp_path / "workspace")
    os.makedirs(ws_root, exist_ok=True)

    with (
        patch(f"{_MODULE}.ensure_workspace", return_value=ws_root),
        patch(f"{_MODULE}.get_lock") as mock_lock,
        patch(f"{_MODULE}.git_commit"),
        patch(f"{_MODULE}.current_user_id") as mock_uid,
    ):
        mock_lock.return_value = threading.Lock()
        mock_uid.get.return_value = "test_user"
        yield ws_root


def _write_config(ws_root: str, content: str) -> None:
    path = os.path.join(ws_root, "news_sources.md")
    with open(path, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# _parse_sources
# ---------------------------------------------------------------------------


class TestParseSources:
    def test_parse_default_config(self):
        sources = _parse_sources(_DEFAULT_CONFIG)
        assert len(sources) == 4
        names = [s["name"] for s in sources]
        assert "New York Times" in names
        assert "Hacker News" in names
        assert "NPR News Now" in names
        assert "Deutschlandfunk" in names

    def test_rss_source(self):
        sources = _parse_sources("- Blog | rss | https://blog.com/feed")
        assert sources == [{"name": "Blog", "type": "rss", "url": "https://blog.com/feed"}]

    def test_hackernews_source(self):
        sources = _parse_sources("- HN | hackernews")
        assert sources[0]["type"] == "hackernews"
        assert sources[0]["url"] == ""

    def test_audio_source(self):
        sources = _parse_sources("- Radio | audio | npr")
        assert sources[0]["type"] == "audio"
        assert sources[0]["url"] == "npr"

    def test_ignores_non_list_lines(self):
        sources = _parse_sources("# Header\nText\n- Valid | rss | https://x.com/feed")
        assert len(sources) == 1

    def test_ignores_malformed(self):
        sources = _parse_sources("- No pipe here")
        assert len(sources) == 0

    def test_ignores_unknown_types(self):
        sources = _parse_sources("- Bad | foobar | https://x.com")
        assert len(sources) == 0


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_text(self):
        assert _truncate("hello") == "hello"

    def test_long_text(self):
        result = _truncate("x" * 300, max_len=200)
        assert len(result) == 203  # 200 + "..."
        assert result.endswith("...")

    def test_strips_html(self):
        assert _truncate("<p>Hello</p>") == "Hello"

    def test_empty(self):
        assert _truncate("") == ""


# ---------------------------------------------------------------------------
# _fetch_rss (mocked)
# ---------------------------------------------------------------------------


class TestFetchRss:
    def test_formats_entries(self):
        mock_feed = MagicMock()
        entry = MagicMock()
        entry.title = "Headline"
        entry.link = "https://example.com/1"
        entry.summary = "A summary."
        mock_feed.entries = [entry]

        with patch(f"{_MODULE}.feedparser.parse", return_value=mock_feed):
            result = _fetch_rss("Feed", "https://example.com/rss")

        assert "### Feed" in result
        assert "**Headline**" in result
        assert "A summary." in result

    def test_empty_feed(self):
        mock_feed = MagicMock()
        mock_feed.entries = []
        with patch(f"{_MODULE}.feedparser.parse", return_value=mock_feed):
            result = _fetch_rss("Empty", "https://x.com/feed")
        assert "No entries" in result

    def test_fetch_error(self):
        with patch(f"{_MODULE}.feedparser.parse", side_effect=Exception("timeout")):
            result = _fetch_rss("Bad", "https://x.com/feed")
        assert "Failed" in result


# ---------------------------------------------------------------------------
# _fetch_rss_incremental
# ---------------------------------------------------------------------------


class TestFetchRssIncremental:
    def test_returns_new_items(self):
        mock_feed = MagicMock()
        entry1 = MagicMock()
        entry1.link = "https://example.com/old"
        entry1.title = "Old"
        entry1.summary = ""
        entry1.published = ""
        entry2 = MagicMock()
        entry2.link = "https://example.com/new"
        entry2.title = "New Article"
        entry2.summary = "Fresh content."
        entry2.published = "2026-03-24"
        mock_feed.entries = [entry1, entry2]

        with patch(f"{_MODULE}.feedparser.parse", return_value=mock_feed):
            text, new_seen = _fetch_rss_incremental(
                "Feed", "https://x.com/feed",
                seen_urls=["https://example.com/old"],
            )

        assert "**New Article**" in text
        assert "Old" not in text
        assert "https://example.com/new" in new_seen
        assert "https://example.com/old" not in new_seen

    def test_no_new_items(self):
        mock_feed = MagicMock()
        entry = MagicMock()
        entry.link = "https://example.com/1"
        entry.title = "Seen"
        mock_feed.entries = [entry]

        with patch(f"{_MODULE}.feedparser.parse", return_value=mock_feed):
            text, new_seen = _fetch_rss_incremental(
                "Feed", "https://x.com/feed",
                seen_urls=["https://example.com/1"],
            )

        assert "No new items" in text
        assert new_seen == []


# ---------------------------------------------------------------------------
# _fetch_hackernews (mocked)
# ---------------------------------------------------------------------------


class TestFetchHackernews:
    def test_formats_stories(self):
        mock_ids = MagicMock()
        mock_ids.json.return_value = [100]
        mock_ids.raise_for_status = MagicMock()

        mock_item = MagicMock()
        mock_item.json.return_value = {
            "title": "Show HN: Cool",
            "url": "https://cool.com",
            "score": 200,
            "descendants": 50,
        }

        def mock_get(url, **kw):
            if "topstories" in url:
                return mock_ids
            return mock_item

        with patch(f"{_MODULE}.requests.get", side_effect=mock_get):
            result = _fetch_hackernews("HN")

        assert "### HN" in result
        assert "**Show HN: Cool**" in result
        assert "200 pts" in result

    def test_api_error(self):
        with patch(f"{_MODULE}.requests.get", side_effect=Exception("down")):
            result = _fetch_hackernews("HN")
        assert "Failed" in result


# ---------------------------------------------------------------------------
# _fetch_audio
# ---------------------------------------------------------------------------


class TestFetchAudio:
    def test_npr(self):
        with patch(
            "prax.readers.news.npr_top_hour.get_latest_npr_podcast",
            return_value="https://npr.org/episode.mp3",
        ):
            result = _fetch_audio("NPR", "npr")
        assert "NPR" in result
        assert "https://npr.org/episode.mp3" in result

    def test_deutschlandfunk(self):
        with patch(
            "prax.readers.news.deutschlandfunk_radio.deutschlandfunk_process",
            return_value="https://dlf.de/broadcast.mp3",
        ):
            result = _fetch_audio("DLF", "deutschlandfunk")
        assert "DLF" in result
        assert "https://dlf.de/broadcast.mp3" in result

    def test_generic_url(self):
        result = _fetch_audio("Custom", "https://radio.example.com/stream")
        assert "https://radio.example.com/stream" in result

    def test_unknown(self):
        result = _fetch_audio("Mystery", "foobar")
        assert "Unknown" in result


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


class TestState:
    def test_roundtrip(self, workspace):
        _save_state(workspace, {"feed1": {"seen_urls": ["a", "b"], "last_checked": "now"}})
        state = _load_state(workspace)
        assert state["feed1"]["seen_urls"] == ["a", "b"]

    def test_missing_file(self, workspace):
        assert _load_state(workspace) == {}


# ---------------------------------------------------------------------------
# Action functions
# ---------------------------------------------------------------------------


class TestBuildBriefingContent:
    def test_includes_all_sources(self):
        sources = [
            {"name": "Feed1", "type": "rss", "url": "https://x.com/feed"},
            {"name": "HN", "type": "hackernews", "url": ""},
        ]
        with (
            patch(f"{_MODULE}._fetch_rss", return_value="### Feed1\n1. Article"),
            patch(f"{_MODULE}._fetch_hackernews", return_value="### HN\n1. Story"),
        ):
            result = _build_briefing_content(sources)

        assert "### Feed1" in result
        assert "### HN" in result

    def test_handles_fetch_error(self):
        sources = [{"name": "Bad", "type": "rss", "url": "https://x.com/feed"}]
        with patch(f"{_MODULE}._fetch_rss", side_effect=Exception("boom")):
            result = _build_briefing_content(sources)
        assert "Error" in result


class TestDoBriefing:
    def test_saves_to_library_outputs(self, workspace):
        sources = [{"name": "Feed1", "type": "rss", "url": "https://x.com/feed"}]
        with (
            patch(f"{_MODULE}._fetch_rss", return_value="### Feed1\n1. Article"),
            patch(
                "prax.services.library_service.write_output",
                return_value={"status": "written", "output": {"slug": "20260408-news-briefing"}},
            ) as mock_write,
        ):
            result = _do_briefing("test_user", sources)
        mock_write.assert_called_once()
        assert "20260408-news-briefing" in result
        assert "Digest" in result

    def test_falls_back_on_save_error(self, workspace):
        sources = [{"name": "Feed1", "type": "rss", "url": "https://x.com/feed"}]
        with (
            patch(f"{_MODULE}._fetch_rss", return_value="### Feed1\n1. Article"),
            patch(
                "prax.services.library_service.write_output",
                side_effect=RuntimeError("disk full"),
            ),
        ):
            result = _do_briefing("test_user", sources)
        assert "saving failed" in result
        assert "### Feed1" in result  # content still returned


class TestDoCheck:
    def test_incremental_check(self, workspace):
        sources = [{"name": "Blog", "type": "rss", "url": "https://x.com/feed"}]
        with patch(
            f"{_MODULE}._fetch_rss_incremental",
            return_value=("**Blog** — 2 new", ["a", "b"]),
        ):
            result = _do_check(workspace, sources, "")
        assert "2 new" in result
        # State should be saved
        state = _load_state(workspace)
        assert "Blog" in state

    def test_filter_by_name(self, workspace):
        sources = [
            {"name": "A", "type": "rss", "url": "https://a.com/feed"},
            {"name": "B", "type": "rss", "url": "https://b.com/feed"},
        ]
        with patch(
            f"{_MODULE}._fetch_rss_incremental",
            return_value=("**A** — 1 new", ["x"]),
        ) as mock_fetch:
            _do_check(workspace, sources, "A")
        # Should only check feed A
        mock_fetch.assert_called_once()
        assert mock_fetch.call_args[0][0] == "A"

    def test_unknown_feed_name(self, workspace):
        sources = [{"name": "Real", "type": "rss", "url": "https://x.com/feed"}]
        result = _do_check(workspace, sources, "Nope")
        assert "No RSS feed named" in result

    def test_no_rss_feeds(self, workspace):
        sources = [{"name": "HN", "type": "hackernews", "url": ""}]
        result = _do_check(workspace, sources, "")
        assert "No RSS feeds" in result


class TestDoSources:
    def test_lists_sources(self):
        sources = [
            {"name": "NYT", "type": "rss", "url": "https://nyt.com/feed"},
            {"name": "HN", "type": "hackernews", "url": ""},
        ]
        result = _do_sources(sources)
        assert "NYT" in result
        assert "HN" in result

    def test_no_sources(self):
        result = _do_sources([])
        assert "No sources" in result


class TestDoListen:
    def test_filters_audio(self):
        sources = [
            {"name": "NYT", "type": "rss", "url": "https://nyt.com/feed"},
            {"name": "NPR", "type": "audio", "url": "npr"},
        ]
        with patch(f"{_MODULE}._fetch_audio", return_value="### NPR\nLink"):
            result = _do_listen(sources, "")
        assert "NPR" in result

    def test_no_audio_sources(self):
        sources = [{"name": "NYT", "type": "rss", "url": "https://nyt.com/feed"}]
        result = _do_listen(sources, "")
        assert "No audio sources" in result

    def test_unknown_name(self):
        sources = [{"name": "NPR", "type": "audio", "url": "npr"}]
        result = _do_listen(sources, "BBC")
        assert "No audio source named" in result


# ---------------------------------------------------------------------------
# news tool (integrated)
# ---------------------------------------------------------------------------


class TestNewsTool:
    def test_briefing_action(self, workspace):
        with patch(f"{_MODULE}._do_briefing", return_value="## Briefing\nContent"):
            result = news.invoke({"action": "briefing"})
        assert "Briefing" in result

    def test_check_action(self, workspace):
        with patch(f"{_MODULE}._do_check", return_value="No new items."):
            result = news.invoke({"action": "check"})
        assert "No new items" in result

    def test_sources_action(self, workspace):
        result = news.invoke({"action": "sources"})
        assert "New York Times" in result

    def test_listen_action(self, workspace):
        with patch(f"{_MODULE}._do_listen", return_value="### NPR\nLink"):
            result = news.invoke({"action": "listen"})
        assert "NPR" in result

    def test_default_is_briefing(self, workspace):
        with patch(f"{_MODULE}._do_briefing", return_value="## Briefing") as mock_brief:
            news.invoke({})
        mock_brief.assert_called_once()

    def test_unknown_action(self, workspace):
        result = news.invoke({"action": "foobar"})
        assert "Unknown action" in result

    def test_no_user_context(self):
        with patch(f"{_MODULE}.current_user_id") as mock_uid:
            mock_uid.get.return_value = None
            result = news.invoke({"action": "briefing"})
        assert "no active user context" in result

    def test_creates_config_on_first_use(self, workspace):
        with patch(f"{_MODULE}._do_briefing", return_value="## Briefing"):
            news.invoke({"action": "briefing"})
        assert os.path.isfile(os.path.join(workspace, "news_sources.md"))

    def test_empty_config(self, workspace):
        _write_config(workspace, "# Empty\n")
        result = news.invoke({"action": "briefing"})
        assert "No news sources" in result


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


def test_register_returns_single_tool():
    tools = register()
    assert len(tools) == 1
    assert tools[0].name == "news"
