"""Browser navigation URL gate — one helper, every call site.

Unconditional: only http(s) may be opened (``file://`` renders the host
filesystem into a browser the agent then reads; ``javascript:``/``data:``/
``about:`` are not pages). Flag-gated (``BROWSER_NAVIGATE_SSRF_GUARD``,
default off): the SSRF guard additionally refuses internal hosts. Covers
``browser_service.navigate`` (→ browser_navigate / browser_page_screenshot),
``sandbox_browser_act("navigate")`` and ``browser_verify`` goto.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from prax.services import browser_service as svc
from prax.utils import ssrf


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setattr(svc.settings, "browser_navigate_ssrf_guard", False)
    monkeypatch.setattr(ssrf.settings, "ssrf_protection_enabled", True)
    monkeypatch.setattr(ssrf.settings, "ssrf_allowed_hosts", "")


class _Page:
    def __init__(self):
        self.gotos: list[str] = []
        self.url = ""

    def goto(self, url, **kw):
        self.gotos.append(url)
        self.url = url

    def title(self):
        return "Title"

    def inner_text(self, selector):
        return "body text"

    def wait_for_timeout(self, ms):
        pass


@pytest.fixture
def page(monkeypatch):
    p = _Page()
    monkeypatch.setattr(svc, "_get_session", lambda uid: SimpleNamespace(page=p))
    return p


# --------------------------------------------------------------------------- #
# The helper
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "FILE:///etc/passwd", " file:///etc/passwd",
    "javascript:alert(1)", "data:text/html,<h1>x</h1>", "about:blank",
    "ftp://example.com/x", "chrome://settings", "example.com", "", "http:///no-host",
])
@pytest.mark.parametrize("flag", [False, True])
def test_non_http_refused_regardless_of_flag(monkeypatch, url, flag):
    monkeypatch.setattr(svc.settings, "browser_navigate_ssrf_guard", flag)
    msg = svc.check_navigation_url(url)
    assert msg and msg.startswith("Refused to navigate"), (url, msg)


def test_http_allowed():
    assert svc.check_navigation_url("https://example.com/path?q=1") is None
    assert svc.check_navigation_url("HTTP://Example.com") is None


def test_internal_host_allowed_when_flag_off():
    assert svc.check_navigation_url("http://127.0.0.1:8000") is None
    assert svc.check_navigation_url("http://localhost:3000") is None


def test_internal_host_refused_when_flag_on(monkeypatch):
    monkeypatch.setattr(svc.settings, "browser_navigate_ssrf_guard", True)
    for url in ("http://127.0.0.1:8000", "http://169.254.169.254/latest/meta-data/",
                "http://localhost:3000"):
        msg = svc.check_navigation_url(url)
        assert msg and "Refused to navigate" in msg, url


# --------------------------------------------------------------------------- #
# browser_service.navigate + browser_tools
# --------------------------------------------------------------------------- #

def test_navigate_refuses_file_url_before_goto(page):
    result = svc.navigate("u", "file:///etc/passwd")
    assert "error" in result and "Refused" in result["error"]
    assert page.gotos == []


def test_navigate_loopback_flag_off_then_on(page, monkeypatch):
    result = svc.navigate("u", "http://127.0.0.1:8000")
    assert result.get("title") == "Title" and page.gotos == ["http://127.0.0.1:8000"]
    monkeypatch.setattr(svc.settings, "browser_navigate_ssrf_guard", True)
    result = svc.navigate("u", "http://127.0.0.1:8000")
    assert "error" in result and "Refused" in result["error"]
    assert page.gotos == ["http://127.0.0.1:8000"]  # no second goto


def test_browser_navigate_tool_surfaces_refusal(page):
    from prax.agent import browser_tools
    out = browser_tools.browser_navigate.invoke({"url": "file:///etc/passwd"})
    assert out.startswith("Browser error: Refused")
    assert page.gotos == []


def test_login_helpers_refuse_bad_urls(page, monkeypatch):
    monkeypatch.setattr(svc.settings, "browser_sandbox_only", False)
    assert "error" in svc.start_interactive_login("u", "file:///etc/passwd")
    monkeypatch.setattr(svc.settings, "browser_navigate_ssrf_guard", True)
    assert "error" in svc.check_login_status("u", "127.0.0.1")
    assert page.gotos == []


# --------------------------------------------------------------------------- #
# cdp_tools — sandbox_browser_act "navigate" and browser_verify goto
# --------------------------------------------------------------------------- #

@pytest.fixture
def cdp(monkeypatch):
    cdp_service = importlib.import_module("prax_sandbox.cdp_service")
    calls: list[str] = []

    def navigate(url):
        calls.append(url)
        return {"url": url, "title": "Sandbox", "text": "hi"}

    monkeypatch.setattr(cdp_service, "is_available", lambda: True)
    monkeypatch.setattr(cdp_service, "navigate", navigate)
    monkeypatch.setattr(cdp_service, "get_page_text", lambda **kw: {"error": "n/a"})
    module = importlib.import_module("prax.agent.cdp_tools")
    return module, calls


def test_sandbox_browser_act_refuses_file_url(cdp):
    module, calls = cdp
    out = module.sandbox_browser_act.invoke({"action": "navigate", "value": "file:///etc/passwd"})
    assert out.startswith("Browser error: Refused")
    assert calls == []


def test_sandbox_browser_act_loopback_flag_off_then_on(cdp, monkeypatch):
    module, calls = cdp
    out = module.sandbox_browser_act.invoke({"action": "navigate", "value": "http://127.0.0.1:8000"})
    assert "Sandbox" in out and calls == ["http://127.0.0.1:8000"]
    monkeypatch.setattr(svc.settings, "browser_navigate_ssrf_guard", True)
    out = module.sandbox_browser_act.invoke({"action": "navigate", "value": "http://127.0.0.1:8000"})
    assert out.startswith("Browser error: Refused")
    assert calls == ["http://127.0.0.1:8000"]


def test_browser_verify_goto_refuses_file_url(cdp):
    module, calls = cdp
    out = module.browser_verify.invoke({
        "flow": [{"goto": "file:///etc/passwd"}, {"assert_text": "root:"}],
    })
    assert "0/1 steps passed" in out and "Refused" in out
    assert calls == []


def test_browser_verify_goto_http_still_navigates(cdp):
    module, calls = cdp
    out = module.browser_verify.invoke({"flow": [{"goto": "http://x/login"}]})
    assert "1/1 steps passed" in out
    assert calls == ["http://x/login"]
