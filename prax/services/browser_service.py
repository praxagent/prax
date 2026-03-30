"""Browser automation service — Playwright-backed web navigation.

Gives the agent the ability to navigate websites like a human: click links,
fill forms, log in with stored credentials, and extract content from
JavaScript-heavy sites (Twitter/X, SPAs, etc.).

Supports **persistent browser profiles** (cookies/localStorage survive restarts)
and **VNC-based interactive login** so users can log in manually via SSH tunnel
instead of storing passwords in YAML.
"""
from __future__ import annotations

import logging
import os
import random
import subprocess
import tempfile
import threading
import time
from typing import Any

import yaml

from prax.settings import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_sessions: dict[str, BrowserSession] = {}
_vnc_sessions: dict[str, dict] = {}  # user_id -> {display, port, xvfb, vnc}
_vnc_port_offset = 0

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_LOGIN_SIGNALS = [
    "log in", "sign in", "sign up", "create account",
    "enter your password", "forgot password",
    "authentication required", "single sign-on",
]

# Human-like timing ranges (seconds).  Each action picks a random delay
# from the corresponding (min, max) range before executing.
_TIMING = {
    "before_click":   (0.4, 1.5),
    "after_click":    (0.8, 2.0),
    "before_fill":    (0.3, 1.0),
    "keystroke":      (0.03, 0.12),   # per-character typing delay
    "after_fill":     (0.3, 0.8),
    "before_key":     (0.2, 0.6),
    "after_key":      (0.4, 1.2),
    "before_select":  (0.3, 0.8),
    "after_select":   (0.3, 0.8),
    "after_navigate": (1.5, 3.0),
}


def _human_delay(page: Any, action: str) -> None:
    """Pause for a random human-like interval before/after an action."""
    lo, hi = _TIMING.get(action, (0.3, 1.0))
    ms = int(random.uniform(lo, hi) * 1000)
    page.wait_for_timeout(ms)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_profile_dir(user_id: str) -> str | None:
    """Return the persistent profile directory for a user, or None.

    If BROWSER_PROFILE_DIR is set, profiles live there (legacy behaviour).
    Otherwise, each user's profile is stored inside their workspace at
    ``{workspace_dir}/{user_id}/.browser_profile/`` — one fewer volume to
    manage and the profile travels with the workspace.
    """
    base = settings.browser_profile_dir
    if base:
        safe_id = user_id.replace("+", "").replace(" ", "_")
        path = os.path.join(base, safe_id)
    else:
        # Store inside the user's workspace directory.
        safe_id = user_id.lstrip("+")
        ws_root = os.path.join(settings.workspace_dir, safe_id)
        if not os.path.isdir(ws_root):
            return None  # workspace not initialised yet
        path = os.path.join(ws_root, ".browser_profile")
    os.makedirs(path, exist_ok=True)
    return path


def _detect_login_wall(title: str, text: str, url: str) -> bool:
    """Heuristic: does the page look like a login wall?"""
    combined = (title + " " + text[:3000]).lower()
    hits = sum(1 for kw in _LOGIN_SIGNALS if kw in combined)
    url_hit = any(p in url.lower() for p in ["/login", "/signin", "/auth", "/account/begin"])
    return hits >= 2 or (hits >= 1 and url_hit)


# ---------------------------------------------------------------------------
# Browser session
# ---------------------------------------------------------------------------

