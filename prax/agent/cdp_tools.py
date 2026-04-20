"""LangChain tool wrappers for CDP browser control.

These tools connect to the sandbox Chrome instance — the same browser the
user sees via the TeamWork browser panel.  Actions taken here are visible
to the user in real-time.

Consolidated into 2 tools to stay within API tool count limits.
"""
from __future__ import annotations

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


# ---------------------------------------------------------------------------
# browser_verify — structured E2E verification
# ---------------------------------------------------------------------------
#
# Anthropic's long-running harness post credits Puppeteer MCP with
# catching a whole class of bugs invisible from code alone. This is
# Prax's analogue: one tool call drives a declarative sequence
# (goto/click/type/key/wait_for/assert_visible/assert_text/screenshot)
# and returns per-step pass/fail so the agent can reason about which
# step broke the flow, not just "it's red somewhere."
#
# Consumed by the content spoke (post-publish Hugo page render check),
# self-improve flow (hot-deploy smoke test), and any future agent
# task that needs to verify UI behaviour.

_VERIFY_MAX_STEPS = 40
_WAIT_TIMEOUT_MS = 5000
_WAIT_POLL_MS = 200


def _wait_for_condition(js_expr: str, timeout_ms: int = _WAIT_TIMEOUT_MS) -> dict:
    """Poll a JS boolean expression until true or timeout. Returns structured result."""
    import time as _time
    deadline = _time.time() + timeout_ms / 1000.0
    poll_interval = _WAIT_POLL_MS / 1000.0
    last_err: str | None = None
    while _time.time() < deadline:
        result = cdp_service.evaluate_js(f"!!({js_expr})")
        if "error" in result:
            last_err = result["error"]
            _time.sleep(poll_interval)
            continue
        if bool(result.get("value")):
            return {"ok": True}
        _time.sleep(poll_interval)
    return {"ok": False, "error": last_err or f"timed out after {timeout_ms}ms"}


def _js_string(s: str) -> str:
    """JSON-encode for safe injection into JS expressions."""
    import json as _json
    return _json.dumps(s)


def _run_step(step: dict) -> dict:
    """Execute one verify step. Returns {'step': verb, 'ok': bool, 'detail': str, 'screenshot': path?}."""
    if not isinstance(step, dict) or len(step) != 1:
        return {"step": "?", "ok": False, "detail": "Each step must be a one-key dict, e.g. {'goto': 'url'}."}
    verb, arg = next(iter(step.items()))
    try:
        if verb == "goto":
            r = cdp_service.navigate(str(arg))
            if "error" in r:
                return {"step": verb, "ok": False, "detail": r["error"]}
            return {"step": verb, "ok": True, "detail": f"{r.get('url', '')} — {r.get('title', '')}"}

        if verb == "click":
            selector = str(arg)
            if _is_css_selector(selector):
                r = cdp_service.click_element(selector)
            else:
                r = cdp_service.click_text(selector)
            if "error" in r:
                return {"step": verb, "ok": False, "detail": f"{selector}: {r['error']}"}
            return {"step": verb, "ok": True, "detail": selector}

        if verb == "type":
            if isinstance(arg, dict):
                selector = arg.get("selector", "")
                text = arg.get("text", "")
                if selector:
                    focus = cdp_service.evaluate_js(
                        f"document.querySelector({_js_string(selector)}).focus()"
                    )
                    if "error" in focus:
                        return {"step": verb, "ok": False, "detail": f"{selector}: focus failed"}
                r = cdp_service.type_text(str(text))
            else:
                r = cdp_service.type_text(str(arg))
            if "error" in r:
                return {"step": verb, "ok": False, "detail": r["error"]}
            return {"step": verb, "ok": True, "detail": "typed"}

        if verb == "key":
            r = cdp_service.press_key(str(arg))
            if "error" in r:
                return {"step": verb, "ok": False, "detail": r["error"]}
            return {"step": verb, "ok": True, "detail": str(arg)}

        if verb == "wait_for":
            selector = str(arg)
            js = f"document.querySelector({_js_string(selector)}) !== null"
            r = _wait_for_condition(js)
            if not r["ok"]:
                return {"step": verb, "ok": False, "detail": f"{selector}: {r.get('error', 'not found')}"}
            return {"step": verb, "ok": True, "detail": selector}

        if verb == "assert_visible":
            selector = str(arg)
            js = (
                f"(function() {{"
                f"  var el = document.querySelector({_js_string(selector)});"
                f"  if (!el) return false;"
                f"  var r = el.getBoundingClientRect();"
                f"  return r.width > 0 && r.height > 0;"
                f"}})()"
            )
            r = cdp_service.evaluate_js(js)
            if "error" in r:
                return {"step": verb, "ok": False, "detail": r["error"]}
            ok = bool(r.get("value"))
            return {
                "step": verb, "ok": ok,
                "detail": selector + ("" if ok else " (not visible)"),
            }

        if verb == "assert_text":
            text = str(arg)
            r = cdp_service.get_page_text()
            if "error" in r:
                return {"step": verb, "ok": False, "detail": r["error"]}
            ok = text in (r.get("text") or "")
            return {
                "step": verb, "ok": ok,
                "detail": text if ok else f"'{text}' not present on page",
            }

        if verb == "screenshot":
            r = cdp_service.screenshot()
            if "error" in r:
                return {"step": verb, "ok": False, "detail": r["error"]}
            return {
                "step": verb, "ok": True, "detail": r.get("path", ""),
                "screenshot": r.get("path"),
            }

        return {"step": verb, "ok": False, "detail": f"unknown verb '{verb}'"}

    except Exception as e:
        return {"step": verb, "ok": False, "detail": f"exception: {e}"}


