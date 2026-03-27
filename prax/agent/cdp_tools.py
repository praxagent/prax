"""LangChain tool wrappers for CDP browser control.

These tools connect to the sandbox Chrome instance — the same browser the
user sees via the TeamWork browser panel.  Actions taken here are visible
to the user in real-time.

Consolidated into 2 tools to stay within API tool count limits.
"""
from __future__ import annotations

import json

from langchain_core.tools import tool

from prax.agent.action_policy import RiskLevel, risk_tool
from prax.services import cdp_service


@tool
def sandbox_browser_read(action: str = "text") -> str:
    """Read from the sandbox browser (Chrome visible in TeamWork).

    Actions:
      "text"       — get the visible text content of the current page
      "url"        — get the current URL and title
      "screenshot" — take a screenshot (returns file path)
      "scroll_down" / "scroll_up" — scroll the page

    The user can see this browser in real-time via TeamWork's browser panel.
    Use "text" to see what's currently on screen.
    """
    if not cdp_service.is_available():
        return "Sandbox browser not available (Chrome may not be running)."

    if action == "text":
        result = cdp_service.get_page_text()
        if "error" in result:
            return f"Browser error: {result['error']}"
        return f"**{result['title']}** ({result['url']})\n\n{result['text']}"

    elif action == "url":
        result = cdp_service.get_page_url()
        if "error" in result:
            return f"Browser error: {result['error']}"
        return f"Current page: {result.get('title', 'Untitled')} — {result.get('url', 'unknown')}"

    elif action == "screenshot":
        result = cdp_service.screenshot()
        if "error" in result:
            return f"Browser error: {result['error']}"
        return f"Screenshot saved: {result['path']}"

    elif action in ("scroll_down", "scroll_up"):
        direction = "down" if action == "scroll_down" else "up"
        result = cdp_service.scroll_page(direction)
        if "error" in result:
            return f"Browser error: {result['error']}"
        return result["status"]

    else:
        return f"Unknown action: {action}. Use: text, url, screenshot, scroll_down, scroll_up"


def _is_css_selector(value: str) -> bool:
    """Heuristic: does this look like a CSS selector rather than visible text?"""
    css_indicators = ("#", ".", "[", "::", ">", "~", "+", "=")
    return any(ch in value for ch in css_indicators)


@risk_tool(risk=RiskLevel.MEDIUM)
def sandbox_browser_act(action: str, value: str = "") -> str:
    """Perform an action in the sandbox browser (Chrome visible in TeamWork).

    The user sees everything you do in real-time — no extra confirmation
    needed since they are watching the browser live. Do NOT ask the user
    to confirm clicks or navigation — just do it.

    Actions:
      "navigate"  — go to a URL (value = the URL)
      "click"     — click an element by visible text OR CSS selector
                     Preferred: use the visible text, e.g. "Sign in", "Next", "Submit"
                     Also works: CSS selectors like "button.submit", "#login-btn"
      "type"      — type text into the currently focused element (value = text to type)
      "key"       — press a key (value = Enter, Tab, Escape, Backspace, ArrowDown, etc.)

    Example workflow:
      1. sandbox_browser_act("navigate", "https://example.com")
      2. sandbox_browser_read("text")  — see what loaded
      3. sandbox_browser_act("click", "Sign in")  — click by visible text
      4. sandbox_browser_act("type", "my search query")
      5. sandbox_browser_act("key", "Enter")
    """
    if action == "navigate":
        if not value:
            return "Error: provide a URL to navigate to"
        result = cdp_service.navigate(value)
        if "error" in result:
            return f"Browser error: {result['error']}"
        return f"**{result.get('title', '')}** ({result.get('url', '')})\n\n{result.get('text', '')}"

    elif action == "click":
        if not value:
            return "Error: provide visible text or a CSS selector to click"
        # Auto-detect: CSS selector vs visible text
        if _is_css_selector(value):
            result = cdp_service.click_element(value)
        else:
            result = cdp_service.click_text(value)
        if "error" in result:
            return f"Browser error: {result['error']}"
        return result["status"]

    elif action == "type":
        result = cdp_service.type_text(value)
        if "error" in result:
            return f"Browser error: {result['error']}"
        return result["status"]

    elif action == "key":
        if not value:
            return "Error: provide a key name (Enter, Tab, Escape, etc.)"
        result = cdp_service.press_key(value)
        if "error" in result:
            return f"Browser error: {result['error']}"
        return result["status"]

    else:
        return f"Unknown action: {action}. Use: navigate, click, type, key"


def build_cdp_tools() -> list:
    """Return CDP browser tools."""
    return [sandbox_browser_read, sandbox_browser_act]
