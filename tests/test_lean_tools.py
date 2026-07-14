"""Key-free unit tests for the Lean proof-check tool (prax.agent.lean_tools).

The pure parsing/verdict helpers carry the logic and are tested with zero
sandbox and zero keys; the tool's flag/sandbox gating is tested by monkeypatching
settings + the sandbox client so no container is needed.
"""
from __future__ import annotations

import importlib

from prax.agent import lean_tools as lt

# --------------------------------------------------------------------------- #
# Pure helpers: forbidden-token scan + axiom parsing
# --------------------------------------------------------------------------- #

def test_find_forbidden_whole_words_only():
    assert lt._find_forbidden("theorem t : True := by sorry") == ["sorry"]
    assert lt._find_forbidden("example := by admit") == ["admit"]
    assert lt._find_forbidden("decide via native_decide") == ["native_decide"]
    # No false positive on substrings (e.g. a name containing 'sorry').
    assert lt._find_forbidden("def sorryFree := 1") == []
    assert lt._find_forbidden("theorem t : 1 = 1 := rfl") == []


def test_parse_axioms_standard_and_none_and_absent():
    assert lt._parse_axioms("'t' depends on axioms: [propext, Classical.choice, Quot.sound]") == \
        ["propext", "Classical.choice", "Quot.sound"]
    assert lt._parse_axioms("'t' does not depend on any axioms") == []
    assert lt._parse_axioms("no axiom line here") is None


# --------------------------------------------------------------------------- #
# Verdict formatting (the trust gate)
# --------------------------------------------------------------------------- #

def test_format_clean_compile_with_standard_axioms():
    out = lt._format_result(
        lean_rc=0,
        stdout="'add_comm' depends on axioms: [propext, Classical.choice, Quot.sound]",
        stderr="", source="theorem add_comm ... := ...", theorem_name="add_comm")
    assert "compiled successfully" in out
    assert "AXIOM AUDIT" in out and "clean" in out
    assert "TRUST GATE" not in out


def test_format_flags_sorry_even_when_it_compiles():
    # A proof with `sorry` type-checks — the trust gate must catch it.
    out = lt._format_result(
        lean_rc=0, stdout="", stderr="warning: declaration uses 'sorry'",
        source="theorem hard : P := by sorry", theorem_name="")
    assert "compiled successfully" in out
    assert "TRUST GATE" in out and "sorry" in out


def test_format_flags_nonstandard_axiom():
    out = lt._format_result(
        lean_rc=0,
        stdout="'t' depends on axioms: [propext, myCustomAxiom]",
        stderr="", source="axiom myCustomAxiom : P\ntheorem t ...", theorem_name="t")
    assert "NON-standard axioms" in out and "myCustomAxiom" in out


def test_format_compile_failure_surfaces_diagnostics():
    out = lt._format_result(
        lean_rc=1, stdout="", stderr="Check.lean:1:2: error: unexpected token",
        source="theorem t : 1 = 2 := rfl", theorem_name="")
    assert "FAILED" in out
    assert "unexpected token" in out


def test_format_failed_compile_never_shows_axiom_audit():
    # A failed compile still emits a `depends on axioms:` line (partial
    # elaboration) — it must NOT be presented as a clean audit under a ✗.
    out = lt._format_result(
        lean_rc=1,
        stdout="'t' depends on axioms: [propext]",
        stderr="error: something", source="theorem t ...", theorem_name="t")
    assert "FAILED" in out
    assert "AXIOM AUDIT" not in out and "clean" not in out


def test_format_compiled_but_axioms_unreadable():
    # Compiled, name given, but no axiom line (e.g. name mismatch) → advisory.
    out = lt._format_result(
        lean_rc=0, stdout="", stderr="",
        source="theorem t : 1 = 1 := rfl", theorem_name="wrongName")
    assert "Could not read axioms" in out and "wrongName" in out


def test_find_forbidden_ignores_comments():
    # sorry/admit inside comments must NOT trip the trust gate.
    assert lt._find_forbidden("theorem t : 1 = 1 := rfl -- no sorry needed here") == []
    assert lt._find_forbidden("/- we admit nothing -/\ntheorem t : 1 = 1 := rfl") == []
    # ...but a real one in proof code still is caught.
    assert lt._find_forbidden("theorem t : P := by sorry") == ["sorry"]


