"""LangChain tool wrappers for sandbox code execution."""
from __future__ import annotations

import logging
import re

from langchain_core.tools import tool

from prax.agent.user_context import current_user_id
from prax.services.sandbox_bridge import configured_client as get_client

logger = logging.getLogger(__name__)


def _get_user_id() -> str:
    uid = current_user_id.get()
    if not uid:
        return "unknown"
    return uid


@tool
def sandbox_shell(command: str, timeout: int = 60) -> str:
    """Run a shell command in the sandbox container.

    WHERE IT SHOWS depends on what the user is looking at:
    - TeamWork's Terminal tab: it runs in their visible terminal, and they
      see the command and its output live.
    - Anywhere else, the Desktop tab included: it runs in the BACKGROUND.
      The output comes back to you only and appears in no window the user
      can see. To type into a terminal on the desktop, use desktop_type
      (window="terminal") or delegate_desktop. Never tell the user they can
      see output from a background run.
    Use it for any shell command: ls, df, git, pytest, pip, apt, curl, etc.

    This is how Prax runs code directly — there is no separate coding-agent
    session to delegate to. Just call this tool with the command.

    FILES FOR THE USER: the container's /tmp is internal — the user can NEVER
    receive a file from there, and "sandbox:/tmp/..." links do not work.
    /workspace is the sandbox's view of the workspace mount. The current
    user's directory there is named in your instructions — /workspace/<their
    dir> when every workspace is mounted, /workspace itself when only theirs
    is. Write any artifact the user should get under that directory's
    active/ — anywhere else under /workspace cannot be delivered from — then
    deliver it with workspace_send_file('active/<filename>').

    BOUND YOUR OUTPUT: the container's disk IS the host disk. Never run
    generators without a size/duration limit (e.g. ffmpeg with a lavfi
    source needs -t; a runaway command once filled the entire disk with a
    21 GB file and took the whole system down).

    Args:
        command: The shell command to run.
        timeout: Max seconds to wait (default 60).
    """
    from prax.settings import settings
    if not settings.sandbox_available:
        return "Sandbox is disabled (SANDBOX_ENABLED=false); no shell is available."

    from prax.agent.user_context import current_active_view

    active_view = current_active_view.get()
    logger.info("sandbox_shell: active_view=%r, command=%r", active_view, command[:80])

    # If user is watching the terminal, run through the shared PTY
    if active_view == "terminal":
        try:
            from prax.services.teamwork_service import get_teamwork_client
            tw = get_teamwork_client()
            logger.info("sandbox_shell: routing through shared terminal (project=%s)", tw._project_id)
            result = tw.terminal_exec(command, timeout=float(timeout))
            if result is not None:
                output = result.get("output", "")
                logger.info("sandbox_shell: terminal_exec returned %d chars", len(output))
                return output if output else "(command produced no output)"
            logger.warning("sandbox_shell: terminal_exec returned None (no active session?)")
        except Exception:
            logger.exception("sandbox_shell: terminal_exec failed, falling through to docker exec")

    # Default: run via docker exec and return structured output
    result = get_client().run_shell(command, timeout=timeout)
    if "error" in result:
        return f"Shell error: {result['error']}"
    parts = []
    if active_view == "desktop":
        # The user is watching the desktop, where this run is invisible. Say
        # so in the result itself, where the model reads it: on 2026-10-04
        # Prax ran `echo` this way and told the user "you should see that
        # message pop up in your terminal right now".
        parts.append(
            "[Ran in the background: the user did NOT see this. To show it in "
            "their terminal, type it with desktop_type(window=\"terminal\").]"
        )
    if result.get("stdout"):
        parts.append(result["stdout"])
    if result.get("stderr"):
        parts.append(f"STDERR:\n{result['stderr']}")
    parts.append(f"(exit code: {result['exit_code']})")
    return "\n".join(parts)


