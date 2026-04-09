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


# ---------- Screenshot + one-shot page screenshot -------------------------

def test_browser_screenshot_returns_workspace_filename(monkeypatch):
    """browser_screenshot's result must include the workspace filename
    so the agent can pass it straight to workspace_send_file."""
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    monkeypatch.setattr(
        svc, "screenshot",
        lambda uid, full_page=False: {
            "path": "/workspaces/user/active/screenshot-20260409-102030-nytimes.com.png",
            "filename": "screenshot-20260409-102030-nytimes.com.png",
            "url": "https://nytimes.com",
            "workspace": True,
        },
    )
    current_user_id.set("+10000000000")

    result = module.browser_screenshot.invoke({})
    assert "screenshot-20260409-102030-nytimes.com.png" in result
    assert "workspace_send_file" in result
    assert "nytimes.com" in result


def test_browser_screenshot_full_page_param(monkeypatch):
    """full_page is forwarded through to the service."""
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    captured = {}

    def fake_screenshot(uid, full_page=False):
        captured["full_page"] = full_page
        return {
            "path": "/workspaces/u/active/x.png",
            "filename": "x.png",
            "url": "https://example.com",
            "workspace": True,
        }

    monkeypatch.setattr(svc, "screenshot", fake_screenshot)
    current_user_id.set("+10000000000")

    module.browser_screenshot.invoke({"full_page": True})
    assert captured["full_page"] is True


class _FakeTool:
    """Minimal stand-in for a LangChain StructuredTool used in tests."""

    def __init__(self, handler):
        self._handler = handler

    def invoke(self, payload):
        return self._handler(**payload)


def test_browser_page_screenshot_delivers_to_user(monkeypatch):
    """browser_page_screenshot chains navigate → screenshot → deliver."""
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")
    ws_tools = importlib.import_module("prax.agent.workspace_tools")

    nav_calls: list[str] = []
    shot_calls: list[bool] = []
    delivery_calls: list[dict] = []

    monkeypatch.setattr(
        svc, "navigate",
        lambda uid, url: nav_calls.append(url) or {
            "url": url, "title": "The New York Times", "content": "headline 1\nheadline 2",
        },
    )
    monkeypatch.setattr(
        svc, "screenshot",
        lambda uid, full_page=False: shot_calls.append(full_page) or {
            "path": "/workspaces/u/active/screenshot-nyt.png",
            "filename": "screenshot-nyt.png",
            "url": "https://nytimes.com",
            "workspace": True,
        },
    )

    def _send(filename, message=""):
        delivery_calls.append({"filename": filename, "message": message})
        return f"Sent {filename} via Discord."

    monkeypatch.setattr(ws_tools, "workspace_send_file", _FakeTool(_send))
    current_user_id.set("+10000000000")

    result = module.browser_page_screenshot.invoke({"url": "https://nytimes.com"})

    assert nav_calls == ["https://nytimes.com"]
    assert shot_calls == [False]
    assert len(delivery_calls) == 1
    assert delivery_calls[0]["filename"] == "screenshot-nyt.png"
    assert "New York Times" in delivery_calls[0]["message"]
    assert "Sent screenshot-nyt.png" in result


def test_browser_page_screenshot_no_send(monkeypatch):
    """send_to_user=False skips delivery but still captures."""
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")
    ws_tools = importlib.import_module("prax.agent.workspace_tools")

    monkeypatch.setattr(
        svc, "navigate",
        lambda uid, url: {"url": url, "title": "Page", "content": "x"},
    )
    monkeypatch.setattr(
        svc, "screenshot",
        lambda uid, full_page=False: {
            "path": "/workspaces/u/active/p.png",
            "filename": "p.png",
            "url": "https://example.com",
            "workspace": True,
        },
    )

    called = []

    def _send(filename, message=""):
        called.append(filename)
        return ""

    monkeypatch.setattr(ws_tools, "workspace_send_file", _FakeTool(_send))
    current_user_id.set("+10000000000")

    result = module.browser_page_screenshot.invoke({
        "url": "https://example.com",
        "send_to_user": False,
    })
    assert called == []
    assert "workspace_send_file('p.png')" in result


def test_browser_page_screenshot_navigation_failure(monkeypatch):
    """Navigation errors propagate as a clean error message."""
    module = importlib.reload(importlib.import_module("prax.agent.browser_tools"))
    svc = importlib.import_module("prax.services.browser_service")

    monkeypatch.setattr(
        svc, "navigate",
        lambda uid, url: {"error": "DNS resolution failed"},
    )
    current_user_id.set("+10000000000")

    result = module.browser_page_screenshot.invoke({"url": "https://bogus.invalid"})
    assert "Navigation failed" in result
    assert "DNS" in result
