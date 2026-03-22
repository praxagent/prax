"""Tests for browser_tools LangChain wrappers."""
import importlib

from prax.agent.user_context import current_user_id


def test_browser_open(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    monkeypatch.setattr(
        svc, "navigate",
        lambda uid, url: {"url": url, "title": "Test Page", "content": "Page content here"},
    )
    current_user_id.set("+10000000000")

    result = module.browser_open.invoke({"url": "https://example.com"})
    assert "Test Page" in result
    assert "Page content" in result


def test_browser_open_login_hint(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    monkeypatch.setattr(
        svc, "navigate",
        lambda uid, url: {
            "url": url, "title": "Log In", "content": "Please sign in",
            "login_hint": "Use browser_request_login for manual login",
        },
    )
    current_user_id.set("+10000000000")

    result = module.browser_open.invoke({"url": "https://x.com"})
    assert "browser_request_login" in result


def test_browser_click(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    monkeypatch.setattr(
        svc, "click",
        lambda uid, sel: {"status": "clicked", "selector": sel, "url": "https://example.com"},
    )
    current_user_id.set("+10000000000")

    result = module.browser_click.invoke({"selector": "button.submit"})
    assert "clicked" in result.lower()


def test_browser_fill(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    monkeypatch.setattr(
        svc, "fill",
        lambda uid, sel, val: {"status": "filled", "selector": sel},
    )
    current_user_id.set("+10000000000")

    result = module.browser_fill.invoke({"selector": "input#user", "text": "alice"})
    assert "filled" in result.lower()


def test_browser_credentials(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    monkeypatch.setattr(
        svc, "get_credentials",
        lambda domain: {"domain": "x.com", "username": "alice", "password": "secret"},
    )
    current_user_id.set("+10000000000")

    result = module.browser_credentials.invoke({"domain": "x.com"})
    assert "alice" in result
    assert "secret" not in result  # password should not be exposed


def test_browser_login(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    monkeypatch.setattr(
        svc, "get_credentials",
        lambda domain: {"domain": "x.com", "username": "alice", "password": "secret"},
    )
    current_user_id.set("+10000000000")

    result = module.browser_login.invoke({"domain": "x.com"})
    assert "alice" in result
    assert "secret" in result  # login tool exposes password for filling


def test_browser_close(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    monkeypatch.setattr(svc, "close_session", lambda uid: {"status": "closed"})
    current_user_id.set("+10000000000")

    result = module.browser_close.invoke({})
    assert "closed" in result.lower()


# ---------- VNC / profile tools -------------------------------------------

def test_browser_request_login(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    monkeypatch.setattr(
        svc, "start_interactive_login",
        lambda uid, url=None: {
            "status": "started", "vnc_port": 5901,
            "instructions": "VNC server running on port 5901. SSH tunnel with: ssh -NL 5901:localhost:5901",
        },
    )
    current_user_id.set("+10000000000")

    result = module.browser_request_login.invoke({"url": "https://x.com"})
    assert "5901" in result
    assert "ssh" in result.lower()


def test_browser_request_login_error(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    monkeypatch.setattr(
        svc, "start_interactive_login",
        lambda uid, url=None: {"error": "VNC disabled"},
    )
    current_user_id.set("+10000000000")

    result = module.browser_request_login.invoke({"url": "https://x.com"})
    assert "error" in result.lower()


def test_browser_finish_login(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    monkeypatch.setattr(
        svc, "finish_interactive_login",
        lambda uid: {"status": "login_saved", "message": "Login saved. I'll use this session for future visits."},
    )
    current_user_id.set("+10000000000")

    result = module.browser_finish_login.invoke({})
    assert "saved" in result.lower()


def test_browser_check_login(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    monkeypatch.setattr(
        svc, "check_login_status",
        lambda uid, domain: {"domain": "x.com", "appears_logged_in": True, "url": "https://x.com", "title": "Home / X"},
    )
    current_user_id.set("+10000000000")

    result = module.browser_check_login.invoke({"domain": "x.com"})
    assert "logged in" in result.lower()
    assert "x.com" in result


def test_browser_check_login_not_logged_in(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    monkeypatch.setattr(
        svc, "check_login_status",
        lambda uid, domain: {"domain": "x.com", "appears_logged_in": False, "url": "https://x.com/login", "title": "Log in to X"},
    )
    current_user_id.set("+10000000000")

    result = module.browser_check_login.invoke({"domain": "x.com"})
    assert "NOT logged in" in result


def test_browser_profiles(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    monkeypatch.setattr(
        svc, "list_profiles",
        lambda: {"count": 1, "profiles": [{"user_id": "+15551234567", "size_mb": 2.3, "path": "/tmp/profiles/15551234567"}]},
    )
    current_user_id.set("+10000000000")

    result = module.browser_profiles.invoke({})
    assert "15551234567" in result
    assert "2.3" in result


def test_browser_profiles_empty(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    monkeypatch.setattr(
        svc, "list_profiles",
        lambda: {"profiles": [], "note": "No profile directory configured"},
    )
    current_user_id.set("+10000000000")

    result = module.browser_profiles.invoke({})
    assert "no" in result.lower()