@tool
def terminal_history(lines: int = 200) -> str:
    """Read the recent scrollback from the user's persistent terminal.

    The TeamWork terminal panel runs a persistent shell that survives
    tab navigation and WebSocket reconnects.  Output is buffered server-
    side, so this tool returns whatever has scrolled past in the panel,
    even when no browser is currently attached.  The user's commands and
    your `sandbox_shell` calls share one continuous history.

    Use it before answering questions like "what just happened in the
    terminal?", "what was the last command?", or "did that build
    succeed?" — instead of re-running things or asking the user to paste
    output.  ANSI escape codes are stripped server-side for clean reading.

    Args:
        lines: How many lines back to capture (default 200).
    """
    import requests
    try:
        from prax.services.teamwork_service import get_teamwork_client
        tw = get_teamwork_client()
        if not tw.enabled or not tw.project_id:
            return "(TeamWork not available — terminal_history needs the TeamWork web UI to be configured)"
        # /api/terminal/{project_id}/recent is the public router (not the
        # /api/external/* TeamWork-agent API), so call it directly.
        resp = requests.get(
            f"{tw.base_url}/api/terminal/{tw.project_id}/recent",
            params={"lines": int(lines)},
            timeout=5.0,
        )
    except Exception as e:
        return f"Could not read terminal history: {e}"
    if resp.status_code == 404:
        return "(no active terminal session — open the Terminal tab in TeamWork to start one)"
    if resp.status_code != 200:
        return f"Could not read terminal history: HTTP {resp.status_code}"
    output = (resp.json() or {}).get("output", "")
    return output or "(terminal buffer is empty)"


@tool
def sandbox_install(package_name: str) -> str:
    """Install a system package (apt-get) in the persistent sandbox.

    Use this when a task requires a package not pre-installed in the sandbox.
    Pre-installed: python3, texlive (full), ffmpeg, poppler-utils, pandoc, git, curl, wget, jq.

    In Docker deployment, packages are installed automatically. In local mode,
    returns instructions for the user to install manually.

    Note: Packages installed this way persist until the sandbox container restarts.
    For permanent additions, ask the user to update the sandbox Dockerfile.
    """
    result = get_client().install_package(package_name)
    if "error" in result:
        hints = result.get("local_install_hints")
        if hints:
            lines = [f"Cannot auto-install in local mode. The user needs to install '{package_name}':"]
            for os_name, cmd in hints.items():
                lines.append(f"  {os_name}: {cmd}")
            return "\n".join(lines)
        return f"Failed to install '{package_name}': {result['error']}"
    return f"Successfully installed '{package_name}' in the sandbox."


@tool
def sandbox_rebuild(dockerfile_content: str | None = None) -> str:
    """Rebuild the sandbox Docker image and restart the container.

    Use this to permanently add system packages to the sandbox. If you provide
    dockerfile_content, it will overwrite sandbox/Dockerfile before building.
    Read the current Dockerfile first with source_read('sandbox/Dockerfile'),
    add your changes, then pass the full content here.

    Only works in Docker deployment mode. The rebuild takes a few minutes.
    All active sandbox sessions should be finished first.
    """
    result = get_client().rebuild_sandbox(dockerfile_content)
    if "error" in result:
        return f"Sandbox rebuild failed: {result['error']}"
    return f"Sandbox rebuilt and restarted successfully (image: {result['image']})."


@tool
def sandbox_restart(reason: str) -> str:
    """Restart the sandbox container when the desktop, a terminal or the
    browser is stuck (frozen screen, commands that never return, a runaway
    process).

    Files, installed packages and the browser profile stay. Everything running
    in the sandbox stops, including the user's open terminals and desktop
    apps, so tell the user first and why. The user can also restart it from
    TeamWork's Desktop tab.

    Args:
        reason: Why, in a few words. Repeated back in the result.
    """
    from prax.settings import settings
    if not settings.sandbox_available:
        return "The sandbox is off (SANDBOX_ENABLED=false); there is nothing to restart."
    restart = getattr(get_client(), "restart_sandbox", None)
    if restart is None:
        return "This prax-sandbox client can't restart the sandbox; update prax-sandbox."
    try:
        result = restart()
    except Exception as e:
        return f"Restart failed: {e}"
    if "error" in result:
        return f"Restart failed: {result['error']}"
    state = ("and it is ready" if result.get("ready")
             else "but it is not answering yet; check again in a minute")
    return (f"Sandbox restarted ({reason}) {state}. Programs that were running in it "
            "(terminals, desktop apps) stopped; files and installed packages are still there.")


