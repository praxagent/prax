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
