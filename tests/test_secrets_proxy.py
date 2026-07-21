"""Keyless tests for the secrets proxy (prax.secrets_proxy).

The upstream is mocked, so no network and no real keys. These pin the security
properties: the real key is injected + never leaked, client auth is stripped,
only allow-listed providers are reachable, and responses stream.
"""
from __future__ import annotations

import pytest

from prax.secrets_proxy.app import build_proxy_app
from prax.secrets_proxy.config import ProxyConfig

REAL_OPENAI = "sk-REAL-openai-KEY"
REAL_ANTHROPIC = "sk-ant-REAL-KEY"


class _FakeUpstream:
    def __init__(self, status=200, headers=None, chunks=(b'data: {"x":1}\n\n', b"data: [DONE]\n\n")):
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/event-stream", "Transfer-Encoding": "chunked"}
        self._chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size=8192):
        yield from self._chunks

    def close(self):
        self.closed = True


@pytest.fixture()
def proxy(monkeypatch):
    monkeypatch.setenv("OPENAI_KEY", REAL_OPENAI)
    monkeypatch.setenv("ANTHROPIC_KEY", REAL_ANTHROPIC)
    captured = {}

    def fake_request(method, url, headers=None, data=None, **kw):
        captured.update(method=method, url=url, headers=headers or {}, data=data, kw=kw)
        return _FakeUpstream()

    monkeypatch.setattr("prax.secrets_proxy.app.requests.request", fake_request)
    app = build_proxy_app(ProxyConfig())
    return app.test_client(), captured


def test_injects_real_openai_key_when_client_sent_none(proxy):
    client, captured = proxy
    r = client.post("/openai/v1/chat/completions", json={"model": "gpt-x"})
    assert r.status_code == 200
    # The REAL key was injected server-side...
    assert captured["headers"]["Authorization"] == f"Bearer {REAL_OPENAI}"
    # ...and forwarded to the right upstream path.
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["method"] == "POST"


def test_strips_and_replaces_client_supplied_auth(proxy):
    client, captured = proxy
    # Prax sends only a PLACEHOLDER key — the proxy must replace, never forward it.
    client.post("/openai/v1/chat/completions",
                headers={"Authorization": "Bearer proxy-placeholder"}, json={})
    assert captured["headers"]["Authorization"] == f"Bearer {REAL_OPENAI}"
    assert "proxy-placeholder" not in captured["headers"]["Authorization"]


def test_anthropic_injects_x_api_key_and_version(proxy):
    client, captured = proxy
    client.post("/anthropic/v1/messages",
                headers={"x-api-key": "placeholder"}, json={"model": "claude-x"})
    assert captured["headers"]["x-api-key"] == REAL_ANTHROPIC
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in captured["headers"]  # anthropic uses x-api-key


def test_unknown_provider_is_404_never_forwarded(proxy):
    client, captured = proxy
    r = client.post("/evil/v1/exfil", json={})
    assert r.status_code == 404
    assert captured == {}  # allowlist: nothing was forwarded


def test_missing_key_is_502(monkeypatch):
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_KEY", raising=False)
    monkeypatch.setattr("prax.secrets_proxy.app.requests.request",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not forward")))
    client = build_proxy_app(ProxyConfig()).test_client()
    r = client.post("/openai/v1/chat/completions", json={})
    assert r.status_code == 502 and "no key" in r.get_json()["error"]


def test_response_streams_through(proxy):
    client, _ = proxy
    r = client.post("/openai/v1/chat/completions", json={})
    body = r.get_data(as_text=True)
    assert 'data: {"x":1}' in body and "[DONE]" in body


def test_hop_by_hop_headers_not_forwarded(proxy):
    client, captured = proxy
    client.post("/openai/v1/chat/completions",
                headers={"Host": "evil.com", "Connection": "keep-alive",
                         "Content-Type": "application/json"}, json={})
    fwd = {k.lower() for k in captured["headers"]}
    assert "host" not in fwd and "connection" not in fwd
    assert "content-type" in fwd  # real headers pass through


def test_query_string_is_preserved(proxy):
    client, captured = proxy
    client.get("/openai/v1/models?limit=5")
    assert captured["url"].endswith("/v1/models?limit=5")


def test_healthz_reports_readiness_without_values(proxy):
    client, _ = proxy
    j = client.get("/healthz").get_json()
    assert j["ok"] is True
    assert j["providers"] == {"openai": True, "anthropic": True}
    # the actual key value must never appear anywhere in the health payload
    assert REAL_OPENAI not in str(j)


def test_audit_log_never_contains_the_key_or_body(proxy, caplog):
    import logging
    client, _ = proxy
    with caplog.at_level(logging.INFO, logger="prax.secrets_proxy"):
        client.post("/openai/v1/chat/completions", json={"secret_in_body": "hunter2"})
    logged = "\n".join(r.message for r in caplog.records)
    assert "/v1/chat/completions" in logged      # the call IS audited
    assert REAL_OPENAI not in logged             # ...but never the key
    assert "hunter2" not in logged               # ...nor the request body
