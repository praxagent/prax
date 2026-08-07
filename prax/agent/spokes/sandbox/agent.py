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


def _container_user_workspace(uid: str) -> str:
    """The CURRENT USER's directory as seen inside the sandbox container.

    The sandbox bind-mounts the whole ``workspaces/`` directory at
    ``/workspace``, so the user's root is ``/workspace/<their-dir>`` — NOT
    ``/workspace`` itself, and NOT ``/workspace/active`` (which resolves to a
    directory belonging to no user; artifacts written there are invisible to
    ``workspace_send_file`` and were observed stranded, root-owned, on the
    live box).
    """
    import os

    try:
        from prax.services import workspace_service

        return "/workspace/" + os.path.basename(workspace_service.workspace_root(uid))
    except Exception:
        return f"/workspace/{uid}"


def _append_delivery_hint(result: str, uid: str) -> str:
    """Deterministically flag deliverable artifacts on the delegate result.

    When the spoke's report mentions container paths that exist inside this
    user's workspace, append a system note telling the orchestrator exactly
    which ``workspace_send_file`` call delivers them. Observed live: a spoke
    produced a valid MP3 and the orchestrator then spent two turns answering
    "link please" with a bare filename in backticks — the affordance exists
    but a low-tier model does not reach for it. The hint is computed by
    code (paths verified on disk), so it cannot assert a deliverable that
    is not there.

    Unconditional: the hint is computed from the filesystem, appended only when
    a reported path really exists under this user's workspace, and says nothing
    when it doesn't — so there is no behaviour to gate, only a fact to report.
    """
    import os
    import re

    try:
        from prax.services import workspace_service

        root = workspace_service.workspace_root(uid)
    except Exception:
        return result
    user_dir = os.path.basename(root)
    deliverable: list[str] = []
    for path in re.findall(r"/workspace/[^\s`'\"]+", result or ""):
        rel = path.removeprefix(f"/workspace/{user_dir}/")
        if rel == path:  # not under this user's directory
            continue
        try:
            host = workspace_service.safe_join(root, rel)
        except Exception:
            continue
        if os.path.isfile(host) and rel not in deliverable:
            deliverable.append(rel)
    if not deliverable:
        return result
    calls = ", ".join(f"workspace_send_file('{r}')" for r in deliverable[:5])
    return (
        f"{result}\n\n"
        f"[SYSTEM: verified deliverable artifact(s) in the user's workspace — "
        f"if the user should receive them, send with: {calls}. A file path or "
        f"name in a chat message is NOT a delivery.]"
    )

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
   `tee {user_workspace}/active/foo.py <<'EOF' ... EOF`).
2. **Run** it with sandbox_shell and read the output.
3. **Iterate** — fix errors and re-run until it works.
4. **Deliver** any artifact the user should receive under
   {user_workspace}/active/ — this user's OWN directory in the shared mount.
   Do NOT write user artifacts to /workspace/active/ (that path belongs to no
   user and the app cannot deliver files from it). Report the full path.
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
        prompt = SYSTEM_PROMPT.format(
            agent_name=settings.agent_name,
            user_workspace=_container_user_workspace(uid),
        )
        result = run_spoke(
            task=task,
            system_prompt=prompt,
            tools=build_tools(),
            config_key="subagent_sandbox",
            role_name="Sandbox Agent",
            channel="engineering",
            recursion_limit=80,
        )
        return _append_delivery_hint(result, uid)
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
