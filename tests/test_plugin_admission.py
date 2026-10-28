"""Plugin admission says what it checked, and can check behaviour.

docs/research/autoenvscaling.md: "it imports and registers" is far from "it
works". The sandbox test used to check only the first, and plugin_write
replied "sandbox test PASSED". An optional example call now runs a tool for
real; without one, the reply says no tool was called.
"""
from __future__ import annotations

import json
import textwrap

import pytest

from prax.agent.plugin_tools import _describe_check, _parse_example_call
from prax.plugins.sandbox import sandbox_test_plugin


@pytest.fixture
def plugin(tmp_path):
    p = tmp_path / "plugin.py"
    p.write_text(textwrap.dedent('''\
        from langchain_core.tools import tool

        PLUGIN_VERSION = "1"

        @tool
        def hello_world(name: str) -> str:
            """Say hello to someone."""
            return f"Hello, {name}!"

        @tool
        def broken(x: str) -> str:
            """Always fails."""
            raise RuntimeError("boom")

        def register():
            return [hello_world, broken]
    '''))
    return str(p)


class TestSandboxExample:
    def test_without_an_example_behaviour_is_untested(self, plugin):
        r = sandbox_test_plugin(plugin)
        assert r["passed"] is True and r["behaviour"] == "untested"

    def test_a_matching_example_is_tested(self, plugin):
        r = sandbox_test_plugin(plugin, example={
            "tool": "hello_world", "args": {"name": "Ada"}, "expect": "Hello, Ada"})
        assert r["passed"] is True and r["behaviour"] == "tested"
        assert r["example_output"] == "Hello, Ada!"

    def test_wrong_output_fails(self, plugin):
        r = sandbox_test_plugin(plugin, example={
            "tool": "hello_world", "args": {"name": "Ada"}, "expect": "Goodbye"})
        assert r["passed"] is False and r["behaviour"] == "failed"
        assert any("does not contain 'Goodbye'" in e for e in r["errors"])

    def test_a_raising_tool_fails(self, plugin):
        r = sandbox_test_plugin(plugin, example={"tool": "broken", "args": {"x": "1"}})
        assert r["passed"] is False
        assert any("boom" in e for e in r["errors"])

    def test_an_unknown_tool_fails(self, plugin):
        r = sandbox_test_plugin(plugin, example={"tool": "nope", "args": {}})
        assert r["passed"] is False
        assert any("no tool named 'nope'" in e for e in r["errors"])


class TestParseAndDescribe:
    def test_parse(self):
        assert _parse_example_call("") == (None, "")
        ex, err = _parse_example_call(json.dumps({"tool": "t", "args": {"a": 1}, "expect": "x"}))
        assert err == "" and ex["tool"] == "t"
        assert "not valid JSON" in _parse_example_call("{nope")[1]
        assert "needs" in _parse_example_call(json.dumps({"args": {}}))[1]
        assert "object" in _parse_example_call(json.dumps({"tool": "t", "args": [1]}))[1]

    def test_the_reply_never_claims_more_than_was_checked(self):
        untested = _describe_check({"tools": ["a", "b"], "behaviour": "untested"}, None)
        assert "No tool was called" in untested and "PASSED" not in untested
        tested = _describe_check({"tools": ["a"], "behaviour": "tested"}, {"tool": "a"})
        assert "example call to a returned the expected output" in tested


def test_plugin_write_reply_reports_the_check(tmp_path, monkeypatch):
    """End to end through plugin_write: no example → 'No tool was called';
    a failing example → reverted."""
    import prax.agent.plugin_tools as pt

    class _Reg:
        def backup_file(self, _p): pass
        def restore_file(self, _p): return False

    class _Loader:
        registry = _Reg()
        def needs_trust_record(self, _p): return False

    target = tmp_path / "greeter" / "plugin.py"
    monkeypatch.setattr(pt, "_safe_plugin_path", lambda _n: target)
    monkeypatch.setattr(pt, "get_plugin_loader", lambda: _Loader())
    monkeypatch.setattr(pt, "_integrity_guarded", lambda *_a: False)
    monkeypatch.setattr(pt, "_audit_plugin_event", lambda *_a, **_k: None)
    code = textwrap.dedent('''\
        from langchain_core.tools import tool

        @tool
        def greet(name: str) -> str:
            """Greet."""
            return "hi " + name

        def register():
            return [greet]
    ''')
    fn = getattr(pt.plugin_write, "func", pt.plugin_write)
    out = fn(name="greeter", code=code)
    assert "No tool was called" in out and "PASSED" not in out
    out = fn(name="greeter", code=code,
             example_call=json.dumps({"tool": "greet", "args": {"name": "x"}, "expect": "hi x"}))
    assert "returned the expected output" in out
    out = fn(name="greeter", code=code,
             example_call=json.dumps({"tool": "greet", "args": {"name": "x"}, "expect": "bye"}))
    assert out.startswith("Sandbox test FAILED") and not target.exists()
