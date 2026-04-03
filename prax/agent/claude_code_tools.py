"""Coding agent tools — runs Claude Code, Codex, or OpenCode in the sandbox.

Gives Prax the ability to run a coding agent inside the sandbox container
for complex codebase tasks like bug fixing, refactoring, and feature
development.  The coding agent has full read-write access to the codebase
at /source/ and can read, edit, run tests, and use git.

Which agent is used is controlled by the SELF_IMPROVE_AGENT setting:
    claude-code  — Anthropic Claude Code (default)
    codex        — OpenAI Codex CLI
    opencode     — OpenCode (multi-provider)

Requires:
    SELF_IMPROVE_ENABLED=true  (global self-improvement gate)
    The chosen CLI installed in sandbox (all three are in the Dockerfile)
    Appropriate API key set in sandbox environment
"""
from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

from prax.agent.action_policy import RiskLevel, risk_tool
from prax.services import sandbox_service

logger = logging.getLogger(__name__)

# Track the last conversation ID for multi-turn sessions
_last_conversation_id: str | None = None


def _get_agent() -> str:
    """Return the configured coding agent name."""
    from prax.settings import settings as _s
    return getattr(_s, "self_improve_agent", "claude-code")


def _build_command(prompt: str, resume_id: str | None = None) -> str:
    """Build the CLI command for the configured coding agent."""
    agent = _get_agent()
    escaped = _shell_escape(prompt)

    if agent == "codex":
        # Codex CLI: codex -q --json "prompt"
        parts = ["codex"]
        if resume_id:
            parts.extend(["--resume", resume_id])
        parts.extend(["-q", "--full-auto", escaped])
        return " ".join(parts)

    if agent == "opencode":
        # OpenCode doesn't have a -p flag — use the API server already running.
        # Fall back to a one-shot command via stdin.
        return f"echo {escaped} | opencode chat"

    # Default: claude-code
    parts = ["claude"]
    if resume_id:
        parts.extend(["--resume", resume_id])
    parts.extend([
        "-p", escaped,
        "--output-format", "json",
        "--max-turns", "50",
        "--permission-mode", "bypassPermissions",
    ])
    return " ".join(parts)


def _parse_response(stdout: str, stderr: str, exit_code: int) -> dict:
    """Parse the CLI output into a structured response dict."""
    agent = _get_agent()

    if exit_code != 0 and not stdout:
        return {
            "response": f"[ERROR] {agent} exited with code {exit_code}. {stderr[:500]}",
            "exit_code": exit_code,
        }

    # Try JSON parse (claude-code and codex output JSON)
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        # Plain text output (opencode or fallback)
        return {"response": stdout, "exit_code": exit_code}

    conv_id = data.get("session_id") or data.get("conversation_id")

    response_text = ""
    if isinstance(data.get("result"), str):
        response_text = data["result"]
    elif isinstance(data.get("result"), list):
        for block in data["result"]:
            if isinstance(block, dict) and block.get("type") == "text":
                response_text += block.get("text", "")
    elif "result" not in data:
        response_text = stdout

    return {
        "response": response_text,
        "conversation_id": conv_id,
        "exit_code": exit_code,
        "cost": data.get("cost_usd"),
    }


def _run_coding_agent(prompt: str, resume_id: str | None = None, timeout: int = 300) -> dict:
    """Run the configured coding agent CLI in the sandbox.

    Returns dict with 'response', 'conversation_id', 'cost', 'exit_code'.
    """
    cmd = _build_command(prompt, resume_id)

    result = sandbox_service.run_shell(
        f"cd /source && {cmd}",
        timeout=timeout,
    )

    if "error" in result:
        return {"response": f"[ERROR] {result['error']}", "exit_code": -1}

    return _parse_response(
        result.get("stdout", "").strip(),
        result.get("stderr", ""),
        result.get("exit_code", -1),
    )


def _shell_escape(s: str) -> str:
    """Escape a string for use in a shell command."""
    return "'" + s.replace("'", "'\\''") + "'"


