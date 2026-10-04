"""Prax on the sandbox desktop (prax/agent/sandbox_tools.py, desktop section).

Live regression (2026-10-04): in TeamWork's Desktop tab the user asked Prax to
type into the terminal they had open. Prax ran `echo` with sandbox_shell, which
runs in the background there, and said "you should see that message pop up in
your terminal right now". Nothing appeared. Underneath: the desktop tools ran
on the Prax HOST on a native deploy (no desktop there), the screenshot tool
returned a path the model never saw, and nothing told Prax which view the user
was in.
"""
from __future__ import annotations

import subprocess

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from prax.agent import sandbox_tools as st

WINDOWS = "\n".join([
    "6291506\tXfwm4\t38\t0\t-1000\t-1000\t5\t5\tXfwm4",
    "4194348\tXfdesktop\t40\t0\t0\t0\t1920\t1080\tDesktop",
    "23068676\tChromium\t43\t0\t0\t75\t1920\t1029\tNew Tab - Chromium",
    "29360140\tXTerm\t327\t1\t50\t118\t544\t160\troot@box: /workspace",
    "29360140\tXTerm\t327\t1\t50\t118\t544\t160\troot@box: /workspace",
    "8388611\tXfce4-panel\t39\t0\t0\t0\t1920\t27\txfce4-panel",
    "20971523\tWrapper-2.0\t185\t0\t1807\t0\t1\t26\twrapper-2.0",
    "31457291\tXTerm\t401\t0\t300\t300\t544\t160\tsecond-term",
]) + "\n"


class FakeClient:
    def __init__(self, outputs=None):
        self.calls: list[list[str]] = []
        self.outputs = outputs or {}

    def run_command(self, cmd, cwd=None, env=None, timeout=300):
        self.calls.append(cmd)
        out = ""
        for key, value in self.outputs.items():
            if key in " ".join(cmd):
                out = value
        return subprocess.CompletedProcess(cmd, 0, out, "")


@pytest.fixture
def desktop(monkeypatch):
    import prax.settings
    monkeypatch.setattr(prax.settings.settings, "sandbox_enabled", True)
    client = FakeClient({"xdotool search --onlyvisible": WINDOWS})
    monkeypatch.setattr(st, "get_client", lambda: client)

    def no_host(*a, **k):
        raise AssertionError("a desktop tool ran a command on the Prax host")
    monkeypatch.setattr("prax.utils.shell.run_command", no_host)
    return client


def _argv(client, word):
    return [c for c in client.calls if word in c]


# --- everything runs inside the sandbox -----------------------------------------

def test_every_command_goes_to_the_sandbox_with_the_display_set(desktop):
    st.desktop_list_windows.invoke({})
    st.desktop_click.invoke({"x": 10, "y": 20})
    assert desktop.calls and all(c[:2] == ["env", "DISPLAY=:99"] for c in desktop.calls)


def test_no_sandbox_means_no_desktop(monkeypatch):
    import prax.settings
    monkeypatch.setattr(prax.settings.settings, "sandbox_enabled", False)
    assert st.desktop_type.invoke({"text": "ls"}).startswith("No desktop")


# --- windows: structured first --------------------------------------------------

def test_the_window_list_is_structured_and_marks_focus(desktop):
    out = st.desktop_list_windows.invoke({})
    assert "★ 0x1c0000c  XTerm" in out and "root@box: /workspace" in out
    assert "Chromium" in out and "second-term" in out
    for furniture in ("Xfwm4", "Xfdesktop", "Xfce4-panel", "Wrapper-2.0"):
        assert furniture not in out
    assert out.count("root@box") == 1                      # duplicates dropped


def test_terminal_means_the_focused_terminal_window():
    windows = [
        {"id": 1, "cls": "Chromium", "active": False, "title": "x"},
        {"id": 2, "cls": "XTerm", "active": False, "title": "a"},
        {"id": 3, "cls": "XTerm", "active": True, "title": "b"},
    ]
    win, note = st._resolve_window("terminal", windows)
    assert win["id"] == 3 and "focused" in note
    assert st._resolve_window("0x2", windows)[0]["id"] == 2
    assert st._resolve_window("chromium", windows)[0]["id"] == 1
    missing, why = st._resolve_window("emacs", windows)
    assert missing is None and "Open windows" in why and "XTerm 'b'" in why