def test_format_proof_with_no_axioms_is_clean():
    out = lt._format_result(
        lean_rc=0, stdout="'t' does not depend on any axioms",
        stderr="", source="theorem t : 1 = 1 := rfl", theorem_name="t")
    assert "clean" in out and "(none)" in out


# --------------------------------------------------------------------------- #
# Tool gating (flag + sandbox) and end-to-end parse with a fake client
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
    return importlib.reload(importlib.import_module("prax.agent.lean_tools"))


def test_lean_check_disabled_by_default(monkeypatch):
    mod = _reload()
    from prax.settings import settings
    monkeypatch.setattr(settings, "lean_tools_enabled", False, raising=False)
    out = mod.lean_check.invoke({"source": "theorem t : 1 = 1 := rfl"})
    assert "disabled" in out.lower()


def test_lean_check_needs_sandbox(monkeypatch):
    mod = _reload()
    from prax.settings import settings
    monkeypatch.setattr(settings, "lean_tools_enabled", True, raising=False)
    monkeypatch.setattr(type(settings), "sandbox_available", property(lambda self: False))
    try:
        out = mod.lean_check.invoke({"source": "theorem t : 1 = 1 := rfl"})
        assert "Sandbox is disabled" in out
    finally:
        monkeypatch.undo()


def test_build_lean_tools_respects_flags(monkeypatch):
    mod = _reload()
    from prax.settings import settings
    monkeypatch.setattr(settings, "lean_tools_enabled", False, raising=False)
    assert mod.build_lean_tools() == []
    monkeypatch.setattr(settings, "lean_tools_enabled", True, raising=False)
    monkeypatch.setattr(type(settings), "sandbox_available", property(lambda self: True))
    try:
        assert len(mod.build_lean_tools()) == 1
    finally:
        monkeypatch.undo()


def test_lean_check_end_to_end_with_fake_client(monkeypatch):
    mod = _reload()
    from prax.settings import settings
    monkeypatch.setattr(settings, "lean_tools_enabled", True, raising=False)
    monkeypatch.setattr(type(settings), "sandbox_available", property(lambda self: True))
    fake = _FakeClient(
        stdout="'add_comm' depends on axioms: [propext]\n" + mod._EXIT_MARKER + "0")
    monkeypatch.setattr(mod, "get_client", lambda: fake)
    try:
        out = mod.lean_check.invoke(
            {"source": "theorem add_comm ... := ...", "theorem_name": "add_comm"})
        assert "compiled successfully" in out
        assert "clean" in out  # propext alone is standard
        # The source was shipped base64 through the shell.
        assert "base64 -d" in fake.last_cmd
    finally:
        monkeypatch.undo()


def test_lean_check_reports_missing_toolchain(monkeypatch):
    mod = _reload()
    from prax.settings import settings
    monkeypatch.setattr(settings, "lean_tools_enabled", True, raising=False)
    monkeypatch.setattr(type(settings), "sandbox_available", property(lambda self: True))
    # Reality: the trailing `echo` still fires when `lean` is absent, so the
    # marker IS present with rc=127 — detection keys on the code, not marker-absence.
    fake = _FakeClient(stdout=mod._EXIT_MARKER + "127",
                       stderr="bash: line 1: lean: not found")
    monkeypatch.setattr(mod, "get_client", lambda: fake)
    try:
        out = mod.lean_check.invoke({"source": "theorem t : 1 = 1 := rfl"})
        assert "toolchain not found" in out.lower()
    finally:
        monkeypatch.undo()


def test_lean_check_failed_compile_via_marker(monkeypatch):
    mod = _reload()
    from prax.settings import settings
    monkeypatch.setattr(settings, "lean_tools_enabled", True, raising=False)
    monkeypatch.setattr(type(settings), "sandbox_available", property(lambda self: True))
    fake = _FakeClient(
        stdout=mod._EXIT_MARKER + "1",
        stderr="Check.lean:1:19: error: type mismatch")
    monkeypatch.setattr(mod, "get_client", lambda: fake)
    try:
        out = mod.lean_check.invoke({"source": "theorem t : 1 = 2 := rfl"})
        assert "FAILED" in out and "type mismatch" in out
    finally:
        monkeypatch.undo()