def _agent_channel() -> str:
    """Return the TeamWork channel name for the active coding agent."""
    return _get_agent()  # "claude-code", "codex", or "opencode"


def _mirror(prax_message: str | None = None, claude_response: str | None = None, meta: str = "") -> None:
    """Fire-and-forget mirror to the agent's TeamWork channel.

    The channel (#claude-code, #codex, or #opencode) is created lazily
    on first use — it only appears when the tool is actually invoked.
    """
    try:
        from prax.services.teamwork_hooks import mirror_coding_agent_turn
        mirror_coding_agent_turn(
            _agent_channel(), prax_message, claude_response, meta=meta,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@risk_tool(risk=RiskLevel.MEDIUM)
def claude_code_start_session(context: str = "") -> str:
    """Start a multi-turn collaboration session with the coding agent in the sandbox.

    The coding agent (Claude Code, Codex, or OpenCode — set by SELF_IMPROVE_AGENT)
    has full read-write access to the codebase at /source/. Use this for complex
    tasks that benefit from iterative back-and-forth: bug fixes, refactors, features.

    Provide initial context about what you want to accomplish. Be specific:
    include failure cases, relevant files, and what the fix should achieve.

    Returns a session_id to use with claude_code_message.

    Args:
        context: Initial context for the session — what you want to work on.
    """
    global _last_conversation_id

    if not context:
        return "Error: provide context for the session."

    agent = _get_agent()
    result = _run_coding_agent(context, timeout=300)
    conv_id = result.get("conversation_id")
    _last_conversation_id = conv_id
    response = result.get("response", "(no response)")

    _mirror(
        prax_message=context,
        claude_response=response,
        meta=f"Session started ({agent}): `{conv_id or 'unknown'}`",
    )

    msg = f"Session started ({agent}): {conv_id or 'unknown'}\n\n{response}"
    if result.get("cost"):
        msg += f"\n(cost: ${result['cost']:.4f})"
    return msg


@tool
def claude_code_message(session_id: str, message: str, timeout: int = 300) -> str:
    """Send a message in an active coding agent session.

    Continues a multi-turn conversation using --resume. The coding agent
    can read files, edit code, run tests, use git, and more.

    Args:
        session_id: The session/conversation ID from claude_code_start_session.
        message: Your message to the coding agent. Be specific and directive.
        timeout: Max seconds to wait (default: 300).
    """
    if not session_id or not message:
        return "Error: session_id and message are required"

    result = _run_coding_agent(message, resume_id=session_id, timeout=timeout)
    response = result.get("response", "(no response)")

    global _last_conversation_id
    if result.get("conversation_id"):
        _last_conversation_id = result["conversation_id"]

    _mirror(prax_message=message, claude_response=response)

    msg = response
    if result.get("cost"):
        msg += f"\n(cost: ${result['cost']:.4f})"
    return msg


@tool
def claude_code_ask(prompt: str, timeout: int = 300) -> str:
    """Ask the coding agent a one-shot question (no session).

    Use this for quick, self-contained questions that don't need
    iterative refinement. For complex tasks, use claude_code_start_session.

    Args:
        prompt: The question or task for the coding agent.
        timeout: Max seconds to wait (default: 300).
    """
    result = _run_coding_agent(prompt, timeout=timeout)
    response = result.get("response", "(no response)")
    agent = _get_agent()
    _mirror(prax_message=prompt, claude_response=response, meta=f"One-shot question ({agent})")
    return response


# ---------------------------------------------------------------------------
# Tool builder
# ---------------------------------------------------------------------------

def build_claude_code_tools() -> list:
    """Return coding agent tools if self-improvement is enabled.

    Returns an empty list if SELF_IMPROVE_ENABLED is false.
    The specific coding agent (claude-code/codex/opencode) is selected
    at runtime via SELF_IMPROVE_AGENT.
    """
    from prax.settings import settings as _settings
    if not _settings.self_improve_enabled:
        logger.debug("Coding agent tools disabled — SELF_IMPROVE_ENABLED=false")
        return []

    return [
        claude_code_start_session,
        claude_code_message,
        claude_code_ask,
    ]
