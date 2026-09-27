"""Guards that stop a runaway turn whatever the cause, not just a broken tool.

In the incident, 13 of 19 delegations to the browser spoke "succeeded" with
false claims while nothing changed, so a failure count never tripped. These
guards don't need to know why progress stalled: a repetition limit per spoke,
and a per-request cost and time budget that ends in a report, not silence.
"""
from __future__ import annotations

import pytest
from langchain_core.tools import StructuredTool

import prax.agent.governed_tool as gov
import prax.settings as prax_settings
from prax.services import turn_registry as reg
from prax.services.turn_registry import TurnBudgetExceeded, TurnCancelled


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reg._turns.clear()
    for name, value in (("turn_budget_usd", 0.0), ("turn_budget_seconds", 0),
                        ("spoke_call_limit", 0), ("spoke_failure_limit", 0)):
        monkeypatch.setattr(prax_settings.settings, name, value)
    yield
    reg._turns.clear()
    gov.drain_audit_log()


class _Graph:
    def __init__(self, spent: float, complete: bool = True):
        self.spent, self.complete = spent, complete

    def cost_so_far(self):
        return self.spent, self.complete


def _tool(calls: list):
    def act(value: str = "") -> str:
        calls.append(value)
        return "ok"
    return gov.wrap_with_governance(
        StructuredTool.from_function(func=act, name="sandbox_browser_act", description="t"),
        layer="spoke", enforce=False)


# --- repetition limit ---------------------------------------------------------

def test_a_spoke_asked_too_often_is_refused_even_when_every_call_succeeded(monkeypatch):
    from prax.agent.spokes import _runner

    monkeypatch.setattr(prax_settings.settings, "spoke_call_limit", 3)
    state = gov.begin_turn()
    state.spoke_calls["browser"] = 2
    assert _runner._failure_limit_refusal("browser") == ""
    state.spoke_calls["browser"] = 3
    refusal = _runner._failure_limit_refusal("browser")
    assert "already been asked 3 times" in refusal
    assert "what you actually observed (not what the agent claimed)" in refusal
    assert _runner._failure_limit_refusal("sandbox") == ""


def test_run_spoke_counts_each_delegation(monkeypatch):
    from prax.agent.spokes import _runner

    monkeypatch.setattr(prax_settings.settings, "spoke_call_limit", 2)
    state = gov.begin_turn()
    # No tools: run_spoke returns early — but only after counting the call.
    for _ in range(2):
        out = _runner.run_spoke(task="t", system_prompt="s", tools=[], config_key="subagent_browser")
        assert "No tools available" in out
    assert state.spoke_calls["browser"] == 2
    out = _runner.run_spoke(task="t", system_prompt="s", tools=[], config_key="subagent_browser")
    assert out.startswith("Not delegated: the browser agent has already been asked 2 times")
    assert state.spoke_calls["browser"] == 2  # a refused call is not a delegation


def test_no_repetition_limit_by_default():
    from prax.agent.spokes import _runner

    state = gov.begin_turn()
    state.spoke_calls["browser"] = 500
    assert _runner._failure_limit_refusal("browser") == ""


# --- per-request budgets --------------------------------------------------------

def test_over_the_cost_budget_tools_are_refused_with_an_instruction_to_report(monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "turn_budget_usd", 0.50)
    calls: list = []
    tool = _tool(calls)
    state = gov.begin_turn()
    state.graph = _Graph(0.20)
    assert tool.invoke({"value": "a"}) == "ok"
    state.graph.spent = 0.61
    out = tool.invoke({"value": "b"})
    assert out.startswith("BUDGET REACHED — not run.")
    assert "$0.61 of a $0.50 cost budget" in out and "ask whether they want you to continue" in out
    assert calls == ["a"]


def test_a_turn_that_ignores_the_budget_is_ended(monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "turn_budget_usd", 0.50)
    tool = _tool([])
    state = gov.begin_turn()
    state.graph = _Graph(1.0)
    for _ in range(gov._BUDGET_GRACE_CALLS):
        assert tool.invoke({"value": "x"}).startswith("BUDGET REACHED")
    with pytest.raises(TurnBudgetExceeded) as exc:
        tool.invoke({"value": "x"})
    assert isinstance(exc.value, TurnCancelled)  # every stop path handles it
    assert "$1.00 of a $0.50 cost budget" in str(exc.value)


def test_unknown_rates_are_a_lower_bound_and_said_so(monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "turn_budget_usd", 0.50)
    state = gov.begin_turn()
    state.graph = _Graph(0.70, complete=False)
    assert "(at least — some model rates are unknown)" in gov._budget_state(state)


def test_the_time_budget(monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "turn_budget_seconds", 600)
    state = gov.begin_turn()
    state.turn = reg.begin("u1", "please solve this")
    assert gov._budget_state(state) == ""
    state.turn.started -= 26 * 60
    assert gov._budget_state(state) == "26 min 0 s of a 10 min 0 s time budget"


def test_budgets_are_off_by_default():
    state = gov.begin_turn()
    state.graph = _Graph(1000.0)
    state.turn = reg.begin("u1", "x")
    state.turn.started -= 10 * 3600
    assert gov._budget_state(state) == ""
    assert _tool([]).invoke({"value": "x"}) == "ok"


def test_a_fresh_turn_gets_a_fresh_budget(monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "turn_budget_usd", 0.50)
    state = gov.begin_turn()
    state.graph = _Graph(1.0)
    gov._over_budget(state)
    assert state.budget_refusals == 1
    assert gov.begin_turn().budget_refusals == 0


# --- the graph's running cost ------------------------------------------------------

def test_graph_cost_so_far_sums_known_rates_and_flags_unknown(monkeypatch):
    from prax.agent import trace
    from prax.eval import pricing

    monkeypatch.setattr(pricing, "estimate_cost",
                        lambda model, tin, tout: None if model == "mystery" else (tin + tout) / 1000)
    graph = trace.ExecutionGraph("t1")
    graph.add_node(trace.SpanNode(span_id="a", trace_id="t1", name="orchestrator", parent_id=None, spoke_or_category="orchestrator"))
    graph.add_llm_usage("a", "known-model", 100, 50)
    assert graph.cost_so_far() == pytest.approx((0.15, True))
    graph.add_llm_usage("a", "mystery", 10, 10)
    assert graph.cost_so_far() == pytest.approx((0.15, False))
