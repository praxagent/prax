"""Tests for prax.services.url_reader — the Jina Reader wrapper.

Covers the JINA_API_KEY propagation, error handling, and the
fetch_markdown / fetch_markdown_and_title / try_fetch_markdown API
shapes used by the orchestrator, note pipeline, and SMS auto-capture.
"""
from __future__ import annotations

import pytest

from prax.services import url_reader


class _FakeResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def capture_request(monkeypatch):
    """Patch requests.get and record the call args."""
    calls: list[dict] = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return calls[-1].setdefault("_response", _FakeResponse(200, "Title: X\n\nBody " + "x" * 100))

    monkeypatch.setattr(url_reader.requests, "get", fake_get)
    return calls


class TestHeaderAuth:
    def test_no_api_key_sends_no_auth_header(self, capture_request, monkeypatch):
        monkeypatch.setattr(url_reader.settings, "jina_api_key", None)
        url_reader.fetch_markdown("https://example.com/article")
        headers = capture_request[0]["headers"]
        assert "Authorization" not in headers
        assert headers["Accept"] == "text/markdown"

    def test_api_key_is_sent_as_bearer(self, capture_request, monkeypatch):
        monkeypatch.setattr(url_reader.settings, "jina_api_key", "jina_test_xyz")
        url_reader.fetch_markdown("https://example.com/article")
        headers = capture_request[0]["headers"]
        assert headers["Authorization"] == "Bearer jina_test_xyz"

    def test_empty_api_key_is_not_sent(self, capture_request, monkeypatch):
        """Empty string should be treated as no key, not 'Bearer '."""
        monkeypatch.setattr(url_reader.settings, "jina_api_key", "")
        url_reader.fetch_markdown("https://example.com/article")
        headers = capture_request[0]["headers"]
        assert "Authorization" not in headers


class TestFetchMarkdown:
    def test_prepends_reader_base_url(self, capture_request, monkeypatch):
        monkeypatch.setattr(url_reader.settings, "jina_api_key", None)
        url_reader.fetch_markdown("https://example.com/foo")
        assert capture_request[0]["url"] == "https://r.jina.ai/https://example.com/foo"

    def test_raises_on_http_error(self, monkeypatch):
        def fake_get(url, **kw):
            return _FakeResponse(502, "bad gateway")
        monkeypatch.setattr(url_reader.requests, "get", fake_get)
        monkeypatch.setattr(url_reader.settings, "jina_api_key", None)
        with pytest.raises(url_reader.ReaderError):
            url_reader.fetch_markdown("https://example.com/x")

    def test_raises_on_minimal_content(self, monkeypatch):
        def fake_get(url, **kw):
            return _FakeResponse(200, "short")  # < 50 chars
        monkeypatch.setattr(url_reader.requests, "get", fake_get)
        monkeypatch.setattr(url_reader.settings, "jina_api_key", None)
        with pytest.raises(url_reader.ReaderError):
            url_reader.fetch_markdown("https://example.com/x")

    def test_raises_on_request_exception(self, monkeypatch):
        import requests as _requests
        def fake_get(url, **kw):
            raise _requests.ConnectionError("timeout")
        monkeypatch.setattr(url_reader.requests, "get", fake_get)
        monkeypatch.setattr(url_reader.settings, "jina_api_key", None)
        with pytest.raises(url_reader.ReaderError) as excinfo:
            url_reader.fetch_markdown("https://example.com/x")
        assert "timeout" in str(excinfo.value).lower()

    def test_truncates_long_content(self, monkeypatch):
        long_body = "x" * 100_000
        def fake_get(url, **kw):
            return _FakeResponse(200, long_body)
        monkeypatch.setattr(url_reader.requests, "get", fake_get)
        monkeypatch.setattr(url_reader.settings, "jina_api_key", None)
        result = url_reader.fetch_markdown("https://example.com/x", max_chars=1000)
        assert len(result) < 2000
        assert "[Content truncated]" in result


class TestFetchMarkdownAndTitle:
    def test_extracts_title_when_present(self, monkeypatch):
        def fake_get(url, **kw):
            return _FakeResponse(200, "Title: Real Title\n\nBody " + "x" * 100)
        monkeypatch.setattr(url_reader.requests, "get", fake_get)
        monkeypatch.setattr(url_reader.settings, "jina_api_key", None)
        body, title = url_reader.fetch_markdown_and_title("https://example.com/x")
        assert title == "Real Title"
        assert body.startswith("Body")
        assert "Title:" not in body

    def test_empty_title_when_absent(self, monkeypatch):
        def fake_get(url, **kw):
            return _FakeResponse(200, "No title here\n\n" + "x" * 100)
        monkeypatch.setattr(url_reader.requests, "get", fake_get)
        monkeypatch.setattr(url_reader.settings, "jina_api_key", None)
        body, title = url_reader.fetch_markdown_and_title("https://example.com/x")
        assert title == ""
        assert body.startswith("No title here")


class TestTryFetchMarkdown:
    def test_returns_content_on_success(self, monkeypatch):
        def fake_get(url, **kw):
            return _FakeResponse(200, "Good content " + "x" * 100)
        monkeypatch.setattr(url_reader.requests, "get", fake_get)
        monkeypatch.setattr(url_reader.settings, "jina_api_key", None)
        result = url_reader.try_fetch_markdown("https://example.com/x")
        assert result is not None
        assert "Good content" in result

    def test_returns_none_on_reader_error(self, monkeypatch):
        def fake_get(url, **kw):
            return _FakeResponse(500, "server error")
        monkeypatch.setattr(url_reader.requests, "get", fake_get)
        monkeypatch.setattr(url_reader.settings, "jina_api_key", None)
        assert url_reader.try_fetch_markdown("https://example.com/x") is None

    def test_returns_none_on_unexpected_exception(self, monkeypatch):
        def fake_get(url, **kw):
            raise ValueError("something unexpected")
        monkeypatch.setattr(url_reader.requests, "get", fake_get)
        monkeypatch.setattr(url_reader.settings, "jina_api_key", None)
        assert url_reader.try_fetch_markdown("https://example.com/x") is None
