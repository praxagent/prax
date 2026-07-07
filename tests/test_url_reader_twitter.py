"""Key-free tests for the X/Twitter API fetch path in url_reader."""
from __future__ import annotations

import prax.services.url_reader as ur


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload

    def json(self):
        return self._p


def test_x_status_regex_variants():
    assert ur._X_STATUS_RE.search("https://x.com/jack/status/1234567890").group(1) == "1234567890"
    assert ur._X_STATUS_RE.search("https://twitter.com/u/status/42").group(1) == "42"
    assert ur._X_STATUS_RE.search("https://mobile.twitter.com/u/statuses/7").group(1) == "7"
    assert ur._X_STATUS_RE.search("https://www.x.com/a_b/status/9").group(1) == "9"
    assert ur._X_STATUS_RE.search("https://x.com/jack") is None  # profile, not a status


def test_none_without_token(monkeypatch):
    monkeypatch.setattr(ur, "_twitter_token", lambda: "")
    assert ur.fetch_tweet_via_api("https://x.com/u/status/1") is None


def test_none_for_non_tweet_url(monkeypatch):
    monkeypatch.setattr(ur, "_twitter_token", lambda: "tok")
    assert ur.fetch_tweet_via_api("https://example.com/article") is None


def test_success_returns_markdown(monkeypatch):
    monkeypatch.setattr(ur, "_twitter_token", lambda: "tok")
    payload = {
        "data": {"author_id": "9", "text": "hello world",
                 "created_at": "2026-07-05T00:00:00Z",
                 "public_metrics": {"like_count": 5, "retweet_count": 2, "reply_count": 1}},
        "includes": {"users": [{"id": "9", "name": "Jack", "username": "jack"}]},
    }
    monkeypatch.setattr(ur.requests, "get", lambda *a, **k: _Resp(200, payload))
    md = ur.fetch_tweet_via_api("https://x.com/jack/status/123")
    assert "hello world" in md and "@jack" in md and "Jack" in md and "5 likes" in md


def test_long_note_tweet_uses_full_body(monkeypatch):
    monkeypatch.setattr(ur, "_twitter_token", lambda: "tok")
    payload = {"data": {"author_id": "9", "text": "short", "note_tweet": {"text": "the full long body"}},
               "includes": {"users": [{"id": "9", "name": "N", "username": "h"}]}}
    monkeypatch.setattr(ur.requests, "get", lambda *a, **k: _Resp(200, payload))
    md = ur.fetch_tweet_via_api("https://x.com/h/status/1")
    assert "the full long body" in md and "short" not in md


def test_api_error_falls_back(monkeypatch):
    monkeypatch.setattr(ur, "_twitter_token", lambda: "tok")
    monkeypatch.setattr(ur.requests, "get", lambda *a, **k: _Resp(401, {}))
    assert ur.fetch_tweet_via_api("https://x.com/u/status/1") is None


def test_fetch_markdown_routes_tweets_through_api(monkeypatch):
    # when the tweet API returns content, fetch_markdown returns it and never
    # touches the Jina reader.
    monkeypatch.setattr(ur, "fetch_tweet_via_api", lambda url, timeout=15: "# Tweet\n\nvia api")

    def _boom(*a, **k):
        raise AssertionError("should not hit the web reader for a tweet")

    monkeypatch.setattr(ur.requests, "get", _boom)
    out = ur.fetch_markdown("https://x.com/u/status/1")
    assert "via api" in out


# --- thread expansion (TWITTER_THREAD_FETCH) --------------------------------

def _thread_payloads():
    root = {
        "id": "100", "author_id": "9", "conversation_id": "100",
        "text": "root post https://t.co/abc",
        "entities": {"urls": [{"url": "https://t.co/abc",
                               "expanded_url": "https://example.com/paper"}]},
        "created_at": "2026-07-07T14:00:00Z",
        "public_metrics": {"like_count": 71, "retweet_count": 17, "reply_count": 7},
    }
    replies = [
        {"id": "101", "conversation_id": "100", "text": "second post"},
        {"id": "102", "conversation_id": "100", "text": "long third post trunc…",
         "note_tweet": {"text": "long third post https://t.co/xyz",
                        "entities": {"urls": [{"url": "https://t.co/xyz",
                                               "expanded_url": "https://arxiv.org/abs/1"}]}}},
    ]
    includes = {"users": [{"id": "9", "name": "Jean", "username": "jrking"}]}
    return root, replies, includes


def test_thread_fetch_off_by_default(monkeypatch):
    monkeypatch.setattr(ur, "_twitter_token", lambda: "tok")
    root, _, includes = _thread_payloads()

    def fake_get(url, params=None, headers=None, timeout=None, **k):
        assert "search/recent" not in url, "flag off — must not call search"
        return _Resp(200, {"data": root, "includes": includes})

    monkeypatch.setattr(ur.requests, "get", fake_get)
    md = ur.fetch_tweet_via_api("https://x.com/jrking/status/100")
    assert "# Tweet by Jean (@jrking)" in md
    # t.co links are expanded even in the single-tweet render
    assert "https://example.com/paper" in md and "t.co/abc" not in md


