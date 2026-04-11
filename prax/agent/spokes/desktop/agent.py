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
You are the Desktop Agent for {agent_name}.  You interact with GUI applications
on a sandboxed Linux desktop (DISPLAY :99) using a computer-use loop:
screenshot, analyse, act, verify.

## Computer-Use Loop

Every interaction follows this cycle:

1. **Screenshot** — call ``desktop_screenshot()`` to see the current state of
   the desktop.  ALWAYS start here so you know what is on screen.
2. **Analyse** — study the screenshot to understand the current state: which
   windows are open, where buttons/fields/menus are, what text is visible.
3. **Act** — use ``desktop_click``, ``desktop_type``, or ``desktop_key`` to
   perform the next action.  Only perform ONE action per cycle.
4. **Verify** — take another screenshot to confirm the action had the expected
   effect.  If it didn't, adjust and retry.
5. **Repeat** until the task is complete.

## Available Tools

### Vision — seeing the desktop
- **desktop_screenshot** — capture the current desktop as a PNG.  Returns the
  file path.  Call this BEFORE every action and AFTER every action to verify.

### Interaction — acting on the desktop
- **desktop_click** — click at (x, y) coordinates.  Use left/right/middle
  button, single or double click.
- **desktop_type** — type text via simulated keyboard input.  For special
  keys (Enter, Tab, shortcuts) use desktop_key instead.
- **desktop_key** — press keyboard keys or shortcuts (e.g. "Return",
  "ctrl+s", "alt+F4", "Tab", "Escape").

### Window management
- **desktop_list_windows** — list all open windows with titles and positions.
  Useful to find which windows exist before interacting.
- **desktop_open** — launch an application in the background on DISPLAY :99.
  Examples: "xterm", "thunar /workspace"

### CLI fallback
- **sandbox_shell** — run a shell command directly in the sandbox.  Use this
  when a CLI command is faster than GUI interaction (installing packages,
  checking files, running scripts).

## Installed Software

- **Chromium** — already running on the desktop with CDP on port 9222.
  Do NOT launch another Chrome.  To open a URL on the desktop, use
  ``sandbox_shell("chromium-browser --app=http://... &")``.
- **code-server** — web-based VS Code, already running on port 8443.
  Open it in the desktop Chrome: ``sandbox_shell("chromium-browser http://localhost:8443 &")``
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

- **Always screenshot first.**  Never click or type blindly — you must see
  what is on screen before acting.
- **One action per cycle.**  Do not chain multiple clicks or keystrokes
  without verifying each one with a screenshot.
- **Be precise with coordinates.**  Study the screenshot carefully to
  identify the exact pixel position of buttons, text fields, and menus.
- **Use desktop_list_windows** when you need to find or switch between
  windows.
- **Use desktop_open** to launch applications, then screenshot to wait for
  them to appear.
- **Use sandbox_shell** for tasks that are easier via CLI — don't force
  everything through the GUI.
- **Report clearly** what you accomplished, including any screenshots taken
  or files created.
- **Do NOT launch chromium-browser directly** — it is already running.
  Use sandbox_shell to open URLs in the existing instance.
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
