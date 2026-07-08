"""Tests for the SEARCH_PROVIDER flag on background_search."""
from __future__ import annotations

import asyncio

import prax.helpers_functions as hf


def _set_provider(monkeypatch, value: str) -> None:
    import prax.settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, "search_provider", value, raising=False)
    monkeypatch.setattr(hf.settings, "search_provider", value, raising=False)


def test_default_legacy_uses_langchain_wrapper(monkeypatch):
    called = {}

    class _StubTool:
        def run(self, q):
            called["q"] = q
            return "legacy results"

    # search_tool is a pydantic BaseTool (rejects attribute patches) — swap
    # the module-level instance instead.
    monkeypatch.setattr(hf, "search_tool", _StubTool())
    _set_provider(monkeypatch, "legacy")
    out = asyncio.run(hf.background_search("test query", to_number=None, sms_bool=False))
    assert out == "legacy results" and called["q"] == "test query"


def test_ddgs_provider_formats_cited_snippets(monkeypatch):
    class _FakeDDGS:
        def __init__(self, timeout=None):
            pass

        def text(self, query, max_results=None):
            assert query == "test query"
            return [
                {"title": "Result One", "body": "Snippet one.", "href": "https://a.example"},
                {"title": "Result Two", "body": "Snippet two.", "href": "https://b.example"},
            ]

    import sys
    import types
    fake_mod = types.ModuleType("ddgs")
    fake_mod.DDGS = _FakeDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake_mod)
    _set_provider(monkeypatch, "ddgs")
    out = asyncio.run(hf.background_search("test query", to_number=None, sms_bool=False))
    assert "- Result One — Snippet one. (https://a.example)" in out
    assert "https://b.example" in out


def test_ddgs_empty_results_graceful(monkeypatch):
    class _FakeDDGS:
        def __init__(self, timeout=None):
            pass

        def text(self, query, max_results=None):
            return []

    import sys
    import types
    fake_mod = types.ModuleType("ddgs")
    fake_mod.DDGS = _FakeDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake_mod)
    _set_provider(monkeypatch, "ddgs")
    out = asyncio.run(hf.background_search("q", to_number=None, sms_bool=False))
    assert out == "No search results found."