# ---------------------------------------------------------------------------
# Desktop interaction — the sandbox's Linux desktop (TeamWork's Desktop tab)
# ---------------------------------------------------------------------------
#
# Structured first, pixels last — the order ChatGPT's and Claude's computer use
# docs themselves recommend:
#   1. desktop_list_windows — what is open, which window has focus, and where.
#   2. desktop_type / desktop_key aimed at a window ("terminal", a title, an
#      id): it is brought to the front and the keys go to it.
#   3. desktop_screenshot — the vision model reads the screen, for GUI state
#      and terminal output. The fallback, not the first move.
#
# Every command runs INSIDE the sandbox container through the sandbox client,
# whatever the deployment shape. They used to go through run_command, which
# reaches the container only in a compose deploy (or with
# SANDBOX_ROUTE_COMMANDS): on a host install they ran on the Prax host, where
# there is no desktop at all, and desktop_open ran a model-written command with
# `bash -c` on the host.

_DISPLAY = ":99"
# WM_CLASS names of terminal emulators, so window="terminal" finds the user's.
_TERMINAL_CLASSES = frozenset({
    "xterm", "uxterm", "xfce4-terminal", "gnome-terminal-server", "konsole",
    "kitty", "alacritty", "urxvt", "rxvt", "st", "terminator", "tilix", "wezterm",
})
# Desktop furniture, never a target for "type into a window".
_SHELL_CLASSES = frozenset({"xfce4-panel", "xfdesktop", "xfwm4", "wrapper-2.0"})
_XDOTOOL_KEY = re.compile(r"^[A-Za-z0-9_+\-]+$")


class DesktopUnavailable(RuntimeError):
    """There is no sandbox desktop to act on."""


def _desktop_run(argv: list[str], timeout: int = 15):
    """Run *argv* on the sandbox desktop, inside the container."""
    from prax.settings import settings
    if not settings.sandbox_available:
        raise DesktopUnavailable("the sandbox is off, so there is no desktop")
    return get_client().run_command(["env", f"DISPLAY={_DISPLAY}", *argv], timeout=timeout)


# One line per window: id, class, pid, active, x, y, width, height, title.
# A fixed script: nothing the model writes is interpolated into it.
_LIST_WINDOWS = r"""
active=$(xdotool getactivewindow 2>/dev/null || echo 0)
for w in $(xdotool search --onlyvisible --name '' 2>/dev/null); do
  title=$(xdotool getwindowname "$w" 2>/dev/null) || continue
  [ -n "$title" ] || continue
  cls=$(xprop -id "$w" WM_CLASS 2>/dev/null | sed -n 's/.*", "\(.*\)"$/\1/p')
  pid=$(xprop -id "$w" _NET_WM_PID 2>/dev/null | sed -n 's/.* = //p')
  eval "$(xdotool getwindowgeometry --shell "$w" 2>/dev/null)"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$w" "$cls" "${pid:-}" \
    "$([ "$w" = "$active" ] && echo 1 || echo 0)" "$X" "$Y" "$WIDTH" "$HEIGHT" "$title"
done
"""


def _list_windows() -> list[dict]:
    result = _desktop_run(["bash", "-c", _LIST_WINDOWS])
    windows, seen = [], set()
    for line in (result.stdout or "").splitlines():
        parts = line.split("\t", 8)
        if len(parts) != 9 or parts[0] in seen:
            continue
        seen.add(parts[0])
        wid, cls, pid, active, x, y, w, h, title = parts
        if cls.lower() in _SHELL_CLASSES:
            continue
        windows.append({
            "id": int(wid), "cls": cls, "pid": pid, "active": active == "1",
            "x": x, "y": y, "w": w, "h": h, "title": title,
        })
    return windows


