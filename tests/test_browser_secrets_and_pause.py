"""Stored passwords stay out of the model's context; the agent yields the browser to a person.

BROWSER_SECRETS_OUT_OF_CONTEXT: browser_login used to return the password from
sites.yaml into the model's context (and the trace). browser_fill_login types it
into the page instead and says only which fields it filled.

BROWSER_PAUSE_FOR_USER: while a person drives the browser (a VNC login, or
TeamWork's Take control / recent input), every browser action stands down.
"""
from __future__ import annotations

import pytest

import prax.settings as prax_settings
from prax.agent import browser_tools, cdp_tools
from prax.services import browser_service

SECRET = "hunter2-very-secret"


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setattr(browser_service, "get_credentials",
                        lambda d: {"domain": d, "username": "tj", "password": SECRET})
    filled = []
    monkeypatch.setattr(browser_service, "fill",
                        lambda uid, sel, val: filled.append((sel, val)) or {"ok": True})
    return filled


@pytest.fixture
def secrets_hidden(monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "browser_secrets_out_of_context", True)


def _names(tools):
    return {t.name for t in tools}


def test_flag_off_keeps_browser_login(creds):
    assert "browser_login" in _names(browser_tools.build_browser_tools())
    assert SECRET in browser_tools.browser_login.invoke({"domain": "x.com"})


def test_password_never_reaches_the_model(creds, secrets_hidden):
    names = _names(browser_tools.build_browser_tools())
    assert "browser_login" not in names and "browser_fill_login" in names

    out = browser_tools.browser_fill_login.func(
        domain="x.com", username_selector="#user", password_selector="#pass")
    assert SECRET not in out and "password" in out
    assert creds == [("#user", "tj"), ("#pass", SECRET)]  # it did reach the page


def test_browser_login_refuses_when_hidden(creds, secrets_hidden):
    out = browser_tools.browser_login.invoke({"domain": "x.com"})
    assert SECRET not in out and "browser_fill_login" in out


def test_credentials_listing_never_shows_the_password(creds):
    assert SECRET not in browser_tools.browser_credentials.invoke({"domain": "x.com"})


# --- the pause ---------------------------------------------------------------

class _TW:
    enabled = True

    def __init__(self, in_control):
        self.in_control = in_control

    def browser_control(self):
        return {"user_in_control": self.in_control}


@pytest.fixture
def pause_on(monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "browser_pause_for_user", True)


def _set_teamwork(monkeypatch, tw):
    monkeypatch.setattr("prax.services.teamwork_service.get_teamwork_client", lambda: tw)


ACTIONS = [
    lambda: browser_tools.browser_navigate.invoke({"url": "https://example.com"}),
    lambda: browser_tools.browser_click.func(selector="#go"),
    lambda: browser_tools.browser_fill.func(selector="#q", text="x"),
    lambda: browser_tools.browser_press.invoke({"key": "Enter"}),
    lambda: browser_tools.browser_fill_login.func(domain="x.com", username_selector="#u", password_selector="#p"),
    lambda: cdp_tools.sandbox_browser_act.func(action="navigate", value="https://example.com"),
    lambda: cdp_tools.browser_verify.invoke({"flow": [{"goto": "https://example.com"}]}),
]


@pytest.mark.parametrize("act", ACTIONS)
def test_every_browser_action_stands_down_while_the_user_drives(act, pause_on, monkeypatch, creds):
    _set_teamwork(monkeypatch, _TW(in_control=True))
    touched = []
    monkeypatch.setattr(browser_service, "navigate", lambda *a, **k: touched.append("nav") or {})
    out = act()
    assert out.startswith("Paused"), out
    assert touched == [] and creds == []


def test_vnc_login_in_progress_pauses(pause_on, monkeypatch):
    _set_teamwork(monkeypatch, _TW(in_control=False))
    monkeypatch.setattr(browser_tools, "_get_user_id", lambda: "u1")
    monkeypatch.setattr(browser_service, "_vnc_sessions", {"u1": {}})
    assert browser_tools.user_has_the_browser().startswith("Paused")


def test_no_pause_when_the_agent_has_the_browser(pause_on, monkeypatch):
    _set_teamwork(monkeypatch, _TW(in_control=False))
    monkeypatch.setattr(browser_service, "_vnc_sessions", {})
    assert browser_tools.user_has_the_browser() == ""


def test_teamwork_down_does_not_take_the_browser_down(pause_on, monkeypatch):
    class Down(_TW):
        def browser_control(self):
            raise ConnectionError("down")
    _set_teamwork(monkeypatch, Down(False))
    monkeypatch.setattr(browser_service, "_vnc_sessions", {})
    assert browser_tools.user_has_the_browser() == ""


def test_pause_is_off_by_default(monkeypatch):
    _set_teamwork(monkeypatch, _TW(in_control=True))
    assert browser_tools.user_has_the_browser() == ""
