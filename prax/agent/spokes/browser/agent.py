"""Browser spoke agent — web navigation, login flows, and page interaction.

Prax delegates browser tasks here instead of calling 16 browser tools directly.
The browser agent knows when to use fast CDP vs reliable Playwright, handles
login flows autonomously, and reports results back to the orchestrator.

This is the reference spoke implementation.  Copy this folder to create a new spoke.
"""
from __future__ import annotations

import logging
import threading

from langchain_core.tools import tool

from prax.agent.spokes._runner import run_spoke
from prax.settings import settings

logger = logging.getLogger(__name__)

# Dedup identical parallel browser delegations (same pattern as sandbox).
_active_tasks: dict[str, str] = {}
_active_tasks_lock = threading.Lock()

# ---------------------------------------------------------------------------
# System prompt — the browser agent's role and routing logic
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are the Browser Agent for {agent_name}.  You control a live Chrome browser
that the user can see in real-time via TeamWork's browser panel.  Everything you
do in the browser is visible to the user — act confidently without asking for
confirmation before clicks or navigation.

## Tool Selection: CDP First, Playwright for Complex Tasks

You have two APIs to the SAME Chrome instance:

### Fast path — Raw CDP tools (use by default)
- **sandbox_browser_read** — quick page text, URL, screenshots, scrolling
- **sandbox_browser_act** — navigate, click (by visible text or CSS), type, press keys

Use CDP for: reading pages, taking screenshots, simple navigation, quick clicks,
typing into focused fields.  CDP is faster because it sends raw WebSocket
commands with no abstraction overhead.

### Reliable path — Playwright tools (use when CDP isn't enough)
- **browser_open** — navigate with auto-wait + JS rendering + login wall detection
- **browser_fill** — reliable form filling (handles focus, clear, type)
- **browser_click** — click by CSS selector with auto-wait
- **browser_find** — query elements by CSS selector
- **browser_press** — press keyboard keys
- **browser_screenshot** — PNG screenshot of the current page, saved into
  the user's active workspace.  Returns the filename so you can deliver
  it with ``workspace_send_file``.
- **browser_page_screenshot** — **one-shot** tool for "send me a screenshot
  of X" requests: navigates to a URL, takes a screenshot, and delivers it
  to the user's current channel in one call.  Use this whenever the user
  asks for a screenshot of a specific URL instead of chaining
  browser_open → browser_screenshot → workspace_send_file manually.
- **browser_read_page** — full page text content

Use Playwright for: login flows, form filling, waiting for elements to appear,
complex selectors (XPath, role-based), file uploads, iframe traversal, multi-step
interactions where reliability matters more than speed.

### Login & credential tools (Playwright)
- **browser_credentials** — look up stored credentials for a domain
- **browser_login** — get the actual password for form filling
- **browser_check_login** — check if logged into a domain
- **browser_request_login** — start VNC session for manual login (MFA, CAPTCHAs)
- **browser_finish_login** — end VNC session and save profile
- **browser_profiles** — list saved browser profiles
- **browser_close** — close the browser session

## Workflow

1. **Read first.** Before acting, use ``sandbox_browser_read("text")`` or
   ``sandbox_browser_read("url")`` to understand what's currently on screen.
2. **Navigate with CDP** by default: ``sandbox_browser_act("navigate", url)``.
   Fall back to ``browser_open`` if the page needs JS rendering or login detection.
3. **Click by visible text** when possible: ``sandbox_browser_act("click", "Sign in")``.
   Fall back to Playwright's ``browser_click`` for CSS/XPath selectors with auto-wait.
4. **Login walls:** If you detect a login page, check ``browser_credentials`` first.
   If credentials exist, use Playwright's ``browser_fill`` to enter them reliably.
   If not, offer ``browser_request_login`` for VNC-based manual login.
5. **Return clear results.** Summarize what you found or did.  Include relevant
   page content, URLs, or screenshot paths.

## Rules
- NEVER reveal stored passwords in your response — only use them with browser_fill.
- If a page fails to load via CDP, try Playwright before reporting failure.
- Keep your output concise — the orchestrator will relay it to the user.
- If you take a screenshot, include the file path so the orchestrator can share it.
- **Pace yourself.** Some sites (Hacker News, Reddit, etc.) rate-limit rapid requests.
  If a page returns an error or "Sorry" message, wait a moment and retry with
  ``browser_open`` (Playwright) instead of CDP — it waits for full page load.
  Don't rapid-fire multiple navigations back-to-back without reading results first.
"""


# ---------------------------------------------------------------------------
# Tool assembly — curated set for browser work
# ---------------------------------------------------------------------------

def build_tools() -> list:
    """Return all tools available to the browser spoke agent."""
    from prax.agent.browser_tools import build_browser_tools
    from prax.agent.cdp_tools import build_cdp_tools

    return build_cdp_tools() + build_browser_tools()


# ---------------------------------------------------------------------------
# Delegation function — this is what the orchestrator calls
# ---------------------------------------------------------------------------

@tool
def delegate_browser(task: str) -> str:
    """Delegate a browser task to the Browser Agent.

    The Browser Agent controls the live Chrome browser (visible in TeamWork).
    It handles web navigation, page reading, form filling, login flows, and
    screenshots.  It chooses between fast CDP and reliable Playwright
    automatically based on the task.

    ALWAYS use this when the user says "in this browser", "in the browser",
    "open/navigate/go to [URL]", "show me", or any phrase implying they
    want to SEE something in the browser panel.  Do NOT use fetch_url_content
    or text responses when the user expects browser interaction.

    Use this for:
    - "Open this URL and tell me what it says"
    - "Find me an interesting article on Hacker News" (when via TeamWork)
    - "Log into x.com and read this tweet"
    - "Fill out this form with these values"
    - "Take a screenshot of the current page"
    - "Send me a screenshot of the NYT front page" / "grab a screenshot
      of [URL]" / "what does [site] look like right now" — the browser
      agent uses ``browser_page_screenshot`` which navigates, captures,
      and delivers the image to the user's channel in one call.
    - "Search Google for X and summarize the results"
    - "Navigate to a site, click through pages, extract data"

    Args:
        task: A clear, self-contained description of what to do in the browser.
              Include URLs, search terms, and any context the agent needs —
              it cannot see your conversation history.
    """
    from prax.agent.user_context import current_user_id
    uid = current_user_id.get() or "unknown"

    normalised = task.strip().lower()[:200]
    with _active_tasks_lock:
        existing = _active_tasks.get(uid)
        if existing == normalised:
            logger.info("Duplicate delegate_browser call for user %s — same task, skipping", uid)
            return (
                "An identical browser delegation is already running. "
                "Wait for it to complete — no need to call this twice."
            )
        _active_tasks[uid] = normalised

    try:
        prompt = SYSTEM_PROMPT.format(agent_name=settings.agent_name)
        return run_spoke(
            task=task,
            system_prompt=prompt,
            tools=build_tools(),
            config_key="subagent_browser",
            default_tier="low",
            role_name="Browser Agent",
            channel="browser",
            recursion_limit=60,
        )
    finally:
        with _active_tasks_lock:
            _active_tasks.pop(uid, None)


# ---------------------------------------------------------------------------
# Registration — the orchestrator imports this
# ---------------------------------------------------------------------------

def build_spoke_tools() -> list:
    """Return the delegation tool for the main agent."""
    return [delegate_browser]