def _is_terminal(win: dict) -> bool:
    return win["cls"].lower() in _TERMINAL_CLASSES


def _resolve_window(spec: str, windows: list[dict]) -> tuple[dict | None, str]:
    """The window *spec* names — an id (decimal or 0x hex), "terminal", or part
    of a title or class — preferring the one that has focus. Returns
    (window, note) or (None, why not)."""
    spec = spec.strip()
    if spec.lower().startswith("0x") or spec.isdigit():
        try:
            want = int(spec, 0)
        except ValueError:
            want = -1
        matches = [w for w in windows if w["id"] == want]
    elif spec.lower() in {"terminal", "the terminal", "term", "shell"}:
        matches = [w for w in windows if _is_terminal(w)]
    else:
        low = spec.lower()
        matches = [w for w in windows if low in w["title"].lower() or low == w["cls"].lower()]
    if not matches:
        open_list = "; ".join(f"{w['cls']} '{w['title']}'" for w in windows) or "none"
        return None, f"No window matches {spec!r}. Open windows: {open_list}."
    chosen = next((w for w in matches if w["active"]), matches[0])
    note = f" ({len(matches)} matched; used the {'focused' if chosen['active'] else 'first'} one)" \
        if len(matches) > 1 else ""
    return chosen, note


def _focus(win: dict) -> None:
    _desktop_run(["xdotool", "windowactivate", "--sync", str(win["id"])], timeout=10)


def _describe(win: dict) -> str:
    return f"{win['cls'] or 'window'} '{win['title']}' (0x{win['id']:x})"


@tool
def desktop_list_windows() -> str:
    """List the windows open on the sandbox desktop: id, application, title,
    position and size, and which one has keyboard focus (★).

    Start here for any desktop task — it is cheap and exact. To act on a
    window, pass its id, "terminal", or part of its title to desktop_type /
    desktop_key.
    """
    try:
        windows = _list_windows()
    except DesktopUnavailable as e:
        return f"No desktop: {e}."
    except Exception as e:
        return f"Window list failed: {e}"
    if not windows:
        return "No application windows are open on the desktop."
    lines = [
        f"{'★' if w['active'] else ' '} 0x{w['id']:x}  {w['cls'] or '?':<16} "
        f"{w['x']},{w['y']} {w['w']}x{w['h']}  {w['title']}"
        for w in windows
    ]
    return "Windows on the desktop (★ = has keyboard focus):\n" + "\n".join(lines)


