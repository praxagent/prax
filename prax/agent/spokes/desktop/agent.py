"""Desktop spoke agent — GUI interaction via a computer-use loop.

Prax delegates desktop tasks here instead of exposing raw desktop_* tools
in the orchestrator.  The desktop agent follows a screenshot-analyse-act-verify
loop to interact with GUI applications on the sandbox Linux desktop.

For web browsing, the orchestrator uses delegate_browser instead.
"""
from __future__ import annotations

import logging
import threading

from langchain_core.tools import tool

from prax.agent.spokes._runner import run_spoke
from prax.settings import settings

logger = logging.getLogger(__name__)

# Dedup identical parallel desktop delegations (same pattern as sandbox/browser).
_active_tasks: dict[str, str] = {}
_active_tasks_lock = threading.Lock()

# ---------------------------------------------------------------------------
# System prompt — the desktop agent's role and computer-use loop
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are the Desktop Agent for {agent_name}.  You operate the sandbox's Linux
desktop (DISPLAY :99). The user watches it live in TeamWork's Desktop tab, so
everything you type and click happens in front of them.

## How to work: structured first, pixels last
1. **desktop_list_windows** — start here. Cheap and exact: which windows are
   open, which one has keyboard focus (★), where they are.
2. **Act on windows by name** — ``desktop_type(text, window=..., press_enter=...)``
   and ``desktop_key(keys, window=...)``. For the user's terminal use
   ``window="terminal"``; otherwise a window id or part of its title. The
   window is brought to the front first.
3. **desktop_screenshot(question)** — when you need to SEE: read what a command
   printed in a terminal, find a button, check a dialog. The vision model
   answers your question and gives screen coordinates. Ask something specific.
4. **desktop_click(x, y)** — with coordinates from desktop_screenshot.
5. **Verify, then report.** After acting, confirm with desktop_screenshot (or
   desktop_list_windows) that it worked. Report only what you confirmed; never
   say something is on screen because you assume it is.

## Other tools
- **desktop_open** — launch an application (``xterm``, ``thunar /workspace``).
  Then desktop_list_windows to find its window.
- **sandbox_shell** — runs in the BACKGROUND: the user sees none of it. Use it
  for setup the user doesn't need to watch (installing, checking files), never
  for something they asked to see happen in a window.

## Installed Software

- **Chromium** — already running on the desktop with CDP on port 9222.
  Do NOT launch another Chrome.  To open a URL on the desktop, use
  ``sandbox_shell("chromium-browser --app=http://... &")``.
- **code-server** — web-based VS Code on port 8443. NOT started by default (saves RAM).
  To launch: ``sandbox_shell("code-server --bind-addr 0.0.0.0:8443 --auth none --disable-telemetry /workspace &")``
  Then open in desktop Chrome: ``sandbox_shell("chromium-browser http://localhost:8443 &")``
- **xterm** — terminal emulator.  Launch with ``desktop_open("xterm")``.
- **XFCE4** — desktop environment with taskbar, file manager, and app menu.

## Desktop Configuration Tips

- **Setting wallpaper:** Do NOT use ``feh`` or ``xfconf-query`` (no dbus session).
  Edit the XML config directly and restart xfdesktop:
  ```
  sandbox_shell("sed -i 's|value=\"[^\"]*\"|value=\"/path/to/image.png\"|' /root/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-desktop.xml && killall xfdesktop; sleep 0.5; xfdesktop &")
  ```
  The ``killall`` + restart is required — xfdesktop only reads the config on startup.

## Guidelines
- **One action, then check.** Don't chain several clicks or keystrokes blind.
- **Prefer keys and window names over coordinates.** Clicking by pixel is the
  last resort, for GUIs with no keyboard path.
- **Use sandbox_shell** for work that is easier via CLI and needn't be seen.
- **Do NOT launch chromium-browser directly** — it is already running.
  Use sandbox_shell to open URLs in the existing instance.
- **Report clearly** what you did and what you confirmed on screen.
"""


# ---------------------------------------------------------------------------
# Tool assembly — curated set for desktop work
# ---------------------------------------------------------------------------

def build_tools() -> list:
    """Return all tools available to the desktop spoke agent."""
    from prax.agent.sandbox_tools import (
        desktop_click,
        desktop_key,
        desktop_list_windows,
        desktop_open,
        desktop_screenshot,
        desktop_type,
        sandbox_shell,
    )

    return [
        desktop_screenshot,
        desktop_click,
        desktop_type,
        desktop_key,
        desktop_list_windows,
        desktop_open,
        sandbox_shell,
    ]


# ---------------------------------------------------------------------------
# Delegation function — this is what the orchestrator calls
# ---------------------------------------------------------------------------

@tool
def delegate_desktop(task: str) -> str:
    """Delegate a desktop GUI task to the Desktop Agent.

    The Desktop Agent interacts with GUI applications on the sandbox Linux
    desktop using a computer-use loop (screenshot, analyse, act, verify).
    It can launch apps, click buttons, type text, press keyboard shortcuts,
    and take screenshots.

    Use this for:
    - "Open VS Code and create a new file"
    - "Launch the file manager and navigate to /workspace"
    - "Take a screenshot of the desktop"
    - "Open a terminal and run this command" (for visual terminal interaction)
    - "Click the Save button in the open application"
    - "Interact with a GUI application"

    Do NOT use this for web browsing — use delegate_browser instead.
    Do NOT use this for simple shell commands — the orchestrator can call
    sandbox_shell directly.

    Args:
        task: A clear, self-contained description of what to do on the desktop.
              Include application names, file paths, and any context the agent
              needs — it cannot see your conversation history.
    """
    from prax.agent.user_context import current_user_id
    uid = current_user_id.get() or "unknown"

    normalised = task.strip().lower()[:200]
    with _active_tasks_lock:
        existing = _active_tasks.get(uid)
        if existing == normalised:
            logger.info("Duplicate delegate_desktop call for user %s — same task, skipping", uid)
            return (
                "An identical desktop delegation is already running. "
                "Wait for it to complete — no need to call this twice."
            )
        _active_tasks[uid] = normalised

    try:
        prompt = SYSTEM_PROMPT.format(agent_name=settings.agent_name)
        return run_spoke(
            task=task,
            system_prompt=prompt,
            tools=build_tools(),
            config_key="subagent_desktop",
            role_name="Desktop Agent",
            recursion_limit=80,
        )
    finally:
        with _active_tasks_lock:
            _active_tasks.pop(uid, None)


# ---------------------------------------------------------------------------
# Registration — the orchestrator imports this
# ---------------------------------------------------------------------------

def build_spoke_tools() -> list:
    """Return the delegation tool for the main agent."""
    return [delegate_desktop]