# --- typing into the user's terminal ----------------------------------------------

def test_typing_into_the_terminal_focuses_it_then_types_verbatim(desktop):
    text = "--weird 'quotes' $(not run) && echo hi"
    out = st.desktop_type.invoke({"text": text, "window": "terminal", "press_enter": True})
    assert "into XTerm 'root@box: /workspace'" in out and "pressed Enter" in out
    activate = _argv(desktop, "windowactivate")[0]
    assert activate[-1] == str(29360140)
    typed = _argv(desktop, "type")[0]
    assert typed[-2:] == ["--", text]                      # one argument, no shell
    assert "--clearmodifiers" in typed                     # a stuck modifier can't garble it
    assert _argv(desktop, "Return")


def test_typing_into_a_window_that_isnt_open_says_so(desktop):
    out = st.desktop_type.invoke({"text": "ls", "window": "emacs"})
    assert out.startswith("No window matches") and not _argv(desktop, "type")


def test_key_names_only(desktop):
    assert st.desktop_key.invoke({"keys": "rm -rf /"}).startswith("Not key names")
    assert st.desktop_key.invoke({"keys": "ctrl+c Return", "window": "terminal"}).startswith("Pressed")
    assert _argv(desktop, "key")[-1][-2:] == ["ctrl+c", "Return"]


def test_open_passes_the_command_as_an_argument(desktop):
    st.desktop_open.invoke({"command": "xterm -title 'x'; echo"})
    argv = _argv(desktop, "desktop_open")[0]
    assert argv[-2:] == ["desktop_open", "xterm -title 'x'; echo"]


# --- seeing: the vision model reads the screen ----------------------------------

def test_the_screenshot_goes_to_the_vision_model_inline(desktop, monkeypatch):
    desktop.outputs["scrot"] = "QUJD\n1920 1080\n"
    seen = {}

    def vision(url, prompt):
        seen.update(url=url, prompt=prompt)
        return "An xterm in front. Last line: `hello`."
    monkeypatch.setattr("prax.agent.vision_tools.analyze_image_impl", vision)
    out = st.desktop_screenshot.invoke({"question": "what did the terminal print?"})
    assert seen["url"] == "data:image/jpeg;base64,QUJD"
    assert "what did the terminal print?" in seen["prompt"] and "1920x1080" in seen["prompt"]
    assert out.startswith("Desktop (1920x1080):") and "hello" in out


def test_a_vision_failure_is_reported_not_papered_over(desktop, monkeypatch):
    desktop.outputs["scrot"] = "QUJD\n1920 1080\n"

    def boom(url, prompt):
        raise RuntimeError("no vision key")
    monkeypatch.setattr("prax.agent.vision_tools.analyze_image_impl", boom)
    out = st.desktop_screenshot.invoke({})
    assert "could not read it" in out and "no vision key" in out


def test_vision_accepts_an_inline_image():
    from prax.agent.vision_tools import _fetch_image_base64
    assert _fetch_image_base64("data:image/jpeg;base64,QUJD") == ("QUJD", "image/jpeg")


# --- sandbox_shell says when its output was invisible ----------------------------

def test_sandbox_shell_in_the_desktop_view_says_nobody_saw_it(monkeypatch):
    import prax.settings
    from prax.agent.user_context import current_active_view
    monkeypatch.setattr(prax.settings.settings, "sandbox_enabled", True)

    class Shell:
        def run_shell(self, command, timeout=60):
            return {"stdout": "hello\n", "stderr": "", "exit_code": 0}
    monkeypatch.setattr(st, "get_client", lambda: Shell())
    token = current_active_view.set("desktop")
    try:
        out = st.sandbox_shell.invoke({"command": "echo hello"})
    finally:
        current_active_view.reset(token)
    assert out.startswith("[Ran in the background: the user did NOT see this")
    assert "desktop_type" in out and "hello" in out


# --- the auditor catches the claim -------------------------------------------------

LIVE_REPLY = ("Done! You should see that message pop up in your terminal right now. "
              "That's me typing directly into the terminal you're watching.")


