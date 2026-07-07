"""Key-free tests for the Bluesky + Threads fetch paths in url_reader."""
from __future__ import annotations

import prax.services.url_reader as ur


class _R:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload

    def json(self):
        return self._p


# --- Bluesky ---------------------------------------------------------------

def test_bsky_regex():
    m = ur._BSKY_POST_RE.search("https://bsky.app/profile/alice.bsky.social/post/3kabc")
    assert m.group(1) == "alice.bsky.social" and m.group(2) == "3kabc"
    assert ur._BSKY_POST_RE.search("https://bsky.app/profile/alice") is None


def test_bsky_resolves_handle_then_fetches(monkeypatch):
    def fake_get(url, params=None, timeout=None, **k):
        if "resolveHandle" in url:
            assert params["handle"] == "alice.bsky.social"
            return _R(200, {"did": "did:plc:xyz"})
        if "getPosts" in url:
            assert params["uris"] == "at://did:plc:xyz/app.bsky.feed.post/3kabc"
            return _R(200, {"posts": [{
                "author": {"displayName": "Alice", "handle": "alice.bsky.social"},
                "record": {"text": "hello bsky"}, "likeCount": 3}]})
        return _R(404, {})

    monkeypatch.setattr(ur.requests, "get", fake_get)
    md = ur.fetch_bsky_via_api("https://bsky.app/profile/alice.bsky.social/post/3kabc")
    assert "hello bsky" in md and "@alice.bsky.social" in md and "3 likes" in md


def test_bsky_did_url_skips_resolve(monkeypatch):
    def fake_get(url, params=None, timeout=None, **k):
        assert "resolveHandle" not in url  # a DID URL needs no handle resolution
        return _R(200, {"posts": [{"author": {"handle": "h"}, "record": {"text": "t"}}]})

    monkeypatch.setattr(ur.requests, "get", fake_get)
    assert "t" in ur.fetch_bsky_via_api("https://bsky.app/profile/did:plc:abc/post/3kx")


def test_bsky_non_post_url_is_none():
    assert ur.fetch_bsky_via_api("https://bsky.app/profile/alice") is None


# --- Threads ---------------------------------------------------------------

def test_threads_regex_and_shortcode_decode():
    assert ur._THREADS_POST_RE.search("https://www.threads.net/@bob/post/ABC").group(1) == "ABC"
    assert ur._THREADS_POST_RE.search("https://threads.com/t/XyZ_9").group(1) == "XyZ_9"
    assert ur._shortcode_to_media_id("B") == 1          # 'B' → index 1
    assert ur._shortcode_to_media_id("!!") is None      # invalid char


def test_threads_none_without_token(monkeypatch):
    monkeypatch.setattr(ur, "_threads_token", lambda: "")
    assert ur.fetch_threads_via_api("https://threads.net/@u/post/ABC") is None


def test_threads_fetch_success(monkeypatch):
    monkeypatch.setattr(ur, "_threads_token", lambda: "tok")
    monkeypatch.setattr(ur.requests, "get",
                        lambda *a, **k: _R(200, {"text": "a thread", "username": "bob",
                                                 "timestamp": "2026-07-06"}))
    md = ur.fetch_threads_via_api("https://www.threads.net/@bob/post/ABC")
    assert "a thread" in md and "@bob" in md


def test_threads_access_denied_falls_back(monkeypatch):
    monkeypatch.setattr(ur, "_threads_token", lambda: "tok")
    monkeypatch.setattr(ur.requests, "get", lambda *a, **k: _R(400, {}))
    assert ur.fetch_threads_via_api("https://threads.net/@u/post/ABC") is None


# --- routing through fetch_markdown ---------------------------------------

def test_fetch_markdown_routes_bluesky(monkeypatch):
    monkeypatch.setattr(ur, "fetch_bsky_via_api", lambda url, timeout=15: "# Bluesky post\n\nvia api")

    def _boom(*a, **k):
        raise AssertionError("should not hit the web reader for a bsky post")

    monkeypatch.setattr(ur.requests, "get", _boom)
    assert "via api" in ur.fetch_markdown("https://bsky.app/profile/a/post/1")
