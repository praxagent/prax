"""Key-free unit tests for the data_query tool (prax.agent.data_tools).

The pure formatting helper carries the report logic and is tested with zero
sandbox and zero keys; the tool's flag/sandbox gating + shell plumbing are tested
by monkeypatching settings + the sandbox client so no container is needed.
"""
from __future__ import annotations

import importlib

from prax.agent import data_tools as dt

# --------------------------------------------------------------------------- #
# Pure formatting helper
# --------------------------------------------------------------------------- #

def test_format_passes_through_result_table():
    out = dt._format_result(exit_code=0, stdout=" four\n    4\n\n[1 row(s)]", stderr="")
    assert "four" in out and "[1 row(s)]" in out


def test_format_surfaces_query_error_as_normal_output():
    # A bad-SQL result is a useful signal the agent should see + fix, not a crash.
    out = dt._format_result(
        exit_code=0, stdout="QUERY ERROR: BinderException Referenced column missing",
        stderr="")
    assert "QUERY ERROR" in out


def test_format_reports_missing_deps():
    out = dt._format_result(exit_code=0, stdout=dt._MISSING_DEPS_MARKER, stderr="")
    assert "not installed" in out.lower() and "rebuild" in out.lower()


def test_format_falls_back_to_stderr_when_no_stdout():
    out = dt._format_result(exit_code=1, stdout="", stderr="Traceback: boom")
    assert "STDERR" in out and "boom" in out


def test_format_empty_output():
    out = dt._format_result(exit_code=0, stdout="", stderr="")
    assert "no output" in out.lower()


# --------------------------------------------------------------------------- #
# Tool gating (flag + sandbox) and end-to-end plumbing with a fake client
# --------------------------------------------------------------------------- #

class _FakeClient:
    def __init__(self, stdout="", stderr="", error=None):
        self._stdout, self._stderr, self._error = stdout, stderr, error
        self.last_cmd = None

    def run_shell(self, command, timeout=60):
        self.last_cmd = command
        if self._error:
            return {"error": self._error}
        return {"stdout": self._stdout, "stderr": self._stderr, "exit_code": 0}


def _reload():
    return importlib.reload(importlib.import_module("prax.agent.data_tools"))


def test_data_query_disabled_by_default(monkeypatch):
    mod = _reload()
    from prax.settings import settings
    monkeypatch.setattr(settings, "data_tools_enabled", False, raising=False)
    out = mod.data_query.invoke({"sql": "SELECT 1"})
    assert "disabled" in out.lower()


def test_data_query_needs_sandbox(monkeypatch):
    mod = _reload()
    from prax.settings import settings
    monkeypatch.setattr(settings, "data_tools_enabled", True, raising=False)
    monkeypatch.setattr(type(settings), "sandbox_available", property(lambda self: False))
    try:
        out = mod.data_query.invoke({"sql": "SELECT 1"})
        assert "Sandbox is disabled" in out
    finally:
        monkeypatch.undo()


def test_build_data_tools_respects_flags(monkeypatch):
    mod = _reload()
    from prax.settings import settings
    monkeypatch.setattr(settings, "data_tools_enabled", False, raising=False)
    assert mod.build_data_tools() == []
    monkeypatch.setattr(settings, "data_tools_enabled", True, raising=False)
    monkeypatch.setattr(type(settings), "sandbox_available", property(lambda self: True))
    try:
        assert len(mod.build_data_tools()) == 1
    finally:
        monkeypatch.undo()


def test_data_query_end_to_end_with_fake_client(monkeypatch):
    mod = _reload()
    from prax.settings import settings
    monkeypatch.setattr(settings, "data_tools_enabled", True, raising=False)
    monkeypatch.setattr(type(settings), "sandbox_available", property(lambda self: True))
    fake = _FakeClient(stdout=" four\n    4\n\n[1 row(s)]\n" + mod._EXIT_MARKER + "0")
    monkeypatch.setattr(mod, "get_client", lambda: fake)
    try:
        out = mod.data_query.invoke({"sql": "SELECT 2+2 AS four"})
        assert "four" in out and "[1 row(s)]" in out
        # The SQL + runner were shipped base64 through the shell, run by the venv python.
        assert "base64 -d" in fake.last_cmd
        assert "/opt/prax-venv/bin/python" in fake.last_cmd
    finally:
        monkeypatch.undo()


def test_data_query_reports_missing_deps(monkeypatch):
    mod = _reload()
    from prax.settings import settings
    monkeypatch.setattr(settings, "data_tools_enabled", True, raising=False)
    monkeypatch.setattr(type(settings), "sandbox_available", property(lambda self: True))
    fake = _FakeClient(stdout=mod._MISSING_DEPS_MARKER + "\n" + mod._EXIT_MARKER + "0")
    monkeypatch.setattr(mod, "get_client", lambda: fake)
    try:
        out = mod.data_query.invoke({"sql": "SELECT 1"})
        assert "not installed" in out.lower()
    finally:
        monkeypatch.undo()


def test_data_query_surfaces_sandbox_error(monkeypatch):
    mod = _reload()
    from prax.settings import settings
    monkeypatch.setattr(settings, "data_tools_enabled", True, raising=False)
    monkeypatch.setattr(type(settings), "sandbox_available", property(lambda self: True))
    fake = _FakeClient(error="container not running")
    monkeypatch.setattr(mod, "get_client", lambda: fake)
    try:
        out = mod.data_query.invoke({"sql": "SELECT 1"})
        assert "Sandbox error" in out and "container not running" in out
    finally:
        monkeypatch.undo()
