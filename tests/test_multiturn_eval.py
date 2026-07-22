"""Keyless tests for the multi-turn eval framework (prax.eval.multiturn).

The agent and user simulator are injected stubs, so the conversation loop,
deterministic grading, pass^k, and YAML loading are all proven with zero API keys.
"""
from __future__ import annotations

from prax.eval.capability import CapCheck
from prax.eval.multiturn import (
    DONE_SIGNAL,
    AgentReply,
    MultiTurnCase,
    grade_conversation,
    load_multiturn_cases,
    pass_hat_k,
    run_conversation,
)


def _scripted_agent(replies):
    """Agent that returns canned AgentReplies in order (last repeats)."""
    calls = {"i": 0}

    def _a(history):
        r = replies[min(calls["i"], len(replies) - 1)]
        calls["i"] += 1
        return r if isinstance(r, AgentReply) else AgentReply(content=r)
    return _a


def _scripted_user(messages):
    """User sim that emits canned messages in order, then DONE_SIGNAL."""
    def _u(case, history):
        idx = sum(1 for t in history if t.role == "user") - 1  # -1 for the opening
        return messages[idx] if idx < len(messages) else DONE_SIGNAL
    return _u


def _case(checks, max_turns=6):
    return MultiTurnCase(id="c", persona="p", opening="hi", checks=checks, max_turns=max_turns)


def test_run_conversation_alternates_and_records():
    case = _case([])
    tr = run_conversation(case, _scripted_agent(["a1", "a2"]), _scripted_user(["u1"]))
    roles = [t.role for t in tr.turns]
    assert roles == ["user", "assistant", "user", "assistant"]  # hi, a1, u1, a2
    assert tr.assistant_text() == "a1\na2"
    assert tr.agent_turns() == 2


def test_done_signal_ends_early():
    case = _case([], max_turns=10)
    # user says DONE right after the first agent reply
    tr = run_conversation(case, _scripted_agent(["a1", "a2", "a3"]),
                          lambda c, h: DONE_SIGNAL)
    assert tr.agent_turns() == 1  # stopped after the first reply


def test_max_turns_bounds_the_loop():
    case = _case([], max_turns=3)
    tr = run_conversation(case, _scripted_agent(["x"]), _scripted_user(["u1", "u2", "u3", "u4"]))
    assert tr.agent_turns() == 3  # never exceeds max_turns


def test_grade_passes_when_all_checks_satisfied():
    case = _case([CapCheck("contains", "paris"), CapCheck("absent", "london")])
    tr = run_conversation(case, _scripted_agent(["The capital is Paris."]),
                          lambda c, h: DONE_SIGNAL)
    g = grade_conversation(case, tr)
    assert g["passed"] is True and g["total"] == 1.0


def test_grade_fails_on_missing_check():
    case = _case([CapCheck("contains", "paris"), CapCheck("contains", "eiffel")])
    tr = run_conversation(case, _scripted_agent(["Paris."]), lambda c, h: DONE_SIGNAL)
    g = grade_conversation(case, tr)
    assert g["passed"] is False and g["total"] == 0.5


def test_grade_fails_on_executor_error():
    case = _case([CapCheck("contains", "ok")])

    def _boom(history):
        raise RuntimeError("model down")
    tr = run_conversation(case, _boom, lambda c, h: DONE_SIGNAL)
    g = grade_conversation(case, tr)
    assert g["passed"] is False and "model down" in g["error"]


def test_checks_grade_tools_and_spokes_across_turns():
    case = _case([CapCheck("tool", "note_create"), CapCheck("spoke", "knowledge")])
    agent = _scripted_agent([
        AgentReply(content="working", tools=["web_search"]),
        AgentReply(content="saved", tools=["note_create"], spokes=["knowledge"]),
    ])
    tr = run_conversation(case, agent, _scripted_user(["and save it"]))
    g = grade_conversation(case, tr)
    assert g["passed"] is True  # tool + spoke seen anywhere in the conversation


def test_pass_hat_k_all_pass():
    case = _case([CapCheck("contains", "paris")])
    res = pass_hat_k(case, _scripted_agent(["Paris"]), lambda c, h: DONE_SIGNAL, k=3)
    assert res["pass_hat_k"] == 1.0 and res["trial_pass_rate"] == 1.0 and res["k"] == 3


def test_pass_hat_k_flaky_case_scores_zero_but_shows_rate():
    """An agent that passes 2 of 3 trials → pass^k=0 but trial_pass_rate≈0.67 —
    exactly the reliability gap single-shot accuracy hides."""
    case = _case([CapCheck("contains", "yes")])
    state = {"n": 0}

    def _flaky(history):
        state["n"] += 1
        return AgentReply(content="yes" if state["n"] != 3 else "no")  # 3rd trial fails
    res = pass_hat_k(case, _flaky, lambda c, h: DONE_SIGNAL, k=3)
    assert res["pass_hat_k"] == 0.0
    assert res["trial_pass_rate"] == round(2 / 3, 3)


def test_load_seed_cases_present_and_valid():
    cases = load_multiturn_cases()
    assert len(cases) >= 2
    for c in cases:
        assert c.id and c.persona and c.opening and c.checks
        assert c.max_turns >= 1


def test_run_suite_with_stubs_aggregates_pass_hat_k():
    from prax.eval.multiturn import run_multiturn_suite
    ca, cb = _case([CapCheck("contains", "ok")]), _case([CapCheck("contains", "nope")])
    ca.id, cb.id = "a", "b"
    res = run_multiturn_suite(
        cases=[ca, cb], k=2,
        agent_factory=lambda c: _scripted_agent(["ok"]),   # always "ok"
        user_sim=lambda c, h: DONE_SIGNAL,
    )
    assert res["aggregate"]["cases"] == 2
    assert res["aggregate"]["pass_hat_k"] == 0.5  # a passes (1.0), b fails (0.0)
    assert {r["id"] for r in res["results"]} == {"a", "b"}