def _render_results(results: list[dict]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        mark = "✓" if r.get("ok") else "✗"
        lines.append(f"  Step {i} {r.get('step', '?')}: {mark}  {r.get('detail', '')}")
    passed = sum(1 for r in results if r.get("ok"))
    header = f"browser_verify: {passed}/{len(results)} steps passed"
    # On failure, snapshot current page state for debugging.
    if passed < len(results):
        try:
            snap = cdp_service.get_page_text(max_length=600)
            if "error" not in snap:
                lines.append("")
                lines.append(f"  Page at failure: **{snap.get('title', '')}** ({snap.get('url', '')})")
                excerpt = (snap.get("text") or "")[:400]
                if excerpt:
                    lines.append(f"  Excerpt: {excerpt}")
        except Exception:
            pass
    return header + "\n" + "\n".join(lines)


@tool
def browser_verify(flow: list[dict]) -> str:
    """Drive a declarative E2E verification flow in the sandbox browser.

    Use this when you've built or changed a UI feature and need to
    confirm it works end-to-end — not just "unit tests pass." Each
    step is a one-key dict naming a verb and its argument.

    Verbs:
      {"goto": "http://..."}                   navigate to URL
      {"click": "Sign in"}                     click by visible text or CSS selector
      {"type": {"selector": "input[name='q']", "text": "hello"}}  focus + type
      {"type": "just text"}                    type into focused element
      {"key": "Enter"}                         press a key
      {"wait_for": "nav.logged-in"}            wait up to 5s for selector to appear
      {"assert_visible": "div.success"}        assert selector exists and has size
      {"assert_text": "Welcome back"}          assert text is present anywhere
      {"screenshot": "login-success"}          capture current viewport

    Returns structured per-step pass/fail. On any failure, the page's
    current title/URL/excerpt is appended so you can diagnose without
    another tool call.

    Max 40 steps per call. Keep flows focused — one feature, one call.
    """
    if not cdp_service.is_available():
        return (
            "browser_verify unavailable: sandbox browser (Chrome) is not running. "
            "Start it first via sandbox_start or ask the user to open the browser panel."
        )
    if not isinstance(flow, list) or not flow:
        return "browser_verify: `flow` must be a non-empty list of step dicts."
    if len(flow) > _VERIFY_MAX_STEPS:
        return f"browser_verify: flow has {len(flow)} steps; max is {_VERIFY_MAX_STEPS}. Split into multiple calls."
    results: list[dict] = []
    for step in flow:
        r = _run_step(step)
        results.append(r)
        if not r.get("ok"):
            # Short-circuit — later steps almost never recover from a missing element.
            break
    return _render_results(results)


def build_cdp_tools() -> list:
    """Return CDP browser tools."""
    return [sandbox_browser_read, sandbox_browser_act, browser_verify]
