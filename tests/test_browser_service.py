"""Tests for browser_service — Playwright is mocked."""
import importlib
import os

import pytest


class FakePage:
    def __init__(self):
        self.url = "https://example.com"
        self._title = "Example"
        self._text = "Hello World content"
        self._clicks = []
        self._fills = []

    def goto(self, url, **kw):
        self.url = url

    def title(self):
        return self._title

    def inner_text(self, selector):
        return self._text

    def click(self, selector, **kw):
        self._clicks.append(selector)

    def fill(self, selector, value, **kw):
        self._fills.append((selector, value))

    def screenshot(self, path=None, **kw):
        if path:
            with open(path, "wb") as f:
                f.write(b"fake png")

    def wait_for_timeout(self, ms):
        pass

    def wait_for_selector(self, selector, **kw):
        pass

    def set_default_timeout(self, ms):
        pass

    def query_selector_all(self, selector):
        return [FakeElement("DIV", "Item 1"), FakeElement("DIV", "Item 2")]

    @property
    def keyboard(self):
        return FakeKeyboard()


class FakeElement:
    def __init__(self, tag, text):
        self._tag = tag
        self._text = text

    def inner_text(self):
        return self._text

    def evaluate(self, expr):
        return self._tag


class FakeKeyboard:
    def press(self, key):
        pass


class FakeBrowserContext:
    def new_page(self):
        return FakePage()

    def close(self):
        pass


class FakeBrowser:
    def new_context(self, **kw):
        return FakeBrowserContext()

    def close(self):
        pass


class FakePlaywright:
    class chromium:
        @staticmethod
        def launch(**kw):
            return FakeBrowser()

    def stop(self):
        pass


@pytest.fixture()
def browser_mod(monkeypatch, tmp_path):
    """Reload browser_service with mocked Playwright."""
    import prax.settings as settings_mod
    importlib.reload(settings_mod)

    module = importlib.reload(
        importlib.import_module("prax.services.browser_service")
    )
    monkeypatch.setattr(module.settings, "browser_headless", True)
    monkeypatch.setattr(module.settings, "browser_timeout", 5000)
    monkeypatch.setattr(module.settings, "sites_credentials_path", str(tmp_path / "sites.yaml"))
    monkeypatch.setattr(module.settings, "browser_profile_dir", None)
    monkeypatch.setattr(module.settings, "browser_vnc_enabled", False)
    monkeypatch.setattr(module.settings, "browser_vnc_base_port", 5900)

    # Clear sessions.
    module._sessions.clear()
    module._vnc_sessions.clear()

    # Mock the session creation to use our fakes.
    def mock_get_session(user_id):
        if user_id not in module._sessions or module._sessions[user_id].page is None:
            session = module.BrowserSession(user_id)
            session._pw = FakePlaywright()
            session._browser = FakeBrowser()
            session._context = FakeBrowserContext()
            session.page = FakePage()
            module._sessions[user_id] = session
        return module._sessions[user_id]

    monkeypatch.setattr(module, "_get_session", mock_get_session)

    # Write test credentials.
    import yaml
    creds = {
        "sites": {
            "x.com": {"username": "testuser", "password": "testpass", "aliases": ["twitter.com"]},
            "github.com": {"username": "ghuser", "password": "ghpass"},
        }
    }
    with open(tmp_path / "sites.yaml", "w") as f:
        yaml.dump(creds, f)

    return module


# ---------- Navigation ------------------------------------------------------

class TestNavigation:
    def test_navigate(self, browser_mod):
        result = browser_mod.navigate("+10000000000", "https://example.com")
        assert "content" in result
        assert result["title"] == "Example"

    def test_get_content(self, browser_mod):
        browser_mod.navigate("+10000000000", "https://example.com")
        result = browser_mod.get_content("+10000000000")
        assert "content" in result

    def test_screenshot(self, browser_mod):
        browser_mod.navigate("+10000000000", "https://example.com")
        result = browser_mod.screenshot("+10000000000")
        assert "path" in result


# ---------- Interaction ----------------------------------------------------

