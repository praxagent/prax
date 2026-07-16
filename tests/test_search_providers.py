"""Keyless tests for the multi-provider web search (brave/jina/tavily/ddgs).

All HTTP is mocked — no network, no keys. Verifies request shape, response
parsing into the shared grounding format, missing-key handling, and that a
provider failure degrades to a clear string instead of crashing the turn.
"""
from __future__ import annotations

import asyncio
import sys
import types

import prax.helpers_functions as hf


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fake_requests(monkeypatch, *, get=None, post=None, capture=None):
    mod = types.ModuleType("requests")

    def _get(url, **kw):
        if capture is not None:
            capture.update({"method": "GET", "url": url, **kw})
        return get(url, **kw) if callable(get) else get

    def _post(url, **kw):
        if capture is not None:
            capture.update({"method": "POST", "url": url, **kw})
        return post(url, **kw) if callable(post) else post

    mod.get, mod.post = _get, _post
    monkeypatch.setitem(sys.modules, "requests", mod)


def test_brave_parses_and_requires_key(monkeypatch):
    monkeypatch.setattr(hf.settings, "brave_api_key", None, raising=False)
    assert "BRAVE_API_KEY" in hf._brave_search("x")

    monkeypatch.setattr(hf.settings, "brave_api_key", "bk", raising=False)
    monkeypatch.setattr(hf.settings, "search_max_results", 3, raising=False)
    cap = {}
    payload = {"web": {"results": [
        {"title": "T1", "description": "snippet one", "url": "http://a"},
        {"title": "T2", "description": "snippet two", "url": "http://b"},
    ]}}
    _fake_requests(monkeypatch, get=_Resp(payload), capture=cap)
    out = hf._brave_search("solar eclipse")
    assert cap["headers"]["X-Subscription-Token"] == "bk"
    assert cap["params"] == {"q": "solar eclipse", "count": 3}
    assert "- T1 — snippet one (http://a)" in out and "T2" in out


def test_tavily_surfaces_answer_and_requires_key(monkeypatch):
    monkeypatch.setattr(hf.settings, "tavily_api_key", None, raising=False)
    assert "TAVILY_API_KEY" in hf._tavily_search("x")

    monkeypatch.setattr(hf.settings, "tavily_api_key", "tv", raising=False)
    cap = {}
    payload = {"answer": "42 is the answer",
               "results": [{"title": "R", "content": "body", "url": "http://c"}]}
    _fake_requests(monkeypatch, post=_Resp(payload), capture=cap)
    out = hf._tavily_search("meaning of life")
    assert cap["url"] == "https://api.tavily.com/search"
    assert cap["json"]["api_key"] == "tv" and cap["json"]["include_answer"] is True
    assert out.startswith("Answer: 42 is the answer")
    assert "- R — body (http://c)" in out


def test_serper_surfaces_answer_box_and_requires_key(monkeypatch):
    monkeypatch.setattr(hf.settings, "serper_dev_api_key", None, raising=False)
    assert "SERPER_DEV_API_KEY" in hf._serper_search("x")

    monkeypatch.setattr(hf.settings, "serper_dev_api_key", "sk", raising=False)
    monkeypatch.setattr(hf.settings, "search_max_results", 2, raising=False)
    cap = {}
    payload = {"answerBox": {"answer": "Argentina"},
               "organic": [{"title": "O1", "snippet": "body one", "link": "http://a"},
                           {"title": "O2", "snippet": "body two", "link": "http://b"}]}
    _fake_requests(monkeypatch, post=_Resp(payload), capture=cap)
    out = hf._serper_search("2022 world cup winner")
    assert cap["url"] == "https://google.serper.dev/search"
    assert cap["headers"]["X-API-KEY"] == "sk"
    assert cap["json"] == {"q": "2022 world cup winner", "num": 2}
    assert out.startswith("Answer: Argentina")
    assert "- O1 — body one (http://a)" in out and "O2" in out


def test_serper_falls_back_to_knowledge_graph_answer(monkeypatch):
    # No answerBox → knowledge-graph description becomes the synthesised answer.
    monkeypatch.setattr(hf.settings, "serper_dev_api_key", "sk", raising=False)
    payload = {"knowledgeGraph": {"description": "A country in South America"},
               "organic": [{"title": "O", "snippet": "b", "link": "http://c"}]}
    _fake_requests(monkeypatch, post=_Resp(payload))
    out = hf._serper_search("argentina")
    assert out.startswith("Answer: A country in South America")


def test_jina_requires_key_and_uses_bearer(monkeypatch):
    # Unlike the Jina reader, the search endpoint rejects keyless requests
    # (401, verified live 2026-07-08) — so no key returns an actionable message
    # instead of a doomed call.
    monkeypatch.setattr(hf.settings, "jina_api_key", None, raising=False)
    assert "JINA_API_KEY" in hf._jina_search("query")

    # With a key: bearer token + metadata-only header + parse.
    monkeypatch.setattr(hf.settings, "jina_api_key", "jk", raising=False)
    cap = {}
    payload = {"data": [{"title": "J", "description": "desc", "url": "http://d"}]}
    _fake_requests(monkeypatch, get=_Resp(payload), capture=cap)
    out = hf._jina_search("query")
    assert cap["url"] == "https://s.jina.ai/"
    assert cap["headers"]["X-Respond-With"] == "no-content"
    assert cap["headers"]["Authorization"] == "Bearer jk"
    assert "- J — desc (http://d)" in out


def test_empty_results_message(monkeypatch):
    monkeypatch.setattr(hf.settings, "brave_api_key", "bk", raising=False)
    _fake_requests(monkeypatch, get=_Resp({"web": {"results": []}}))
    assert hf._brave_search("nothing") == "No search results found."


def test_dispatch_routes_to_provider(monkeypatch):
    monkeypatch.setattr(hf.settings, "search_provider", "tavily", raising=False)
    monkeypatch.setattr(hf, "_tavily_search", lambda q: f"tavily::{q}")
    out = asyncio.run(hf.background_search("q1", to_number=None, sms_bool=False))
    assert out == "tavily::q1"


def test_dispatch_failure_degrades_gracefully(monkeypatch):
    monkeypatch.setattr(hf.settings, "search_provider", "brave", raising=False)

    def _boom(q):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(hf, "_brave_search", _boom)
    out = asyncio.run(hf.background_search("q", to_number=None, sms_bool=False))
    assert "Search via 'brave' failed" in out and "connection reset" in out


def test_legacy_provider_uses_search_tool(monkeypatch):
    monkeypatch.setattr(hf.settings, "search_provider", "legacy", raising=False)

    class _FakeTool:
        def run(self, q):
            return f"legacy::{q}"

    monkeypatch.setattr(hf, "search_tool", _FakeTool())  # Pydantic model — replace whole
    out = asyncio.run(hf.background_search("q2", to_number=None, sms_bool=False))
    assert out == "legacy::q2"
