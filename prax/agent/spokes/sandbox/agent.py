"""Sandbox spoke agent — code execution in isolated containers.

Prax delegates coding tasks here instead of keeping 9 sandbox tools in the
main orchestrator's tool list.  The sandbox agent manages session lifecycle,
communicates with the AI coding agent (OpenCode), handles artifact archival,
and manages package installation.
"""
from __future__ import annotations

import logging
import threading

from langchain_core.tools import tool

from prax.agent.spokes._runner import run_spoke
from prax.settings import settings

logger = logging.getLogger(__name__)

# Track active delegation tasks per user to deduplicate identical parallel
# calls.  LLMs sometimes emit the same delegate_sandbox tool call twice in
# one response; LangGraph runs them concurrently.  We let the first through
# and short-circuit the duplicate.  Genuinely different tasks are allowed.
_active_tasks: dict[str, str] = {}  # uid -> normalised task text
_active_tasks_lock = threading.Lock()

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are the Sandbox Agent for {agent_name}.  You manage sandboxed coding
sessions where an AI coding agent (OpenCode) writes and executes code inside
isolated Docker containers.

## Available tools

### Direct shell (fast — no AI agent needed)
- **sandbox_shell** — Run a shell command directly in the sandbox container
  via docker exec.  Use for simple commands: ls, pwd, df -h, cat, grep,
  python -c '...', du, find, env, pip list, etc.  Instant results — no
  session overhead.

### Session lifecycle (for complex coding tasks)
- **sandbox_start** — Start a new coding session with an AI coding agent.
  Returns a session_id.  You can run multiple sessions concurrently.
- **sandbox_message** — Send follow-up instructions to a session.
  Pass session_id if you have multiple sessions; omit to target the latest.
- **sandbox_review** — Check session status (elapsed time, files, rounds).
- **sandbox_finish** — End the session and archive all artifacts to workspace.
- **sandbox_abort** — Kill the session without archiving (stuck/bad results).

### Search & re-execute
- **sandbox_search** — Search past solutions by keyword.
- **sandbox_execute** — Re-run an archived solution in a new container.

### Environment
- **sandbox_install** — Install a system package (apt-get) in the container.
- **sandbox_rebuild** — Rebuild the sandbox Docker image for permanent changes.

## Choosing the right tool

- **Simple commands** (ls, df, pwd, cat, grep, running a script) →
  use **sandbox_shell**.  This is instant.
- **Complex coding tasks** (write a script, generate a document, multi-step
  development) → use **sandbox_start** + **sandbox_message** to work with
  the AI coding agent.
- Do NOT start an OpenCode session just to run shell commands.

## Workflow for coding tasks
1. **Start** a session with a clear, detailed task description.
2. **Monitor** with sandbox_review if the orchestrator asks for status.
3. **Iterate** with sandbox_message — refine the task, request changes, or
   ask the coding agent to try a different approach.  Max 2-3 iterations.
4. **Finish** when done — sandbox_finish archives everything to the workspace.
5. **Report** back what was created, any files produced, and whether it succeeded.

## Rules
- For simple commands, ALWAYS prefer sandbox_shell over sandbox_start.
- Keep iterations tight — 2-3 sandbox_message calls max per session.
- If the session times out or errors repeatedly, abort and report honestly.
- Always finish or abort sessions — don't leave them running.
- If the task needs a missing package, install it before starting the session.
"""


# ---------------------------------------------------------------------------
# Tool assembly
# ---------------------------------------------------------------------------

def build_tools() -> list:
    """Return all tools available to the sandbox spoke."""
    from prax.agent.sandbox_tools import build_sandbox_tools
    return build_sandbox_tools()


# ---------------------------------------------------------------------------
# Delegation function
# ---------------------------------------------------------------------------

@tool
def delegate_sandbox(task: str) -> str:
    """Delegate a coding task to the Sandbox Agent.

    The Sandbox Agent manages isolated coding sessions with an AI coding
    agent (OpenCode) inside Docker containers.  It handles starting sessions,
    sending instructions, reviewing progress, and archiving results.

    Use this for:
    - "Write a Python script that does X"
    - "Generate a LaTeX document for Y"
    - "Run this code and show me the output"
    - "Re-execute the solution from last week"
    - "Install package Z in the sandbox"

    Do NOT use this for browser tasks (use delegate_browser) or for
    fixing Prax's own code (use delegate_sysadmin).

    Args:
        task: A clear, self-contained description of the coding task.
              Include any specific requirements, file formats, or constraints.
    """
    from prax.agent.user_context import current_user_id
    uid = current_user_id.get() or "unknown"

    # Deduplicate identical parallel calls (LLM emits the same tool call
    # twice in one response).  Different tasks are allowed through.
    normalised = task.strip().lower()[:200]
    with _active_tasks_lock:
        existing = _active_tasks.get(uid)
        if existing == normalised:
            logger.info("Duplicate delegate_sandbox call for user %s — same task, skipping", uid)
            return (
                "An identical sandbox delegation is already running. "
                "Wait for it to complete — no need to call this twice."
            )
        _active_tasks[uid] = normalised

    try:
        prompt = SYSTEM_PROMPT.format(agent_name=settings.agent_name)
        return run_spoke(
            task=task,
            system_prompt=prompt,
            tools=build_tools(),
            config_key="subagent_sandbox",
            default_tier="low",
            role_name="Sandbox Agent",
            channel="engineering",
            recursion_limit=80,
        )
    finally:
        with _active_tasks_lock:
            _active_tasks.pop(uid, None)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def build_spoke_tools() -> list:
    """Return the delegation tool for the main agent."""
    return [delegate_sandbox]