class TestInteraction:
    def test_click(self, browser_mod):
        browser_mod.navigate("+10000000000", "https://example.com")
        result = browser_mod.click("+10000000000", "button.submit")
        assert result["status"] == "clicked"

    def test_fill(self, browser_mod):
        browser_mod.navigate("+10000000000", "https://example.com")
        result = browser_mod.fill("+10000000000", "input#name", "Alice")
        assert result["status"] == "filled"

    def test_press_key(self, browser_mod):
        browser_mod.navigate("+10000000000", "https://example.com")
        result = browser_mod.press_key("+10000000000", "Enter")
        assert result["status"] == "pressed"

    def test_get_elements(self, browser_mod):
        browser_mod.navigate("+10000000000", "https://example.com")
        result = browser_mod.get_elements("+10000000000", "div.item")
        assert result["count"] == 2


# ---------- Credentials ----------------------------------------------------

class TestCredentials:
    def test_get_direct(self, browser_mod):
        result = browser_mod.get_credentials("x.com")
        assert result["username"] == "testuser"

    def test_get_by_alias(self, browser_mod):
        result = browser_mod.get_credentials("twitter.com")
        assert result["username"] == "testuser"

    def test_not_found(self, browser_mod):
        result = browser_mod.get_credentials("unknown.com")
        assert "error" in result


# ---------- Session management ---------------------------------------------

class TestSession:
    def test_close(self, browser_mod):
        browser_mod.navigate("+10000000000", "https://example.com")
        result = browser_mod.close_session("+10000000000")
        assert result["status"] == "closed"

    def test_close_no_session(self, browser_mod):
        result = browser_mod.close_session("+19999999999")
        assert result["status"] == "no_session"

    def test_close_all(self, browser_mod):
        browser_mod.navigate("+10000000000", "https://example.com")
        browser_mod.navigate("+10000000001", "https://example.com")
        browser_mod.close_all_sessions()
        assert len(browser_mod._sessions) == 0


# ---------- Login detection ------------------------------------------------

class TestLoginDetection:
    def test_detects_login_page(self, browser_mod):
        assert browser_mod._detect_login_wall(
            "Log In - Twitter",
            "Sign in to your account. Enter your password. Forgot password?",
            "https://x.com/login",
        ) is True

    def test_no_false_positive(self, browser_mod):
        assert browser_mod._detect_login_wall(
            "Example Homepage",
            "Welcome to our site. Read the latest news about technology.",
            "https://example.com",
        ) is False

    def test_url_plus_one_signal(self, browser_mod):
        # A login URL + one keyword is enough.
        assert browser_mod._detect_login_wall(
            "Welcome",
            "Please sign in to continue",
            "https://example.com/login",
        ) is True

    def test_navigate_login_hint(self, browser_mod, monkeypatch, tmp_path):
        monkeypatch.setattr(browser_mod.settings, "browser_profile_dir", str(tmp_path))
        monkeypatch.setattr(browser_mod.settings, "browser_vnc_enabled", True)
        session = browser_mod._get_session("+10000000000")
        session.page._title = "Log In"
        session.page._text = "Sign in to your account. Enter your password. Forgot password?"
        session.page.url = "https://x.com/login"
        result = browser_mod.navigate("+10000000000", "https://x.com/login")
        assert result.get("login_required") is True
        assert "login_hint" in result
        assert "browser_request_login" in result["login_hint"]

    def test_navigate_no_hint_normal_page(self, browser_mod):
        result = browser_mod.navigate("+10000000000", "https://example.com")
        assert "login_hint" not in result


# ---------- Persistent profiles --------------------------------------------

class TestProfiles:
    def test_get_profile_dir_none_when_disabled(self, browser_mod):
        assert browser_mod._get_profile_dir("+10000000000") is None

    def test_get_profile_dir_creates(self, browser_mod, monkeypatch, tmp_path):
        monkeypatch.setattr(browser_mod.settings, "browser_profile_dir", str(tmp_path))
        result = browser_mod._get_profile_dir("+15551234567")
        assert result == str(tmp_path / "15551234567")
        assert os.path.isdir(result)

    def test_get_profile_dir_strips_plus(self, browser_mod, monkeypatch, tmp_path):
        monkeypatch.setattr(browser_mod.settings, "browser_profile_dir", str(tmp_path))
        result = browser_mod._get_profile_dir("+1 555 123")
        assert "+" not in os.path.basename(result)
        assert " " not in os.path.basename(result)

    def test_list_profiles_empty(self, browser_mod):
        result = browser_mod.list_profiles()
        assert result["profiles"] == []

    def test_list_profiles_with_entries(self, browser_mod, monkeypatch, tmp_path):
        monkeypatch.setattr(browser_mod.settings, "browser_profile_dir", str(tmp_path))
        profile = tmp_path / "15551234567"
        profile.mkdir()
        (profile / "Cookies").write_bytes(b"data")
        result = browser_mod.list_profiles()
        assert result["count"] == 1
        assert result["profiles"][0]["user_id"] == "+15551234567"
        assert result["profiles"][0]["size_mb"] >= 0


