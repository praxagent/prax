"""Tests for the SSRF egress guard (prax.utils.ssrf)."""
from __future__ import annotations

import pytest

from prax.utils import ssrf
from prax.utils.ssrf import SSRFError, safe_request, validate_url


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(ssrf.settings, "ssrf_protection_enabled", True)
    monkeypatch.setattr(ssrf.settings, "ssrf_allowed_hosts", "")


# --------------------------------------------------------------------------- #
# Scheme + IP-literal blocking (no DNS needed)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://localhost:8080/",
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://[::1]/",                                # ipv6 loopback
    "http://0.0.0.0/",
    "https://foo.internal/x",
    "https://db.local/",
])
def test_blocks_internal_targets(url):
    with pytest.raises(SSRFError):
        validate_url(url)


@pytest.mark.parametrize("url", [
    "ftp://example.com/x",
    "file:///etc/passwd",
    "gopher://example.com/",
    "data:text/plain;base64,AAAA",
])
def test_blocks_non_http_schemes(url):
    with pytest.raises(SSRFError):
        validate_url(url)


def test_allows_public_host(monkeypatch):
    # Stub resolution to a public IP so the test never needs real DNS.
    monkeypatch.setattr(ssrf.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))])
    assert validate_url("https://example.com/article") == "https://example.com/article"


def test_blocks_name_resolving_to_private(monkeypatch):
    monkeypatch.setattr(ssrf.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 80))])
    with pytest.raises(SSRFError):
        validate_url("https://sneaky.example.com/")


def test_allows_on_resolution_failure(monkeypatch):
    # Non-resolving host is not an internal-resource risk → allowed (the real
    # request will simply fail). Keeps offline/mocked tests working.
    def _boom(*a, **k):
        raise OSError("name resolution failed")
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", _boom)
    assert validate_url("https://does-not-exist.invalid/") == "https://does-not-exist.invalid/"


def test_allowlist_overrides(monkeypatch):
    monkeypatch.setattr(ssrf.settings, "ssrf_allowed_hosts", "localhost,127.0.0.1")
    assert validate_url("http://localhost:6333/") == "http://localhost:6333/"


def test_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(ssrf.settings, "ssrf_protection_enabled", False)
    assert validate_url("http://169.254.169.254/") == "http://169.254.169.254/"


# --------------------------------------------------------------------------- #
# safe_request — per-hop redirect re-validation
# --------------------------------------------------------------------------- #

class _Resp:
    def __init__(self, status_code, location=None):
        self.status_code = status_code
        self.headers = {"Location": location} if location else {}


def test_safe_request_revalidates_redirect(monkeypatch):
    monkeypatch.setattr(ssrf.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))])
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        # First hop 302 → internal metadata endpoint; must be blocked on re-validate.
        return _Resp(302, location="http://169.254.169.254/latest/")

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(SSRFError):
        safe_request("get", "https://example.com/start")
    assert calls == ["https://example.com/start"]  # stopped before fetching the internal hop


def test_safe_request_returns_non_redirect(monkeypatch):
    monkeypatch.setattr(ssrf.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))])
    import requests
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Resp(200))
    resp = safe_request("get", "https://example.com/")
    assert resp.status_code == 200
