"""Sandbox spoke agent — direct code execution in isolated containers.

Prax delegates headless coding/execution tasks here instead of keeping the
sandbox tools in the main orchestrator's tool list.  The sandbox agent writes
and runs code DIRECTLY in the container (shell, file editing, package install)
— there is no separate AI coding-agent session (the OpenCode subsystem was
removed from the sandbox image; Prax codes directly).
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
You are the Sandbox Agent for {agent_name}.  You write and run code DIRECTLY in
an isolated Docker container — there is no separate coding-agent session; YOU are
the one writing the commands and code.

## Available tools

### Shell & execution
- **sandbox_shell** — Run a shell command in the container via docker exec:
  ls, cat, grep, python script.py, pytest, pip, ffmpeg, pdflatex, git, etc.
  This is how you write files (heredoc/`tee`), run scripts, and inspect output.

### Reading files
- **sandbox_view / sandbox_scroll / sandbox_goto** — Page through a file in the
  container with line numbers (view a window, scroll, jump to a line).

### Environment
- **sandbox_install** — Install a system package (apt-get) in the container.
- **sandbox_rebuild** — Rebuild the sandbox Docker image for permanent changes.

(data_query and lean_check are also available when their flags are enabled.)

## Workflow
1. **Plan** the steps, then **write** code/files with sandbox_shell (e.g.
   `tee /workspace/active/foo.py <<'EOF' ... EOF`).
2. **Run** it with sandbox_shell and read the output.
3. **Iterate** — fix errors and re-run until it works.
4. **Deliver** any artifact the user should receive under /workspace/active/
   (the app's shared workspace), then report the filename.
5. **Report** honestly what you did, what was produced, and whether it succeeded.

## Rules
- Install missing packages before you need them.
- BOUND your output — the container disk IS the host disk; never run an unbounded
  generator (e.g. ffmpeg with a lavfi source needs `-t`).
- If something fails repeatedly, stop and report honestly rather than looping.
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
    """Delegate a headless code-execution task to the Sandbox Agent.

    The Sandbox Agent writes and runs code DIRECTLY in an isolated Docker
    container (shell, file editing, package install) and reports the result.
    There is no separate coding-agent session — it runs the commands itself.

    Use this for:
    - "Write a Python script that does X and run it"
    - "Generate a LaTeX document for Y"
    - "Run this code and show me the output"
    - "Install package Z in the sandbox"

    Do NOT use this for browser tasks (use delegate_browser), desktop/GUI tasks
    (use delegate_desktop), or for fixing Prax's own code (use delegate_sysadmin).

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
    """Return the delegation tool for the main agent.

    ``delegate_sandbox`` runs headless code-execution tasks directly in the
    container (no OpenCode session — that subsystem was removed). Registered
    whenever the sandbox is available (the caller already gates on
    ``settings.sandbox_available``).
    """
    return [delegate_sandbox]
