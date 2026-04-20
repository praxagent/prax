"""Tests for browser_verify — declarative E2E verification flow.

CDP is fully mocked. The goal is to prove that:
- Each verb routes to the right CDP primitive.
- A failing step short-circuits the rest of the flow.
- Unavailable browser returns a graceful error.
- The output is structured enough for an agent to reason about.
"""
from __future__ import annotations

import importlib


def _load(monkeypatch, *, available=True, cdp_overrides=None):
    """Fresh cdp_tools module with a mocked cdp_service."""
    cdp_service = importlib.import_module("prax.services.cdp_service")
    monkeypatch.setattr(cdp_service, "is_available", lambda: available)
    for attr, fn in (cdp_overrides or {}).items():
        monkeypatch.setattr(cdp_service, attr, fn)
    module = importlib.reload(importlib.import_module("prax.agent.cdp_tools"))
    return module


class TestBrowserVerifyAvailability:
    def test_unavailable_returns_graceful_error(self, monkeypatch):
        module = _load(monkeypatch, available=False)
        result = module.browser_verify.invoke({"flow": [{"goto": "http://x"}]})
        assert "unavailable" in result.lower()

    def test_empty_flow_rejected(self, monkeypatch):
        module = _load(monkeypatch)
        result = module.browser_verify.invoke({"flow": []})
        assert "non-empty" in result.lower()

    def test_flow_too_long_rejected(self, monkeypatch):
        module = _load(monkeypatch)
        flow = [{"goto": "http://x"}] * 100
        result = module.browser_verify.invoke({"flow": flow})
        assert "max" in result.lower() or "split" in result.lower()


class TestBrowserVerifyFlow:
    def test_happy_path_all_steps_pass(self, monkeypatch):
        calls = []

        def navigate(url):
            calls.append(("navigate", url))
            return {"url": url, "title": "Home", "text": ""}

        def click_element(sel):
            calls.append(("click_element", sel))
            return {"status": "clicked"}

        def type_text(text):
            calls.append(("type_text", text))
            return {"status": "typed"}

        def evaluate_js(expr):
            calls.append(("evaluate_js", expr))
            # Both the focus() call and the assert_visible check return truthy.
            return {"value": True}

        def get_page_text(max_length=30_000):
            return {"title": "Home", "url": "http://x", "text": "Welcome back"}

        module = _load(monkeypatch, cdp_overrides={
            "navigate": navigate,
            "click_element": click_element,
            "type_text": type_text,
            "evaluate_js": evaluate_js,
            "get_page_text": get_page_text,
        })
        result = module.browser_verify.invoke({"flow": [
            {"goto": "http://x/login"},
            {"type": {"selector": "input[name='email']", "text": "a@b.com"}},
            {"click": "button.submit"},
            {"assert_visible": "div.success"},
            {"assert_text": "Welcome back"},
        ]})
        assert "5/5 steps passed" in result
        assert "✓" in result
        # Each primitive was exercised.
        verbs = [c[0] for c in calls]
        assert "navigate" in verbs
        assert "click_element" in verbs
        assert "type_text" in verbs

    def test_failure_short_circuits_and_snapshots_page(self, monkeypatch):
        module = _load(monkeypatch, cdp_overrides={
            "navigate": lambda url: {"url": url, "title": "Login", "text": ""},
            "evaluate_js": lambda expr: {"value": False},  # assert fails
            "click_element": lambda sel: {"status": "clicked"},
            "click_text": lambda t: {"status": "clicked"},
            "get_page_text": lambda max_length=30_000: {
                "title": "Login", "url": "http://x/login", "text": "Invalid password",
            },
        })
        result = module.browser_verify.invoke({"flow": [
            {"goto": "http://x/login"},
            {"click": "Sign in"},
            {"assert_visible": "nav.logged-in"},
            {"assert_text": "Welcome back"},  # never reached
        ]})
        # Third step fails, fourth is never executed.
        assert "3/4 steps passed" not in result  # no, wait — it IS 2/3
        # Correction: steps[0]=goto ok, steps[1]=click ok, steps[2]=assert_visible fail.
        # Short-circuit, so only 3 results.
        assert "2/3 steps passed" in result
        assert "nav.logged-in" in result
        # Page snapshot on failure:
        assert "Invalid password" in result

    def test_unknown_verb_flagged(self, monkeypatch):
        module = _load(monkeypatch)
        result = module.browser_verify.invoke({"flow": [{"jump": "nowhere"}]})
        assert "unknown verb" in result or "unknown" in result.lower()

    def test_wait_for_times_out_cleanly(self, monkeypatch):
        module = _load(monkeypatch, cdp_overrides={
            "evaluate_js": lambda expr: {"value": False},
        })
        # Shrink the timeout so the test doesn't hang for 5s.
        module._WAIT_TIMEOUT_MS = 100
        module._WAIT_POLL_MS = 20
        result = module.browser_verify.invoke({"flow": [
            {"wait_for": "div.never-appears"},
        ]})
        assert "0/1 steps passed" in result
        assert "div.never-appears" in result

    def test_screenshot_reports_path(self, monkeypatch):
        module = _load(monkeypatch, cdp_overrides={
            "screenshot": lambda: {"path": "/tmp/verify.png", "format": "png"},
        })
        result = module.browser_verify.invoke({"flow": [
            {"screenshot": "login-success"},
        ]})
        assert "/tmp/verify.png" in result
        assert "1/1 steps passed" in result
