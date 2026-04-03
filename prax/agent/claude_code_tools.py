"""Claude Code collaboration tools — multi-turn sessions with Claude Code.

Gives Prax the ability to have back-and-forth conversations with Claude Code
(running on the host via the bridge service) for complex codebase tasks like
bug fixing, refactoring, and feature development.

The bridge must be running on the host (``./scripts/start_claude_bridge.sh``).
If the bridge is down, these tools report that clearly and are excluded from
the agent's tool set.

Environment:
    CLAUDE_BRIDGE_URL    — bridge endpoint (default: http://host.docker.internal:9819)
    CLAUDE_BRIDGE_SECRET — shared auth secret (must match the bridge)
"""
from __future__ import annotations

import logging

import requests
from langchain_core.tools import tool

from prax.agent.action_policy import RiskLevel, risk_tool

logger = logging.getLogger(__name__)

# Cache the availability check for the lifetime of this import
_bridge_available: bool | None = None


def _bridge_url() -> str:
    from prax.settings import settings
    return getattr(settings, "claude_bridge_url", "") or ""


def _bridge_secret() -> str:
    from prax.settings import settings
    return getattr(settings, "claude_bridge_secret", "") or ""


def _headers() -> dict:
    h: dict[str, str] = {"Content-Type": "application/json"}
    secret = _bridge_secret()
    if secret:
        h["Authorization"] = f"Bearer {secret}"
    return h


def is_bridge_available() -> bool:
    """Check if the Claude Code bridge is reachable.

    Result is cached — call ``reset_bridge_cache()`` to re-check.
    """
    global _bridge_available
    if _bridge_available is not None:
        return _bridge_available

    url = _bridge_url()
    if not url:
        _bridge_available = False
        return False

    try:
        resp = requests.get(f"{url}/health", timeout=3)
        _bridge_available = resp.ok
        if _bridge_available:
            data = resp.json()
            logger.info(
                "Claude Code bridge available (version=%s, repo=%s)",
                data.get("claude_version", "?"),
                data.get("repo_path", "?"),
            )
        return _bridge_available
    except Exception:
        _bridge_available = False
        return False


def reset_bridge_cache() -> None:
    """Reset the bridge availability cache so the next call re-probes."""
    global _bridge_available
    _bridge_available = None


def _post(path: str, body: dict, timeout: int = 300) -> dict:
    """Make a POST request to the bridge."""
    url = _bridge_url()
    if not url:
        return {"error": "CLAUDE_BRIDGE_URL not configured"}

    try:
        resp = requests.post(
            f"{url}{path}",
            json=body,
            headers=_headers(),
            timeout=timeout + 10,  # slightly longer than Claude Code's own timeout
        )
        if resp.status_code == 401:
            return {"error": "Bridge auth failed — check CLAUDE_BRIDGE_SECRET"}
        return resp.json()
    except requests.Timeout:
        return {"error": "Bridge request timed out"}
    except requests.ConnectionError:
        reset_bridge_cache()
        return {"error": "Claude Code bridge is not running. Ask the user to start it: ./scripts/start_claude_bridge.sh"}
    except Exception as e:
        return {"error": f"Bridge error: {e}"}


# ---------------------------------------------------------------------------
# Session-based tools (multi-turn conversations)
# ---------------------------------------------------------------------------

@risk_tool(risk=RiskLevel.MEDIUM)
def claude_code_start_session(context: str = "") -> str:
    """Start a multi-turn collaboration session with Claude Code.

    Claude Code is a powerful coding agent running on the host machine with
    full access to the codebase, terminal, and git. Use this when you need
    to collaborate on complex tasks — bug fixes, refactors, new features.

    Unlike one-shot prompts, sessions maintain context across turns, allowing
    iterative refinement — just like a pair programming conversation.

    Provide initial context about what you want to accomplish. Be specific:
    include the failure cases, relevant files, and what the fix should achieve.

    Returns a session_id to use with claude_code_message.

    Args:
        context: Initial context for the session — what you want to work on.
                 Include failure cases, relevant source files, eval criteria.
    """
    if not is_bridge_available():
        return (
            "Claude Code bridge is not running. "
            "The user needs to start it on the host: ./scripts/start_claude_bridge.sh"
        )

    result = _post("/session/start", {"context": context})
    if "error" in result:
        return f"Error: {result['error']}"

    session_id = result.get("session_id", "")
    response = result.get("response", "")

    msg = f"Session started: {session_id}\n\n"
    if response:
        msg += f"Claude Code: {response}"
    else:
        msg += "Ready for your first message. Use claude_code_message to continue."
    return msg


@tool
def claude_code_message(session_id: str, message: str, timeout: int = 300) -> str:
    """Send a message in an active Claude Code session.

    This is the core collaboration tool — use it to have back-and-forth
    conversations with Claude Code. Claude Code can read files, edit code,
    run tests, use git, and more.

    Tips for effective collaboration:
    - Be specific about what you want changed and why
    - Ask Claude Code to explain its approach before making changes
    - Request diffs before committing
    - Ask it to run the relevant tests after changes
    - Iterate: if the first attempt isn't right, explain what's wrong

    Args:
        session_id: The session ID from claude_code_start_session.
        message: Your message to Claude Code. Be specific and directive.
        timeout: Max seconds to wait for a response (default: 300).
    """
    if not session_id or not message:
        return "Error: session_id and message are required"

    result = _post("/session/message", {
        "session_id": session_id,
        "message": message,
        "timeout": timeout,
    }, timeout=timeout)

    if "error" in result:
        return f"Error: {result['error']}"

    response = result.get("response", "(no response)")
    turn = result.get("turn", "?")
    cost = result.get("cost")

    msg = f"[Turn {turn}] {response}"
    if cost:
        msg += f"\n(cost: ${cost:.4f})"
    return msg


@tool
def claude_code_end_session(session_id: str) -> str:
    """End a Claude Code collaboration session.

    Call this when the collaboration is complete. The session context
    is released and cannot be resumed.

    Args:
        session_id: The session to end.
    """
    result = _post("/session/end", {"session_id": session_id})
    if "error" in result:
        return f"Error: {result['error']}"
    turns = result.get("turns", 0)
    return f"Session {session_id} ended after {turns} turns."


@tool
def claude_code_ask(prompt: str, timeout: int = 300) -> str:
    """Ask Claude Code a one-shot question (no session).

    Use this for quick, self-contained questions that don't need
    iterative refinement. For complex tasks, use claude_code_start_session
    instead for multi-turn collaboration.

    Args:
        prompt: The question or task for Claude Code.
        timeout: Max seconds to wait (default: 300).
    """
    if not is_bridge_available():
        return (
            "Claude Code bridge is not running. "
            "The user needs to start it on the host: ./scripts/start_claude_bridge.sh"
        )

    result = _post("/ask", {"prompt": prompt, "timeout": timeout}, timeout=timeout)
    if "error" in result:
        return f"Error: {result['error']}"
    return result.get("response", "(no response)")


# ---------------------------------------------------------------------------
# Tool builder
# ---------------------------------------------------------------------------

def build_claude_code_tools() -> list:
    """Return Claude Code tools if the bridge is configured and reachable.

    Returns an empty list if CLAUDE_BRIDGE_URL is not set or the bridge
    is not responding — Prax won't even see these tools if they can't work.
    """
    if not is_bridge_available():
        logger.debug("Claude Code bridge not available — tools disabled")
        return []

    return [
        claude_code_start_session,
        claude_code_message,
        claude_code_end_session,
        claude_code_ask,
    ]