class BrowserSession:
    """Wraps a Playwright browser context for a single user."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None
        self._persistent = False
        self._cdp = False  # True when connected to external Chrome via CDP

    def _start_cdp(self) -> bool:
        """Try to connect to the sandbox Chrome via CDP.

        Returns True on success. On failure, logs and returns False so the
        caller can fall back to launching a standalone browser.
        """
        cdp_url = settings.browser_cdp_url
        if not cdp_url:
            return False

        try:
            self._browser = self._pw.chromium.connect_over_cdp(cdp_url)
            self._cdp = True

            # Use the browser's default context (the sandbox Chrome's context).
            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
                self.page = (
                    self._context.pages[0]
                    if self._context.pages
                    else self._context.new_page()
                )
            else:
                self._context = self._browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent=_USER_AGENT,
                )
                self.page = self._context.new_page()

            logger.info("Playwright connected to sandbox Chrome via CDP: %s", cdp_url)
            return True
        except Exception as exc:
            logger.warning("CDP connection to %s failed, falling back to standalone: %s", cdp_url, exc)
            # Clean up partial state
            try:
                if self._browser:
                    self._browser.close()
            except Exception:
                pass
            self._browser = None
            self._context = None
            self.page = None
            self._cdp = False
            return False

    def start(self, headless: bool | None = None, vnc_display: str | None = None) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()

        # If a CDP endpoint is configured and we're not doing VNC, try CDP first.
        if not vnc_display and self._start_cdp():
            self.page.set_default_timeout(settings.browser_timeout)
            return

        # Standalone browser (local dev or VNC login flow).
        h = headless if headless is not None else settings.browser_headless
        profile = _get_profile_dir(self.user_id)

        launch_kw: dict[str, Any] = {}
        if vnc_display:
            launch_kw["args"] = ["--no-sandbox", "--disable-gpu"]
            launch_kw["env"] = {**os.environ, "DISPLAY": vnc_display}

        if profile:
            self._persistent = True
            self._context = self._pw.chromium.launch_persistent_context(
                profile,
                headless=h,
                viewport={"width": 1280, "height": 720},
                user_agent=_USER_AGENT,
                **launch_kw,
            )
            self.page = (
                self._context.pages[0]
                if self._context.pages
                else self._context.new_page()
            )
        else:
            self._browser = self._pw.chromium.launch(headless=h, **launch_kw)
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=_USER_AGENT,
            )
            self.page = self._context.new_page()

        self.page.set_default_timeout(settings.browser_timeout)

    def close(self) -> None:
        try:
            if self._cdp:
                # Don't close the shared Chrome — just disconnect Playwright.
                if self._browser:
                    self._browser.close()
            elif self._persistent and self._context:
                self._context.close()
            elif self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None
        self._persistent = False
        self._cdp = False


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def _get_session(user_id: str) -> BrowserSession:
    with _lock:
        if user_id not in _sessions or _sessions[user_id].page is None:
            session = BrowserSession(user_id)
            session.start()
            _sessions[user_id] = session
        return _sessions[user_id]


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def _load_credentials() -> dict[str, dict]:
    """Load site credentials from the YAML file."""
    path = settings.sites_credentials_path
    if not path or not os.path.exists(path):
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("sites", {})


def get_credentials(domain: str) -> dict[str, Any]:
    """Get stored credentials for a domain."""
    creds = _load_credentials()
    # Direct match.
    if domain in creds:
        return {"domain": domain, **creds[domain]}
    # Check aliases.
    for site_domain, site_creds in creds.items():
        aliases = site_creds.get("aliases", [])
        if domain in aliases or site_domain in domain or any(a in domain for a in aliases):
            return {"domain": site_domain, **site_creds}
    return {"error": f"No credentials stored for {domain}"}


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def navigate(user_id: str, url: str) -> dict[str, Any]:
    """Navigate to a URL and return page title + text content."""
    try:
        session = _get_session(user_id)
        session.page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_timeout)
        # Human-like pause while "reading" the page after it loads.
        _human_delay(session.page, "after_navigate")
        title = session.page.title()
        # Extract text content, truncated for sanity.
        text = session.page.inner_text("body")
        if len(text) > 15000:
            text = text[:15000] + "\n\n[Content truncated — use browser_read_page for full text]"

        result: dict[str, Any] = {"url": url, "title": title, "content": text}

        # Login wall detection.
        if _detect_login_wall(title, text, session.page.url):
            result["login_required"] = True
            hints = ["This page appears to require login. Options:"]
            hints.append("- browser_login: auto-fill credentials from sites.yaml")
            if settings.browser_vnc_enabled and settings.browser_profile_dir:
                hints.append("- browser_request_login: open VNC for the user to log in manually")
            elif settings.browser_profile_dir:
                hints.append("  (Enable BROWSER_VNC_ENABLED for manual VNC login)")
            result["login_hint"] = "\n".join(hints)

        return result
    except Exception as e:
        return {"error": f"Navigation failed: {e}"}


def get_content(user_id: str) -> dict[str, Any]:
    """Get the current page's text content."""
    try:
        session = _get_session(user_id)
        if not session.page:
            return {"error": "No active browser session"}
        title = session.page.title()
        url = session.page.url
        text = session.page.inner_text("body")
        if len(text) > 30000:
            text = text[:30000] + "\n\n[Content truncated]"
        return {"url": url, "title": title, "content": text}
    except Exception as e:
        return {"error": f"Failed to get content: {e}"}


