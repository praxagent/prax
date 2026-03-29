"""Sandbox spoke agent — code execution in isolated containers.

Prax delegates coding tasks here instead of keeping 9 sandbox tools in the
main orchestrator's tool list.  The sandbox agent manages session lifecycle,
communicates with the AI coding agent (OpenCode), handles artifact archival,
and manages package installation.
"""
from __future__ import annotations

import logging

from langchain_core.tools import tool

from prax.agent.spokes._runner import run_spoke
from prax.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are the Sandbox Agent for {agent_name}.  You manage sandboxed coding
sessions where an AI coding agent (OpenCode) writes and executes code inside
isolated Docker containers.

## Available tools

### Session lifecycle
- **sandbox_start** — Start a new coding session with a task description.
  Optionally specify a model (e.g. 'anthropic/claude-sonnet-4-5').
- **sandbox_message** — Send follow-up instructions to the active session.
- **sandbox_review** — Check session status (elapsed time, files, rounds).
- **sandbox_finish** — End the session and archive all artifacts to workspace.
- **sandbox_abort** — Kill the session without archiving (stuck/bad results).

### Search & re-execute
- **sandbox_search** — Search past solutions by keyword.
- **sandbox_execute** — Re-run an archived solution in a new container.

### Environment
- **sandbox_install** — Install a system package (apt-get) in the container.
- **sandbox_rebuild** — Rebuild the sandbox Docker image for permanent changes.

## Workflow
1. **Start** a session with a clear, detailed task description.
2. **Monitor** with sandbox_review if the orchestrator asks for status.
3. **Iterate** with sandbox_message — refine the task, request changes, or
   ask the coding agent to try a different approach.  Max 2-3 iterations.
4. **Finish** when done — sandbox_finish archives everything to the workspace.
5. **Report** back what was created, any files produced, and whether it succeeded.

## Rules
- Keep iterations tight — 2-3 sandbox_message calls max.
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
    prompt = SYSTEM_PROMPT.format(agent_name=settings.agent_name)
    return run_spoke(
        task=task,
        system_prompt=prompt,
        tools=build_tools(),
        config_key="subagent_sandbox",
        default_tier="low",
        role_name="Sandbox Agent",
        channel="engineering",
        recursion_limit=40,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def build_spoke_tools() -> list:
    """Return the delegation tool for the main agent."""
    return [delegate_sandbox]
