"""workspace_download: SSRF guard with per-hop revalidation; filename confined.

Review 2026-09-05: the tool fetched a model-controlled URL with a bare
``requests.get(allow_redirects=True)`` — ``http://127.0.0.1:6333`` (Qdrant),
``http://169.254.169.254`` (cloud metadata), or a public URL redirecting to
either. The file still lands in the workspace root as before (``safe_join``
now confines the name there; ``workspace_send_file`` checks active/ and then
the root, so it is deliverable). All network is mocked.
"""
from __future__ import annotations

import pytest
import requests

from prax.agent import workspace_tools as wt
from prax.agent.user_context import current_user_id
from prax.services import workspace_service
from prax.utils import ssrf

PUBLIC_ADDRINFO = [(2, 1, 6, "", ("93.184.216.34", 443))]


class _Resp:
    def __init__(self, status=200, headers=None, body=b""):
        self.status_code = status
        self.headers = headers or {}
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def iter_content(self, chunk_size=8192):
        yield self._body


@pytest.fixture
def user_ws(tmp_path, monkeypatch):
    ws = tmp_path / "ws" / "usr_d"
    ws.mkdir(parents=True)
    monkeypatch.setattr(workspace_service, "workspace_root", lambda uid: str(ws))
    monkeypatch.setattr(ssrf.settings, "ssrf_protection_enabled", True)
    monkeypatch.setattr(ssrf.settings, "ssrf_allowed_hosts", "")
    token = current_user_id.set("usr_d")
    yield ws
    current_user_id.reset(token)


def test_internal_targets_refused_without_any_network_call(user_ws, monkeypatch):
    calls = []

    def _no_network(*a, **k):
        calls.append(a)
        raise AssertionError("network call made")

    monkeypatch.setattr(requests, "get", _no_network)
    for url in ("http://127.0.0.1:6333/collections", "http://169.254.169.254/latest/meta-data/"):
        out = wt.workspace_download.invoke({"url": url})
        assert out.startswith("Failed to download"), out
        assert "blocked" in out, out
    assert calls == []
    assert list(user_ws.rglob("*")) == []


def test_redirect_to_internal_is_revalidated_and_refused(user_ws, monkeypatch):
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", lambda *a, **k: PUBLIC_ADDRINFO)
    seen = []

    def fake_get(url, **kw):
        seen.append(url)
        return _Resp(302, {"Location": "http://169.254.169.254/latest/meta-data/"})

    monkeypatch.setattr(requests, "get", fake_get)
    out = wt.workspace_download.invoke({"url": "https://example.com/file.pdf"})
    assert out.startswith("Failed to download") and "blocked" in out, out
    assert seen == ["https://example.com/file.pdf"]  # the internal hop was never fetched
    assert list(user_ws.rglob("*")) == []


def test_public_download_follows_public_redirect_and_lands_in_workspace_root(user_ws, monkeypatch):
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", lambda *a, **k: PUBLIC_ADDRINFO)
    seen = []

    def fake_get(url, **kw):
        seen.append(url)
        assert kw.get("allow_redirects") is False  # hops are followed by the guard, not requests
        if url.endswith("/file.pdf"):
            return _Resp(302, {"Location": "https://cdn.example.com/real.pdf"})
        return _Resp(200, {"content-type": "application/pdf"}, b"%PDF-1.4 fake")

    monkeypatch.setattr(requests, "get", fake_get)
    out = wt.workspace_download.invoke({"url": "https://example.com/file.pdf"})
    assert out.startswith("Downloaded file.pdf"), out
    assert seen == ["https://example.com/file.pdf", "https://cdn.example.com/real.pdf"]
    assert out.endswith("to workspace."), out
    assert (user_ws / "file.pdf").read_bytes() == b"%PDF-1.4 fake"


def test_filename_is_sanitised_and_confined_to_workspace(user_ws, monkeypatch):
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", lambda *a, **k: PUBLIC_ADDRINFO)
    monkeypatch.setattr(
        requests, "get",
        lambda url, **kw: _Resp(200, {"content-type": "application/octet-stream"}, b"x"),
    )
    out = wt.workspace_download.invoke({"url": "https://example.com/x", "filename": ".."})
    assert out.startswith("Downloaded download.bin"), out
    assert (user_ws / "download.bin").is_file()
    out = wt.workspace_download.invoke({"url": "https://example.com/x", "filename": "../../etc/evil"})
    assert out.startswith("Downloaded"), out
    written = [p for p in user_ws.rglob("*") if p.is_file()]
    assert written and all(p.parent == user_ws for p in written), written