# ---------- VNC interactive login ------------------------------------------

class TestVncLogin:
    def test_start_fails_no_profile_dir(self, browser_mod):
        result = browser_mod.start_interactive_login("+10000000000", "https://x.com")
        assert "error" in result
        assert "BROWSER_PROFILE_DIR" in result["error"]

    def test_start_fails_vnc_disabled(self, browser_mod, monkeypatch, tmp_path):
        monkeypatch.setattr(browser_mod.settings, "browser_profile_dir", str(tmp_path))
        result = browser_mod.start_interactive_login("+10000000000", "https://x.com")
        assert "error" in result
        assert "VNC" in result["error"]

    def test_start_and_finish(self, browser_mod, monkeypatch, tmp_path):
        monkeypatch.setattr(browser_mod.settings, "browser_profile_dir", str(tmp_path))
        monkeypatch.setattr(browser_mod.settings, "browser_vnc_enabled", True)

        # Mock VNC server and BrowserSession.start.
        fake_xvfb = type("Proc", (), {"terminate": lambda s: None, "wait": lambda s, **kw: None, "kill": lambda s: None, "poll": lambda s: 0})()
        fake_vnc = type("Proc", (), {"terminate": lambda s: None, "wait": lambda s, **kw: None, "kill": lambda s: None, "poll": lambda s: 0})()
        monkeypatch.setattr(
            browser_mod, "_start_vnc_server",
            lambda port=None: (":99", 5901, fake_xvfb, fake_vnc),
        )

        def fake_start(self, headless=None, vnc_display=None):
            self._pw = FakePlaywright()
            self._browser = FakeBrowser()
            self._context = FakeBrowserContext()
            self.page = FakePage()
            self._persistent = True
        monkeypatch.setattr(browser_mod.BrowserSession, "start", fake_start)

        # Start interactive login.
        result = browser_mod.start_interactive_login("+10000000000", "https://x.com/login")
        assert result["status"] == "started"
        assert result["vnc_port"] == 5901
        assert "ssh" in result["instructions"].lower()
        assert "+10000000000" in browser_mod._vnc_sessions

        # Already active.
        result2 = browser_mod.start_interactive_login("+10000000000", "https://x.com")
        assert result2["status"] == "already_active"

        # Finish.
        result3 = browser_mod.finish_interactive_login("+10000000000")
        assert result3["status"] == "login_saved"
        assert "+10000000000" not in browser_mod._vnc_sessions
        assert "+10000000000" not in browser_mod._sessions

    def test_finish_no_session(self, browser_mod):
        result = browser_mod.finish_interactive_login("+19999999999")
        assert "error" in result

    def test_check_login_status(self, browser_mod):
        result = browser_mod.check_login_status("+10000000000", "example.com")
        assert "appears_logged_in" in result

    def test_close_cleans_vnc(self, browser_mod):
        fake_proc = type("Proc", (), {"terminate": lambda s: None, "wait": lambda s, **kw: None, "kill": lambda s: None, "poll": lambda s: 0})()
        browser_mod._vnc_sessions["+10000000000"] = {
            "display": ":99", "port": 5901,
            "xvfb": fake_proc, "vnc": fake_proc,
        }
        browser_mod.navigate("+10000000000", "https://example.com")
        result = browser_mod.close_session("+10000000000")
        assert result["status"] == "closed"
        assert "+10000000000" not in browser_mod._vnc_sessions

    def test_close_all_cleans_vnc(self, browser_mod):
        fake_proc = type("Proc", (), {"terminate": lambda s: None, "wait": lambda s, **kw: None, "kill": lambda s: None, "poll": lambda s: 0})()
        browser_mod._vnc_sessions["+10000000000"] = {
            "display": ":99", "port": 5901,
            "xvfb": fake_proc, "vnc": fake_proc,
        }
        browser_mod.navigate("+10000000000", "https://example.com")
        browser_mod.close_all_sessions()
        assert len(browser_mod._sessions) == 0
        assert len(browser_mod._vnc_sessions) == 0