def screenshot(user_id: str) -> dict[str, Any]:
    """Take a screenshot of the current page.  Returns the file path."""
    try:
        session = _get_session(user_id)
        if not session.page:
            return {"error": "No active browser session"}
        fd, path = tempfile.mkstemp(suffix=".png", prefix="browser_")
        os.close(fd)
        session.page.screenshot(path=path, full_page=False)
        return {"path": path, "url": session.page.url}
    except Exception as e:
        return {"error": f"Screenshot failed: {e}"}


# ---------------------------------------------------------------------------
# Interaction
# ---------------------------------------------------------------------------

def click(user_id: str, selector: str) -> dict[str, Any]:
    """Click an element on the current page."""
    try:
        session = _get_session(user_id)
        _human_delay(session.page, "before_click")
        session.page.click(selector, timeout=10000)
        _human_delay(session.page, "after_click")
        return {"status": "clicked", "selector": selector, "url": session.page.url}
    except Exception as e:
        return {"error": f"Click failed on '{selector}': {e}"}


def fill(user_id: str, selector: str, value: str) -> dict[str, Any]:
    """Fill a form field with human-like typing."""
    try:
        session = _get_session(user_id)
        _human_delay(session.page, "before_fill")
        # Clear field first, then type character-by-character.
        session.page.click(selector, timeout=10000)
        session.page.fill(selector, "")
        lo, hi = _TIMING["keystroke"]
        avg_delay = (lo + hi) / 2 * 1000  # ms
        session.page.type(selector, value, delay=avg_delay)
        _human_delay(session.page, "after_fill")
        return {"status": "filled", "selector": selector}
    except Exception as e:
        return {"error": f"Fill failed on '{selector}': {e}"}


def press_key(user_id: str, key: str) -> dict[str, Any]:
    """Press a keyboard key (e.g. 'Enter', 'Tab', 'Escape')."""
    try:
        session = _get_session(user_id)
        _human_delay(session.page, "before_key")
        session.page.keyboard.press(key)
        _human_delay(session.page, "after_key")
        return {"status": "pressed", "key": key}
    except Exception as e:
        return {"error": f"Key press failed: {e}"}


def select_option(user_id: str, selector: str, value: str) -> dict[str, Any]:
    """Select an option from a dropdown."""
    try:
        session = _get_session(user_id)
        _human_delay(session.page, "before_select")
        session.page.select_option(selector, value, timeout=10000)
        _human_delay(session.page, "after_select")
        return {"status": "selected", "selector": selector, "value": value}
    except Exception as e:
        return {"error": f"Select failed: {e}"}


def wait_for(user_id: str, selector: str, timeout: int = 10000) -> dict[str, Any]:
    """Wait for an element to appear."""
    try:
        session = _get_session(user_id)
        session.page.wait_for_selector(selector, timeout=timeout)
        return {"status": "found", "selector": selector}
    except Exception as e:
        return {"error": f"Element not found: {e}"}


