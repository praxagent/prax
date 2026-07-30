"""A trace can answer "what did this turn cost".

Token usage flowed to Prometheus and OTel and stopped there — the trace, the
artifact a person actually opens, never carried it. Prometheus aggregates
cannot be joined back to one conversation after the fact; per-span attribution
at call time is the only place this join is cheap.
"""
from __future__ import annotations

from prax.agent import trace as tr


def _graph_with_usage(model="gpt-5.4-mini", tin=1000, tout=500):
    span = tr.start_span("orchestrator", "orchestrator")
    span.ctx.graph.add_llm_usage(span.span_id, model, tin, tout)
    span.end()
    return span.ctx.graph.to_dict(), span.span_id


def test_usage_lands_on_the_span_and_the_trace():
    d, sid = _graph_with_usage()
    node = next(n for n in d["nodes"] if n["span_id"] == sid)
    assert node["tokens_in"] == 1000 and node["tokens_out"] == 500
    assert node["llm_calls"] == 1
    assert d["tokens_in"] == 1000 and d["tokens_out"] == 500


def test_known_model_gets_a_cost_estimate():
    d, sid = _graph_with_usage(model="gpt-5.4-mini")
    node = next(n for n in d["nodes"] if n["span_id"] == sid)
    # The exact figure tracks the price table; the claim under test is that a
    # known model yields a number, not which number.
    assert isinstance(node["cost_estimate_usd"], float)
    assert node["cost_estimate_usd"] >= 0
    assert isinstance(d["cost_estimate_usd"], float)


def test_unknown_model_reports_none_not_zero():
    """Unknown and free are different claims."""
    d, sid = _graph_with_usage(model="some/model-nobody-priced")
    node = next(n for n in d["nodes"] if n["span_id"] == sid)
    assert node["cost_estimate_usd"] is None
    assert d["cost_estimate_usd"] is None


def test_partial_pricing_is_treated_as_no_pricing():
    """Summing known models and dropping unknown ones LOOKS complete and is
    quietly smaller than the truth — the laundering mistake in miniature."""
    span = tr.start_span("orchestrator", "orchestrator")
    span.ctx.graph.add_llm_usage(span.span_id, "gpt-5.4-mini", 1000, 100)
    span.ctx.graph.add_llm_usage(span.span_id, "mystery/unpriced", 999999, 999999)
    span.end()
    d = span.ctx.graph.to_dict()
    assert d["cost_estimate_usd"] is None


def test_usage_accumulates_across_calls():
    span = tr.start_span("orchestrator", "orchestrator")
    span.ctx.graph.add_llm_usage(span.span_id, "gpt-5.4-mini", 100, 10)
    span.ctx.graph.add_llm_usage(span.span_id, "gpt-5.4-mini", 200, 20)
    span.end()
    node = span.ctx.graph.to_dict()["nodes"][0]
    assert node["llm_calls"] == 2
    assert node["tokens_in"] == 300 and node["tokens_out"] == 30


def test_usage_for_a_finished_or_unknown_span_is_dropped_silently():
    # Accounting must never break a call path; a late callback is a no-op.
    span = tr.start_span("orchestrator", "orchestrator")
    span.ctx.graph.add_llm_usage("nonexistent-span", "m", 5, 5)
    span.end()
    assert span.ctx.graph.to_dict()["tokens_in"] == 0