def _turn(*tools):
    msgs = [AIMessage(content="")]
    for name, content in tools:
        msgs.append(ToolMessage(content=content, name=name, tool_call_id=name))
    return msgs


def test_the_live_false_claim_is_flagged():
    from prax.agent.claim_audit import audit_unseen_on_screen
    found = audit_unseen_on_screen(LIVE_REPLY, _turn(("sandbox_shell", "hello (exit code: 0)")), "desktop")
    assert found and "terminal" in found["phrases"][0]


def test_a_real_desktop_action_grounds_it():
    from prax.agent.claim_audit import audit_unseen_on_screen
    ok = _turn(("desktop_type", "Typed 10 characters into XTerm 'bash' and pressed Enter."))
    assert audit_unseen_on_screen(LIVE_REPLY, ok, "desktop") is None
    failed = _turn(("desktop_type", "No window matches 'terminal'. Open windows: none."))
    assert audit_unseen_on_screen(LIVE_REPLY, failed, "desktop")


def test_the_shared_terminal_tab_grounds_sandbox_shell():
    from prax.agent.claim_audit import audit_unseen_on_screen
    msgs = _turn(("sandbox_shell", "hello"))
    assert audit_unseen_on_screen(LIVE_REPLY, msgs, "terminal") is None
    assert audit_unseen_on_screen(LIVE_REPLY, msgs, "chat")


def test_ordinary_replies_are_not_flagged():
    from prax.agent.claim_audit import audit_unseen_on_screen
    assert audit_unseen_on_screen("The upgrade finished: 128 packages.", _turn(), "desktop") is None


# --- Prax knows when the user is on the desktop -----------------------------------

def test_the_desktop_view_has_a_label_and_rules():
    from prax.blueprints import teamwork_routes
    assert "Desktop tab" in teamwork_routes._VIEW_LABELS["desktop"]
    src = open(teamwork_routes.__file__).read()
    assert 'active_view == "desktop"' in src and "runs in the BACKGROUND" in src


def test_the_orchestrator_can_pair_on_the_desktop(monkeypatch):
    import prax.settings
    from prax.agent.tools import build_default_tools
    monkeypatch.setattr(prax.settings.settings, "sandbox_enabled", True)
    names = {t.name for t in build_default_tools()}
    assert {"desktop_type", "desktop_screenshot", "sandbox_shell"} <= names
    monkeypatch.setattr(prax.settings.settings, "desktop_kernel_tools", False)
    names = {t.name for t in build_default_tools()}
    assert "desktop_type" not in names


# --- restarting a stuck sandbox ----------------------------------------------------

def test_prax_can_restart_a_stuck_sandbox(monkeypatch):
    import prax.settings
    monkeypatch.setattr(prax.settings.settings, "sandbox_enabled", True)

    class Client:
        def restart_sandbox(self):
            return {"restarted": "prax-sandbox-sandbox-1", "ready": True}
    monkeypatch.setattr(st, "get_client", lambda: Client())
    out = st.sandbox_restart.invoke({"reason": "the desktop froze"})
    assert out.startswith("Sandbox restarted (the desktop froze) and it is ready")
    assert "files and installed packages are still there" in out


def test_restart_failures_and_an_old_client_say_so(monkeypatch):
    import prax.settings
    monkeypatch.setattr(prax.settings.settings, "sandbox_enabled", True)

    class Failing:
        def restart_sandbox(self):
            return {"error": "No sandbox container to restart"}
    monkeypatch.setattr(st, "get_client", lambda: Failing())
    assert "No sandbox container" in st.sandbox_restart.invoke({"reason": "x"})
    monkeypatch.setattr(st, "get_client", lambda: object())
    assert "update prax-sandbox" in st.sandbox_restart.invoke({"reason": "x"})


def test_restart_is_medium_risk_and_in_both_spokes():
    from prax.agent.action_policy import TOOL_RISK_MAP, RiskLevel
    from prax.agent.spokes.desktop.agent import build_tools
    assert TOOL_RISK_MAP["sandbox_restart"] is RiskLevel.MEDIUM
    assert "sandbox_restart" in {t.name for t in build_tools()}
    src = open(st.__file__).read()
    assert "sandbox_install, sandbox_rebuild, sandbox_restart," in src