def get_elements(user_id: str, selector: str) -> dict[str, Any]:
    """Get text content of all elements matching a selector."""
    try:
        session = _get_session(user_id)
        elements = session.page.query_selector_all(selector)
        results = []
        for el in elements[:50]:  # cap at 50
            text = el.inner_text()
            tag = el.evaluate("el => el.tagName")
            results.append({"tag": tag, "text": text[:500]})
        return {"count": len(results), "elements": results}
    except Exception as e:
        return {"error": f"Query failed: {e}"}


# ---------------------------------------------------------------------------
# VNC interactive login
# ---------------------------------------------------------------------------

def _start_vnc_server(port: int | None = None) -> tuple[str, int, subprocess.Popen, subprocess.Popen]:
    """Start Xvfb + x11vnc.  Returns (display, vnc_port, xvfb_proc, vnc_proc)."""
    global _vnc_port_offset
    with _lock:
        _vnc_port_offset += 1
        display_num = 50 + _vnc_port_offset
        vnc_port = port or (settings.browser_vnc_base_port + _vnc_port_offset)

    display = f":{display_num}"
    xvfb = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", "1280x720x24"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)

    vnc = subprocess.Popen(
        ["x11vnc", "-display", display, "-rfbport", str(vnc_port),
         "-nopw", "-forever", "-shared", "-noxdamage",
         "-listen", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return display, vnc_port, xvfb, vnc


def _stop_vnc_server(xvfb: subprocess.Popen | None, vnc: subprocess.Popen | None) -> None:
    """Stop Xvfb + x11vnc processes."""
    for proc in [vnc, xvfb]:
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def start_interactive_login(user_id: str, url: str | None = None) -> dict[str, Any]:
    """Start a VNC session for manual browser login.

    Launches Xvfb + x11vnc + non-headless Chromium on the virtual display
    using the user's persistent profile.  The user SSH-tunnels to the VNC
    port and logs in manually.  Once done, call ``finish_interactive_login``.
    """
    if not settings.browser_profile_dir:
        return {"error": "Persistent profiles required (set BROWSER_PROFILE_DIR)"}
    if not settings.browser_vnc_enabled:
        return {"error": "VNC login disabled (set BROWSER_VNC_ENABLED=true)"}

    with _lock:
        if user_id in _vnc_sessions:
            info = _vnc_sessions[user_id]
            return {
                "status": "already_active",
                "vnc_port": info["port"],
                "instructions": (
                    f"VNC session already active on port {info['port']}. "
                    "Log in via your VNC client, then tell me when you're done."
                ),
            }

    # Close any existing headless session (profile lock).
    close_session(user_id)

    try:
        display, vnc_port, xvfb, vnc_proc = _start_vnc_server()
    except FileNotFoundError as e:
        return {"error": f"Required binary not found: {e}. Install Xvfb and x11vnc."}

    # Launch browser non-headless on the VNC display with persistent profile.
    session = BrowserSession(user_id)
    try:
        session.start(headless=False, vnc_display=display)
    except Exception as e:
        _stop_vnc_server(xvfb, vnc_proc)
        return {"error": f"Failed to start browser on VNC display: {e}"}

    if url:
        try:
            session.page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_timeout)
            session.page.wait_for_timeout(2000)
        except Exception:
            pass  # Partial load is fine for login pages.

    with _lock:
        _sessions[user_id] = session
        _vnc_sessions[user_id] = {
            "display": display,
            "port": vnc_port,
            "xvfb": xvfb,
            "vnc": vnc_proc,
            "url": url,
        }

    instructions = (
        f"I've opened a browser for you to log in manually.\n\n"
        f"Connect via VNC:\n"
        f"  1. SSH tunnel: ssh -NL {vnc_port}:localhost:{vnc_port} your-server\n"
        f"  2. VNC client: connect to localhost:{vnc_port}\n"
        f"  3. Log in to the site in the browser window\n"
        f"  4. When done, reply 'done' or 'I'm logged in'\n\n"
        f"Your login will be saved so I can access this site for you in the future."
    )

    return {
        "status": "started",
        "vnc_port": vnc_port,
        "url": url,
        "instructions": instructions,
    }


def finish_interactive_login(user_id: str) -> dict[str, Any]:
    """End the VNC login session and save the persistent profile.

    Future headless sessions will reuse the saved cookies/localStorage.
    """
    with _lock:
        vnc_info = _vnc_sessions.pop(user_id, None)
    if not vnc_info:
        return {"error": "No active VNC login session"}

    # Get page state before closing.
    session = _sessions.get(user_id)
    current_url = ""
    current_title = ""
    if session and session.page:
        try:
            current_url = session.page.url
            current_title = session.page.title()
        except Exception:
            pass

    # Close browser (saves persistent profile to disk).
    with _lock:
        session = _sessions.pop(user_id, None)
    if session:
        session.close()

    # Stop VNC.
    _stop_vnc_server(vnc_info.get("xvfb"), vnc_info.get("vnc"))

    logger.info("Finished interactive login for %s at %s", user_id, current_url)
    return {
        "status": "login_saved",
        "url": current_url,
        "title": current_title,
        "message": "Login saved. I'll use this session for future visits to this site.",
    }


def check_login_status(user_id: str, domain: str) -> dict[str, Any]:
    """Navigate to a domain and check if the browser appears logged in."""
    try:
        session = _get_session(user_id)
        session.page.goto(f"https://{domain}", wait_until="domcontentloaded", timeout=15000)
        _human_delay(session.page, "after_navigate")
        url = session.page.url
        title = session.page.title()
        text = session.page.inner_text("body")[:3000]

        is_login_page = _detect_login_wall(title, text, url)

        return {
            "domain": domain,
            "appears_logged_in": not is_login_page,
            "url": url,
            "title": title,
        }
    except Exception as e:
        return {"error": f"Login check failed: {e}"}


def list_profiles() -> dict[str, Any]:
    """List all saved browser profiles."""
    base = settings.browser_profile_dir
    if not base or not os.path.isdir(base):
        return {"profiles": [], "note": "No profile directory configured (set BROWSER_PROFILE_DIR)"}

    profiles = []
    for entry in sorted(os.listdir(base)):
        full = os.path.join(base, entry)
        if os.path.isdir(full):
            total = 0
            for dp, _dn, fnames in os.walk(full):
                for fn in fnames:
                    try:
                        total += os.path.getsize(os.path.join(dp, fn))
                    except OSError:
                        pass
            profiles.append({
                "user_id": f"+{entry}" if entry.isdigit() else entry,
                "path": full,
                "size_mb": round(total / (1024 * 1024), 1),
            })
    return {"profiles": profiles, "count": len(profiles)}


# ---------------------------------------------------------------------------
# Session cleanup
# ---------------------------------------------------------------------------

def close_session(user_id: str) -> dict[str, Any]:
    """Close the browser session for a user (including VNC if active)."""
    with _lock:
        vnc_info = _vnc_sessions.pop(user_id, None)
        session = _sessions.pop(user_id, None)
    if vnc_info:
        _stop_vnc_server(vnc_info.get("xvfb"), vnc_info.get("vnc"))
    if session:
        session.close()
        return {"status": "closed"}
    return {"status": "no_session"}


def close_all_sessions() -> None:
    """Close all browser sessions and VNC processes (call on app shutdown)."""
    with _lock:
        sessions = list(_sessions.values())
        _sessions.clear()
        vnc_infos = list(_vnc_sessions.values())
        _vnc_sessions.clear()
    for session in sessions:
        session.close()
    for vnc_info in vnc_infos:
        _stop_vnc_server(vnc_info.get("xvfb"), vnc_info.get("vnc"))
