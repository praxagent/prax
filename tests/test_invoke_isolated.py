"""invoke_isolated severs LangChain callback inheritance at the spoke boundary.

A spoke's loop runs inside one of the orchestrator's tool calls, and LangChain
propagates the ambient callback config down a contextvar — so every
spoke-internal tool event fired BOTH handlers and was recorded twice, once
under each parent (6 duplicate pairs in one 31-node live trace, 2026-08-30).
"""
from __future__ import annotations

from langchain_core.runnables.config import var_child_runnable_config

from prax.agent.agent_loop import invoke_isolated


class _FakeGraph:
    """Records what the propagation var contained DURING the invoke."""
    def __init__(self):
        self.seen_inside = "unset"
        self.config = None

    def invoke(self, inputs, config=None):
        self.seen_inside = var_child_runnable_config.get()
        self.config = config
        return {"ok": True, "inputs": inputs}


def test_inherited_config_is_severed_inside_and_preserved_outside():
    ambient = {"callbacks": ["the-orchestrator-handler"]}
    token = var_child_runnable_config.set(ambient)
    try:
        g = _FakeGraph()
        out = invoke_isolated(g, {"messages": []}, config={"callbacks": ["spoke"]})
        assert out == {"ok": True, "inputs": {"messages": []}}
        # Inside: no inherited config — only what was passed explicitly.
        assert g.seen_inside is None
        assert g.config == {"callbacks": ["spoke"]}
        # Outside: the caller's context is untouched.
        assert var_child_runnable_config.get() == ambient
    finally:
        var_child_runnable_config.reset(token)


def test_exceptions_propagate_and_do_not_leak_context():
    class _Boom:
        def invoke(self, inputs, config=None):
            raise RuntimeError("boom")

    token = var_child_runnable_config.set({"callbacks": ["ambient"]})
    try:
        try:
            invoke_isolated(_Boom(), {})
            raise AssertionError("should have raised")
        except RuntimeError:
            pass
        assert var_child_runnable_config.get() == {"callbacks": ["ambient"]}
    finally:
        var_child_runnable_config.reset(token)


def test_spoke_runner_and_subagent_use_it():
    """The two delegation paths must both route through invoke_isolated."""
    import pathlib
    for f in ("prax/agent/spokes/_runner.py", "prax/agent/subagent.py"):
        src = pathlib.Path(f).read_text()
        assert "invoke_isolated(" in src, f