@tool
def desktop_type(text: str, window: str = "", press_enter: bool = False) -> str:
    """Type text into a window on the sandbox desktop, as if on the keyboard.

    This is how to type into the user's terminal on the desktop: use
    window="terminal" (or the window's id or part of its title) and
    press_enter=True to run a command. The user sees it happen live in the
    Desktop tab. To read what the command printed, use desktop_screenshot.

    Args:
        text: The text to type. For shortcuts and special keys use desktop_key.
        window: Which window gets the keys — "terminal", an id from
            desktop_list_windows, or part of a title. Empty = whatever has focus.
        press_enter: Press Enter after typing (to run a command).
    """
    try:
        target, note = None, ""
        if window:
            target, note = _resolve_window(window, _list_windows())
            if target is None:
                return note
            _focus(target)
        argv = ["xdotool", "type", "--clearmodifiers", "--delay", "12", "--", text]
        result = _desktop_run(argv, timeout=max(15, len(text) // 20 + 10))
        if result.returncode != 0:
            return f"Type failed: {result.stderr or 'unknown error'}"
        if press_enter:
            _desktop_run(["xdotool", "key", "--clearmodifiers", "Return"])
    except DesktopUnavailable as e:
        return f"No desktop: {e}."
    except Exception as e:
        return f"Type failed: {e}"
    where = f"into {_describe(target)}{note}" if target else "into the focused window"
    return f"Typed {len(text)} characters {where}{' and pressed Enter' if press_enter else ''}."


@tool
def desktop_key(keys: str, window: str = "") -> str:
    """Press keys or shortcuts on the sandbox desktop (xdotool key names).

    Args:
        keys: One or more key combinations separated by spaces, e.g. "Return",
            "ctrl+c", "ctrl+shift+t", "alt+F4", "Tab Tab Return".
        window: Which window gets them — "terminal", an id, or part of a title.
            Empty = whatever has focus.
    """
    combos = keys.split()
    if not combos or not all(_XDOTOOL_KEY.match(k) for k in combos):
        return f"Not key names: {keys!r} (use xdotool names like Return, ctrl+c, alt+F4)."
    try:
        target, note = None, ""
        if window:
            target, note = _resolve_window(window, _list_windows())
            if target is None:
                return note
            _focus(target)
        result = _desktop_run(["xdotool", "key", "--clearmodifiers", *combos])
        if result.returncode != 0:
            return f"Key press failed: {result.stderr or 'unknown error'}"
    except DesktopUnavailable as e:
        return f"No desktop: {e}."
    except Exception as e:
        return f"Key press failed: {e}"
    where = f" in {_describe(target)}{note}" if target else ""
    return f"Pressed {keys}{where}."


@tool
def desktop_click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    """Click at screen coordinates on the sandbox desktop.

    Get coordinates from desktop_screenshot (it reports them in screen
    pixels) or from a window's position in desktop_list_windows.

    Args:
        x: Pixels from the left edge of the screen.
        y: Pixels from the top.
        button: "left", "right" or "middle".
        clicks: 1 for a single click, 2 for a double click.
    """
    btn = {"left": "1", "middle": "2", "right": "3"}.get(button, "1")
    argv = ["xdotool", "mousemove", str(int(x)), str(int(y)), "click"]
    if clicks > 1:
        argv += ["--repeat", str(min(int(clicks), 3))]
    argv.append(btn)
    try:
        result = _desktop_run(argv)
        if result.returncode != 0:
            return f"Click failed: {result.stderr or 'unknown error'}"
    except DesktopUnavailable as e:
        return f"No desktop: {e}."
    except Exception as e:
        return f"Click failed: {e}"
    return f"Clicked ({button}, {clicks}x) at ({x}, {y})."


@tool
def desktop_open(command: str) -> str:
    """Launch an application on the sandbox desktop, in the background.

    Examples: "xterm", "thunar /workspace", "mousepad notes.txt".

    Args:
        command: The command line that starts the application.
    """
    # Inside the container only; the command is an argument, not spliced in.
    script = 'setsid bash -c "$1" >/dev/null 2>&1 </dev/null & echo $!'
    try:
        result = _desktop_run(["bash", "-c", script, "desktop_open", command], timeout=10)
        if result.returncode != 0:
            return f"Launch failed: {result.stderr or 'unknown error'}"
    except DesktopUnavailable as e:
        return f"No desktop: {e}."
    except Exception as e:
        return f"Launch failed: {e}"
    return f"Launched: {command} (PID {(result.stdout or '').strip()}). Check it with desktop_list_windows."


_SCREEN_PROMPT = """\
This is a screenshot of a Linux desktop, {width}x{height} pixels. Coordinates
are in this image's pixels, which are the screen's pixels.
{question}
Report:
1. The windows you can see, and which one is in front.
2. The text in the front window. For a terminal, copy its last lines verbatim.
3. For anything the question asks about, its centre point as (x, y).
Be exact. If you cannot read or locate something, say so instead of guessing.
"""


@tool
def desktop_screenshot(question: str = "") -> str:
    """Look at the sandbox desktop: the vision model describes what is on the
    screen, copies the text of the front window (a terminal's output), and
    gives screen coordinates for whatever *question* asks about.

    Slower and costlier than desktop_list_windows — use it to read output or
    see a GUI's state, and to confirm an action worked before telling the user
    they can see it.

    Args:
        question: What you need to know, e.g. "what did the last command print
            in the terminal?" or "where is the Save button?".
    """
    from prax.settings import settings
    if not settings.desktop_screenshots_enabled:
        return ("Screenshots are turned off (Settings → Prax: \"Prax can look at the "
                "desktop\"), so I can't see the screen. desktop_list_windows still "
                "works; ask the user to turn screenshots on if you need to read it.")
    # JPEG keeps a 1920x1080 screen around 150-250 KB; base64 on stdout, so
    # nothing is written outside the container.
    script = (
        "f=$(mktemp --suffix=.png) && scrot -o \"$f\" && "
        "convert \"$f\" -quality 70 jpg:- | base64 -w0; "
        "echo; convert \"$f\" -format '%w %h' info:; rm -f \"$f\""
    )
    try:
        result = _desktop_run(["bash", "-c", script], timeout=20)
        lines = (result.stdout or "").strip().splitlines()
        if result.returncode != 0 or len(lines) < 2:
            return f"Screenshot failed: {result.stderr or 'no image'}"
        b64, size = lines[0], lines[-1].split()
        width, height = (size + ["?", "?"])[:2]
    except DesktopUnavailable as e:
        return f"No desktop: {e}."
    except Exception as e:
        return f"Screenshot failed: {e}"
    from prax.agent.vision_tools import analyze_image_impl
    prompt = _SCREEN_PROMPT.format(
        width=width, height=height,
        question=f"Question: {question}" if question else "Describe the screen.",
    )
    try:
        seen = analyze_image_impl(f"data:image/jpeg;base64,{b64}", prompt)
    except Exception as e:
        return f"Screenshot taken, but the vision model could not read it: {e}"
    return f"Desktop ({width}x{height}):\n{seen}"


# ---------------------------------------------------------------------------
# File viewer — windowed read with line numbers
# ---------------------------------------------------------------------------
#
# SWE-agent ACI design: reading a full file with `cat` floods context and
# loses position. Return exactly one window at a time (100 lines is the
# validated Goldilocks number), with line numbers prepended so the agent
# can target edits without counting. Last-viewed position is remembered
# per (user, path) so `sandbox_scroll` is a one-liner.

_VIEW_WINDOW = 100
_VIEW_MAX_WINDOW = 300
_VIEW_STATE: dict[tuple[str, str], int] = {}


def _view_lines(path: str, start: int, count: int) -> dict:
    """Return line-numbered slice + file metadata via a single shell round-trip."""
    start = max(1, start)
    end = start + count - 1
    # awk: print NR-prefixed lines in range; then END block prints total line count.
    cmd = (
        f"awk -v s={start} -v e={end} "
        f"'NR>=s && NR<=e {{printf \"%6d  %s\\n\", NR, $0}} "
        f"END {{print \"---TOTAL:\" NR}}' "
        f"{_shell_quote(path)}"
    )
    result = get_client().run_shell(cmd, timeout=15)
    return result


def _shell_quote(arg: str) -> str:
    return "'" + arg.replace("'", "'\\''") + "'"


def _parse_view_output(raw: str) -> tuple[str, int]:
    """Split the tail `---TOTAL:N` marker off the rendered output."""
    total = 0
    lines = raw.splitlines()
    body_lines = []
    for line in lines:
        if line.startswith("---TOTAL:"):
            try:
                total = int(line.split(":", 1)[1].strip())
            except ValueError:
                total = 0
        else:
            body_lines.append(line)
    return "\n".join(body_lines), total


@tool
def sandbox_view(path: str, start_line: int = 1, window: int = 100) -> str:
    """View a file in the sandbox as a line-numbered window.

    Prefer this over `cat` — `cat` floods context and loses your position.
    This shows exactly `window` lines (default 100, max 300) starting at
    `start_line`, with line numbers prepended so you can target edits
    precisely. The last-viewed position is remembered per path, so
    sandbox_scroll(path) picks up where you left off.

    Args:
        path: Absolute path inside the sandbox (e.g. /workspace/app.py).
        start_line: First line to show (1-indexed). Default 1.
        window: Number of lines in the view (default 100, capped at 300).
    """
    window = max(10, min(window, _VIEW_MAX_WINDOW))
    start_line = max(1, start_line)
    result = _view_lines(path, start_line, window)
    if "error" in result:
        return f"Sandbox error reading {path}: {result['error']}"
    if result.get("exit_code", 0) != 0:
        stderr = (result.get("stderr") or "").strip()
        exit_code = result["exit_code"]
        return f"Failed to read {path}: {stderr or f'unknown error (exit {exit_code})'}"
    body, total = _parse_view_output(result.get("stdout") or "")
    if total == 0 and not body:
        return f"{path} is empty or does not exist."
    end_line = min(start_line + window - 1, total)
    _VIEW_STATE[(_get_user_id(), path)] = end_line
    header = (
        f"{path} — lines {start_line}-{end_line} of {total} "
        f"({'end of file' if end_line >= total else 'sandbox_scroll to continue'})"
    )
    return f"{header}\n{body}"


@tool
def sandbox_scroll(path: str, direction: str = "down", window: int = 100) -> str:
    """Scroll the sandbox_view one window up or down in a file.

    Uses the last-viewed position for `path`. If you haven't viewed the
    file yet, starts at line 1 (down) or reports it.

    Args:
        path: Absolute path inside the sandbox.
        direction: "down" (default) or "up".
        window: Lines per window (default 100, capped at 300).
    """
    window = max(10, min(window, _VIEW_MAX_WINDOW))
    last_end = _VIEW_STATE.get((_get_user_id(), path), 0)
    if direction == "up":
        start = max(1, last_end - window - window + 1)
    else:
        start = max(1, last_end + 1)
    return sandbox_view.invoke({"path": path, "start_line": start, "window": window})


@tool
def sandbox_goto(path: str, line: int, window: int = 100) -> str:
    """Jump the sandbox_view to a specific line in a file.

    Centers the window around `line` so you can see surrounding context.

    Args:
        path: Absolute path inside the sandbox.
        line: Line number to center the view on (1-indexed).
        window: Lines per window (default 100, capped at 300).
    """
    window = max(10, min(window, _VIEW_MAX_WINDOW))
    start = max(1, line - window // 2)
    return sandbox_view.invoke({"path": path, "start_line": start, "window": window})


def build_sandbox_tools() -> list:
    from prax.settings import settings
    if not settings.sandbox_available:
        return []
    # Pure-execution tools — direct docker-exec / browser / desktop. These need
    # NO coding-agent server and are always available with the sandbox.
    tools = [
        sandbox_shell, terminal_history,
        sandbox_install, sandbox_rebuild, sandbox_restart,
        sandbox_view, sandbox_scroll, sandbox_goto,
        desktop_screenshot, desktop_click, desktop_type, desktop_key,
        desktop_list_windows, desktop_open,
    ]
    # The OpenCode coding-SESSION tools were removed: the sandbox image no longer
    # ships a coding-agent server and the control-plane/client dropped the session
    # API (prax-sandbox #4/#5). Prax now codes DIRECTLY — run_python /
    # workspace_save|patch / source_read|grep / sandbox_shell — no separate agent.
    # Lean 4 proof-check tool (opt-in, LEAN_TOOLS_ENABLED) — needs the Lean
    # toolchain in the sandbox image. See docs/research/cdc-lean-teach-prax-lean.md.
    from prax.agent.lean_tools import build_lean_tools
    tools.extend(build_lean_tools())
    # data_query — DuckDB SQL / number-crunching (opt-in, DATA_TOOLS_ENABLED);
    # needs duckdb + pandas in the sandbox image. See prax/agent/data_tools.py.
    from prax.agent.data_tools import build_data_tools
    tools.extend(build_data_tools())
    return tools
