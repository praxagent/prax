"""LangChain tool wrappers for browser automation."""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.action_policy import RiskLevel, risk_tool
from prax.agent.user_context import current_user_id
from prax.services import browser_service


def _get_user_id() -> str:
    uid = current_user_id.get()
    if not uid:
        return "unknown"
    return uid


@tool
def browser_open(url: str) -> str:
    """Open a URL in the browser and return the page content.

    Handles JavaScript-heavy sites (Twitter/X, SPAs, etc.) by waiting
    for the page to render.  If the site requires login, use
    browser_credentials to get stored credentials, then browser_fill
    and browser_click to log in — or use browser_request_login for
    manual VNC-based login.
    """
    result = browser_service.navigate(_get_user_id(), url)
    if "error" in result:
        return f"Browser error: {result['error']}"
    output = f"**{result['title']}** ({result['url']})\n\n{result['content']}"
    if "login_hint" in result:
        output += f"\n\n**Note:** {result['login_hint']}"
    return output


@tool
def browser_read_page() -> str:
    """Get the full text content of the current browser page."""
    result = browser_service.get_content(_get_user_id())
    if "error" in result:
        return f"Browser error: {result['error']}"
    return f"**{result['title']}** ({result['url']})\n\n{result['content']}"


@tool
def browser_screenshot() -> str:
    """Take a screenshot of the current browser page.

    Returns the file path to the screenshot image.
    """
    result = browser_service.screenshot(_get_user_id())
    if "error" in result:
        return f"Browser error: {result['error']}"
    return f"Screenshot saved: {result['path']} (page: {result['url']})"


@risk_tool(risk=RiskLevel.HIGH)
def browser_click(selector: str) -> str:
    """Click an element on the current page.

    Use CSS selectors: 'button.submit', '#login', 'a[href="/about"]',
    'text=Log in', '[data-testid="tweet"]', etc.
    """
    result = browser_service.click(_get_user_id(), selector)
    if "error" in result:
        return f"Browser error: {result['error']}"
    return f"Clicked '{selector}'. Current URL: {result['url']}"


@risk_tool(risk=RiskLevel.HIGH)
def browser_fill(selector: str, text: str) -> str:
    """Type text into a form field on the current page.

    Use CSS selectors: 'input[name="username"]', '#password',
    'textarea.comment', '[placeholder="Search"]', etc.
    """
    result = browser_service.fill(_get_user_id(), selector, text)
    if "error" in result:
        return f"Browser error: {result['error']}"
    return f"Filled '{selector}' with text."


@tool
def browser_press(key: str) -> str:
    """Press a keyboard key: 'Enter', 'Tab', 'Escape', 'ArrowDown', etc."""
    result = browser_service.press_key(_get_user_id(), key)
    if "error" in result:
        return f"Browser error: {result['error']}"
    return f"Pressed '{key}'."


@tool
def browser_find(selector: str) -> str:
    """Find all elements matching a CSS selector and return their text.

    Useful for finding links, buttons, or content on the page.
    """
    result = browser_service.get_elements(_get_user_id(), selector)
    if "error" in result:
        return f"Browser error: {result['error']}"
    if not result["elements"]:
        return f"No elements found for '{selector}'"
    lines = [f"Found {result['count']} element(s):"]
    for el in result["elements"]:
        lines.append(f"  <{el['tag']}> {el['text'][:100]}")
    return "\n".join(lines)


@tool
def browser_credentials(domain: str) -> str:
    """Get stored login credentials for a website.

    Returns username/password from sites.yaml.  Use these with
    browser_fill to log into the site.
    """
    result = browser_service.get_credentials(domain)
    if "error" in result:
        return f"No credentials: {result['error']}"
    # Don't expose the actual password in the tool output.
    cred_keys = [k for k in result if k not in ("password", "domain")]
    info = ", ".join(f"{k}: {result[k]}" for k in cred_keys)
    return f"Credentials for {result['domain']}: {info}. Password is available — use browser_fill to enter it."


@tool
def browser_login(domain: str) -> str:
    """Get the stored password for a domain (to use with browser_fill).

    Returns the actual password value so you can fill it into the login form.
    Keep this value private — only use it with browser_fill.
    """
    result = browser_service.get_credentials(domain)
    if "error" in result:
        return f"No credentials: {result['error']}"
    password = result.get("password", "")
    username = result.get("username", result.get("email", ""))
    return f"username={username}\npassword={password}"


@tool
def browser_close() -> str:
    """Close the browser session.  Call when done browsing."""
    result = browser_service.close_session(_get_user_id())
    return f"Browser session {result['status']}."


# ---------------------------------------------------------------------------
# VNC / persistent profile tools
# ---------------------------------------------------------------------------

@risk_tool(risk=RiskLevel.HIGH)
def browser_request_login(url: str = "") -> str:
    """Start a VNC-based interactive login session for manual browser login.

    Instead of storing passwords in a config file, this opens a visible
    browser on a VNC display.  The user SSH-tunnels to the VNC port,
    logs in manually (handling MFA, CAPTCHAs, etc.), and the login
    session is saved to a persistent profile.  Future headless sessions
    reuse the saved cookies.

    Call browser_finish_login when the user is done logging in.
    Requires BROWSER_PROFILE_DIR and BROWSER_VNC_ENABLED to be configured.
    """
    result = browser_service.start_interactive_login(_get_user_id(), url or None)
    if "error" in result:
        return f"VNC login error: {result['error']}"
    if result.get("status") == "already_active":
        return result.get("instructions", f"VNC session already active on port {result['vnc_port']}.")
    return result.get("instructions", f"VNC session started on port {result.get('vnc_port')}")


@risk_tool(risk=RiskLevel.HIGH)
def browser_finish_login() -> str:
    """Finish the VNC interactive login session and save the browser profile.

    Call this after the user has completed logging in through VNC.
    The login state (cookies, localStorage) is saved to the persistent
    profile and will be reused in future headless browser sessions.
    """
    result = browser_service.finish_interactive_login(_get_user_id())
    if "error" in result:
        return f"Error: {result['error']}"
    return result.get("message", "Login session saved.")


@tool
def browser_check_login(domain: str) -> str:
    """Check if the browser appears to be logged into a domain.

    Navigates to the domain and uses heuristics to detect whether
    the page is a login wall or an authenticated view.
    """
    result = browser_service.check_login_status(_get_user_id(), domain)
    if "error" in result:
        return f"Error: {result['error']}"
    status = "logged in" if result["appears_logged_in"] else "NOT logged in"
    return f"Browser appears {status} to {result['domain']} (page: {result['title']})"


@tool
def browser_profiles() -> str:
    """List all saved browser profiles with persistent login sessions."""
    result = browser_service.list_profiles()
    profiles = result.get("profiles", [])
    if not profiles:
        note = result.get("note", "No profiles found.")
        return f"No saved browser profiles. {note}"
    lines = [f"Saved browser profiles ({result['count']}):"]
    for p in profiles:
        lines.append(f"  - {p['user_id']} ({p['size_mb']} MB)")
    return "\n".join(lines)


def build_browser_tools() -> list:
    return [
        browser_open, browser_read_page, browser_screenshot,
        browser_click, browser_fill, browser_press, browser_find,
        browser_credentials, browser_login, browser_close,
        browser_request_login, browser_finish_login,
        browser_check_login, browser_profiles,
    ]