def test_thread_fetch_assembles_self_thread(monkeypatch):
    monkeypatch.setattr(ur, "_twitter_token", lambda: "tok")
    monkeypatch.setattr(ur, "_thread_fetch_enabled", lambda: True)
    root, replies, includes = _thread_payloads()

    def fake_get(url, params=None, headers=None, timeout=None, **k):
        if "search/recent" in url:
            q = params["query"]
            assert "conversation_id:100" in q
            assert "from:jrking" in q and "to:jrking" in q
            # search returns newest-first; assembly must re-sort by id
            return _Resp(200, {"data": list(reversed(replies)), "meta": {}})
        assert url.endswith("/tweets/100")
        return _Resp(200, {"data": root, "includes": includes})

    monkeypatch.setattr(ur.requests, "get", fake_get)
    md = ur.fetch_tweet_via_api("https://x.com/jrking/status/100")
    assert "# X thread by Jean (@jrking) — 3 posts" in md
    assert "## 1/3" in md and "## 3/3" in md
    assert md.index("root post") < md.index("second post") < md.index("long third post")
    assert "https://arxiv.org/abs/1" in md   # note_tweet entities expanded
    assert "trunc…" not in md                # full note body used, not the preview
    assert "71 likes · 17 reposts · 7 replies (root post)" in md


def test_thread_fetch_falls_back_when_search_denied(monkeypatch):
    monkeypatch.setattr(ur, "_twitter_token", lambda: "tok")
    monkeypatch.setattr(ur, "_thread_fetch_enabled", lambda: True)
    root, _, includes = _thread_payloads()

    def fake_get(url, params=None, headers=None, timeout=None, **k):
        if "search/recent" in url:
            return _Resp(403, {})  # tier without search access
        return _Resp(200, {"data": root, "includes": includes})

    monkeypatch.setattr(ur.requests, "get", fake_get)
    md = ur.fetch_tweet_via_api("https://x.com/jrking/status/100")
    assert "# Tweet by Jean (@jrking)" in md  # graceful single-tweet render


def test_thread_fetch_single_when_no_self_replies(monkeypatch):
    monkeypatch.setattr(ur, "_twitter_token", lambda: "tok")
    monkeypatch.setattr(ur, "_thread_fetch_enabled", lambda: True)
    root, _, includes = _thread_payloads()

    def fake_get(url, params=None, headers=None, timeout=None, **k):
        if "search/recent" in url:
            return _Resp(200, {"data": [], "meta": {}})  # replies are all from others
        return _Resp(200, {"data": root, "includes": includes})

    monkeypatch.setattr(ur.requests, "get", fake_get)
    md = ur.fetch_tweet_via_api("https://x.com/jrking/status/100")
    assert "# Tweet by Jean (@jrking)" in md
    assert "X thread" not in md


def test_thread_fetch_mid_thread_link_pulls_root(monkeypatch):
    monkeypatch.setattr(ur, "_twitter_token", lambda: "tok")
    monkeypatch.setattr(ur, "_thread_fetch_enabled", lambda: True)
    root, replies, includes = _thread_payloads()
    linked = dict(replies[0])
    linked["author_id"] = "9"
    linked["public_metrics"] = {"reply_count": 2}

    def fake_get(url, params=None, headers=None, timeout=None, **k):
        if "search/recent" in url:
            return _Resp(200, {"data": replies, "meta": {}})
        if url.endswith("/tweets/101"):
            return _Resp(200, {"data": linked, "includes": includes})
        if url.endswith("/tweets/100"):
            return _Resp(200, {"data": root, "includes": includes})
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(ur.requests, "get", fake_get)
    md = ur.fetch_tweet_via_api("https://x.com/jrking/status/101")
    assert "3 posts" in md and "root post" in md  # root recovered from mid-thread link


def test_thread_truncation_marker(monkeypatch):
    monkeypatch.setattr(ur, "_twitter_token", lambda: "tok")
    monkeypatch.setattr(ur, "_thread_fetch_enabled", lambda: True)
    root, replies, includes = _thread_payloads()

    def fake_get(url, params=None, headers=None, timeout=None, **k):
        if "search/recent" in url:
            return _Resp(200, {"data": replies, "meta": {"next_token": "more"}})
        return _Resp(200, {"data": root, "includes": includes})

    monkeypatch.setattr(ur.requests, "get", fake_get)
    md = ur.fetch_tweet_via_api("https://x.com/jrking/status/100")
    assert "[Thread truncated" in md


# --- fetch_markdown_with_source ----------------------------------------------

def test_fetch_markdown_with_source_labels_api(monkeypatch):
    monkeypatch.setattr(ur, "fetch_tweet_via_api", lambda url, timeout=15: "# Tweet\n\nvia api")
    md, source = ur.fetch_markdown_with_source("https://x.com/u/status/1")
    assert source == "x-api" and "via api" in md


def test_fetch_markdown_with_source_web_reader(monkeypatch):
    class _Page:
        status_code = 200
        text = "long page content " * 10

    monkeypatch.setattr(ur.requests, "get", lambda *a, **k: _Page())
    md, source = ur.fetch_markdown_with_source("https://example.com/article")
    assert source == "web-reader" and "long page content" in md
