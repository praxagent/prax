"""Tests for sandbox_tools LangChain wrappers."""
import importlib

from prax.agent.user_context import current_user_id


class TestSandboxView:
    """Windowed, line-numbered file viewer — the ACI pattern."""

    def _make_fake_shell(self, total_lines: int):
        """Return a run_shell stub that simulates awk behavior on a synthetic file."""

        def fake(cmd, timeout=60):
            import re
            m = re.search(r"-v s=(\d+) -v e=(\d+)", cmd)
            if not m:
                return {"stdout": "", "stderr": "no match", "exit_code": 1}
            s, e = int(m.group(1)), int(m.group(2))
            out_lines = []
            for n in range(max(1, s), min(total_lines, e) + 1):
                out_lines.append(f"{n:>6}  line-{n}")
            out_lines.append(f"---TOTAL:{total_lines}")
            return {"stdout": "\n".join(out_lines), "stderr": "", "exit_code": 0}

        return fake

    def _module(self, monkeypatch, total_lines: int = 250):
        module = importlib.reload(importlib.import_module("prax.agent.sandbox_tools"))
        svc = importlib.import_module("prax_sandbox.control_plane")
        monkeypatch.setattr(svc, "run_shell", self._make_fake_shell(total_lines))
        current_user_id.set("+10000000000")
        return module

    def test_view_returns_100_line_window_with_line_numbers(self, monkeypatch):
        module = self._module(monkeypatch, total_lines=250)
        result = module.sandbox_view.invoke({"path": "/workspace/app.py"})
        assert "lines 1-100 of 250" in result
        assert "     1  line-1" in result
        assert "   100  line-100" in result
        assert "   101" not in result  # window ends at 100

    def test_view_explicit_start_line(self, monkeypatch):
        module = self._module(monkeypatch, total_lines=250)
        result = module.sandbox_view.invoke({"path": "/workspace/app.py", "start_line": 50})
        assert "lines 50-149 of 250" in result
        assert "    50  line-50" in result

    def test_window_capped_at_max(self, monkeypatch):
        module = self._module(monkeypatch, total_lines=1000)
        result = module.sandbox_view.invoke({
            "path": "/workspace/big.py", "start_line": 1, "window": 5000,
        })
        # Capped at _VIEW_MAX_WINDOW (300).
        assert "lines 1-300 of 1000" in result

    def test_view_past_end_signals_eof(self, monkeypatch):
        module = self._module(monkeypatch, total_lines=50)
        result = module.sandbox_view.invoke({"path": "/workspace/short.py"})
        assert "lines 1-50 of 50" in result
        assert "end of file" in result

    def test_scroll_down_picks_up_from_last_view(self, monkeypatch):
        module = self._module(monkeypatch, total_lines=500)
        module.sandbox_view.invoke({"path": "/workspace/app.py"})
        result = module.sandbox_scroll.invoke({"path": "/workspace/app.py"})
        assert "lines 101-200 of 500" in result

    def test_scroll_up_goes_back_one_window(self, monkeypatch):
        module = self._module(monkeypatch, total_lines=500)
        module.sandbox_view.invoke({"path": "/workspace/app.py", "start_line": 200})
        # Last end = 299. Scroll up should start near 100.
        result = module.sandbox_scroll.invoke({"path": "/workspace/app.py", "direction": "up"})
        assert "lines 100-" in result

    def test_goto_centers_window_on_line(self, monkeypatch):
        module = self._module(monkeypatch, total_lines=500)
        result = module.sandbox_goto.invoke({"path": "/workspace/app.py", "line": 200})
        # Default window=100, centered: start = 200 - 50 = 150.
        assert "lines 150-249 of 500" in result

    def test_empty_file(self, monkeypatch):
        module = self._module(monkeypatch, total_lines=0)
        result = module.sandbox_view.invoke({"path": "/workspace/empty.py"})
        assert "empty" in result.lower() or "does not exist" in result.lower()

    def test_shell_error_propagated(self, monkeypatch):
        module = importlib.reload(importlib.import_module("prax.agent.sandbox_tools"))
        svc = importlib.import_module("prax_sandbox.control_plane")
        monkeypatch.setattr(
            svc, "run_shell",
            lambda cmd, timeout=60: {"error": "container not running"},
        )
        current_user_id.set("+10000000000")
        result = module.sandbox_view.invoke({"path": "/workspace/app.py"})
        assert "container not running" in result


def test_coding_session_tools_removed(monkeypatch):
    """The OpenCode coding-session tools are gone (the sandbox image + client no
    longer ship the session API; Prax codes directly). The pure-execution tools
    stay, and the sandbox spoke delegates direct code execution."""
    from prax.settings import settings
    monkeypatch.setattr(type(settings), "sandbox_available", property(lambda self: True))
    from prax.agent.sandbox_tools import build_sandbox_tools
    from prax.agent.spokes.sandbox.agent import build_spoke_tools
    names = {t.name for t in build_sandbox_tools()}
    assert "sandbox_shell" in names                 # direct exec — always available
    for gone in ("sandbox_start", "sandbox_message", "sandbox_review",
                 "sandbox_finish", "sandbox_abort", "sandbox_search", "sandbox_execute"):
        assert gone not in names
    # delegate_sandbox is offered (direct code-execution sub-agent).
    assert [t.name for t in build_spoke_tools()] == ["delegate_sandbox"]
