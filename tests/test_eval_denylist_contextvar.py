"""The eval tool denylist is a ContextVar consulted at EVERY tool-list build.

The eval runner used to monkey-patch ``tool_registry.get_registered_tools``.
The orchestrator binds that name by ``from``-import at module load, so the
patch never reached it, and the spokes build their own tool lists without
consulting the registry at all.  ``tool_registry.eval_tool_denylist`` +
``apply_eval_denylist`` are applied at the registry build and at each spoke /
sub-agent build site; the eval scope sets the ContextVar.
"""
from __future__ import annotations

import contextvars

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from prax.agent import tool_registry


def _names(tools) -> set[str]:
    return {t.name for t in tools}


def _in_context_with_denylist(deny: set[str], fn):
    """Run *fn* in a copied context where the denylist is set — the caller's
    context stays unset, which is what the 'unset → unchanged' checks rely on."""
    def _call():
        tool_registry.eval_tool_denylist.set(frozenset(deny))
        return fn()
    return contextvars.copy_context().run(_call)


def test_apply_eval_denylist_is_a_noop_when_unset():
    tools = [StructuredTool.from_function(func=lambda: "x", name=n, description=n)
             for n in ("a", "b")]
    assert tool_registry.eval_tool_denylist.get() == frozenset()
    assert _names(tool_registry.apply_eval_denylist(tools)) == {"a", "b"}


def test_apply_eval_denylist_filters_by_name():
    tools = [StructuredTool.from_function(func=lambda: "x", name=n, description=n)
             for n in ("source_read", "b")]
    kept = _in_context_with_denylist({"source_read"},
                                     lambda: tool_registry.apply_eval_denylist(tools))
    assert _names(kept) == {"b"}


def test_registry_build_honours_the_denylist():
    baseline = _names(tool_registry.get_registered_tools())
    assert "get_current_datetime" in baseline  # a real hub tool, so the check is not vacuous

    denied = _in_context_with_denylist(
        {"get_current_datetime"}, lambda: _names(tool_registry.get_registered_tools()))
    assert "get_current_datetime" not in denied
    assert denied == baseline - {"get_current_datetime"}
    # Unset in this context → unchanged.
    assert _names(tool_registry.get_registered_tools()) == baseline


def test_orchestrator_tool_set_honours_the_denylist():
    """The orchestrator's ``self.tools`` is built from the registry by
    from-import — the ContextVar reaches it where the old monkey-patch did not."""
    from prax.agent.orchestrator import ConversationAgent

    baseline = _names(tool_registry.get_registered_tools())  # what an undenied agent gets
    assert "get_current_datetime" in baseline
    denied = _in_context_with_denylist(
        {"get_current_datetime"}, lambda: _names(ConversationAgent().tools))
    assert "get_current_datetime" not in denied
    assert denied == baseline - {"get_current_datetime"}


class _FakeGraph:
    def invoke(self, inputs, config=None):
        return {"messages": [AIMessage(content="done")]}


def _capture(monkeypatch, module):
    captured: dict = {}

    def _build_agent_loop(llm, tools, **kwargs):
        captured["tools"] = list(tools)
        return _FakeGraph()

    monkeypatch.setattr(module, "build_agent_loop", _build_agent_loop)
    monkeypatch.setattr(module, "build_llm", lambda **kwargs: object())
    return captured


def _tool(name):
    return StructuredTool.from_function(func=lambda x="": name, name=name, description=name)


def test_run_spoke_honours_the_denylist(monkeypatch):
    from prax.agent.spokes import _runner
    captured = _capture(monkeypatch, _runner)

    def _build():
        _runner.run_spoke(task="t", system_prompt="s", config_key="subagent_test",
                          tools=[_tool("source_read"), _tool("get_current_datetime")])
        return _names(captured["tools"])

    assert _in_context_with_denylist({"source_read"}, _build) == {"get_current_datetime"}
    assert _build() == {"source_read", "get_current_datetime"}  # unset → unchanged


def test_subagent_honours_the_denylist(monkeypatch):
    from prax.agent import subagent
    captured = _capture(monkeypatch, subagent)
    monkeypatch.setattr(subagent, "_get_tools_for_category",
                        lambda category: [_tool("source_read"), _tool("get_current_datetime")])

    def _build():
        subagent._run_subagent("t", "research")
        return _names(captured["tools"])

    assert _in_context_with_denylist({"source_read"}, _build) == {"get_current_datetime"}
    assert _build() == {"source_read", "get_current_datetime"}


def test_research_agent_honours_the_denylist(monkeypatch):
    from prax.agent import research_agent
    captured = _capture(monkeypatch, research_agent)
    monkeypatch.setattr(research_agent, "_build_research_tools",
                        lambda depth=0: [_tool("source_read"), _tool("get_current_datetime")])

    def _build():
        research_agent._run_research("q")
        return _names(captured["tools"])

    assert _in_context_with_denylist({"source_read"}, _build) == {"get_current_datetime"}
    assert _build() == {"source_read", "get_current_datetime"}


def test_self_improve_agent_honours_the_denylist(monkeypatch):
    """Uses the REAL self-improve tool builder: ``source_read`` is in it."""
    from prax.agent import self_improve_agent
    captured = _capture(monkeypatch, self_improve_agent)

    def _build():
        self_improve_agent.delegate_self_improve.func("t")
        return _names(captured["tools"])

    unfiltered = _build()
    assert "source_read" in unfiltered
    assert _in_context_with_denylist({"source_read"}, _build) == unfiltered - {"source_read"}
