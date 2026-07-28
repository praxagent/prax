"""A trace must remember which model answered.

`models_used` was empty on every node of every trace. The data was being
recorded — `build_llm` files a tier choice — but tagged with whatever span was
open at the time, and ConversationAgent resolves its LLM in `__init__` and is
then reused across turns. So the choice was filed under `span_id=None`, every
span filtered it out as "not mine", and the trace threw away the one fact it had.

Which made the model badge on a message permanently blank: the feature read a
field that could never be populated.
"""
from __future__ import annotations

import pytest

from prax.agent import trace as tr


@pytest.fixture(autouse=True)
def _clean_log():
    from prax.agent import llm_factory

    with llm_factory._tier_lock:
        llm_factory._tier_choice_log.clear()
    yield
    with llm_factory._tier_lock:
        llm_factory._tier_choice_log.clear()


def _record(**kw):
    from prax.agent import llm_factory

    llm_factory._record_tier_choice(
        tier_requested=kw.get("tier", "medium"),
        tier_resolved=kw.get("tier", "medium"),
        model=kw["model"],
        provider="openai",
        span_id=kw.get("span_id"),
        span_name=kw.get("span_name"),
    )


def test_the_root_span_claims_a_model_chosen_before_any_span_opened():
    """The normal case: the agent's LLM is built once, outside a span."""
    _record(model="gpt-5.4")               # span_id=None, as build_llm files it

    span = tr.start_span("orchestrator", "orchestrator")
    try:
        assert span.ctx.parent_id is None, "this test needs a root span"
    finally:
        span.end()

    node = span.ctx.graph.to_dict()["nodes"][0]
    assert [m["model"] for m in node["models_used"]] == ["gpt-5.4"]


def test_a_child_span_does_not_steal_an_unowned_choice():
    """Attributing it to a random concurrent child would be a guess."""
    _record(model="gpt-5.4")

    root = tr.start_span("orchestrator", "orchestrator")
    child = tr.start_span("some_tool", "tool")
    child.end()
    child_node = next(n for n in root.ctx.graph.to_dict()["nodes"]
                      if n["span_id"] == child.span_id)
    assert child_node["models_used"] == []

    root.end()
    root_node = next(n for n in root.ctx.graph.to_dict()["nodes"]
                     if n["span_id"] == root.span_id)
    assert [m["model"] for m in root_node["models_used"]] == ["gpt-5.4"]


def test_a_choice_tagged_with_a_span_still_goes_to_that_span():
    root = tr.start_span("orchestrator", "orchestrator")
    child = tr.start_span("delegate_research", "tool")
    _record(model="claude-x", span_id=child.span_id)
    child.end()

    node = next(n for n in root.ctx.graph.to_dict()["nodes"]
                if n["span_id"] == child.span_id)
    assert [m["model"] for m in node["models_used"]] == ["claude-x"]
    root.end()


def test_the_reported_model_reaches_the_message_metadata(monkeypatch):
    """End to end: what the badge on a message actually reads."""
    from prax.blueprints import teamwork_routes as twr

    _record(model="gpt-5.4")
    span = tr.start_span("orchestrator", "orchestrator")
    span.end()

    monkeypatch.setattr("prax.agent.trace.get_last_completed_graph",
                        lambda: span.ctx.graph)
    assert twr._models_from_trace() == ["gpt-5.4"]
