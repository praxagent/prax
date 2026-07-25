"""The model that answered should be visible on the message it answered.

The trace has recorded this per span all along; nothing carried it to the
message, so the one place a user looks could not say what answered them. It
matters more now a space can pin a model: without this, a pin is unverifiable
from the outside, and an unverifiable setting is one you take on faith.
"""
from __future__ import annotations

from prax.blueprints import teamwork_routes as tr


class _Graph:
    def __init__(self, nodes):
        self._nodes = nodes
        self.trace_id = "abc123"

    def to_dict(self):
        return {"nodes": self._nodes}


def _node(*models):
    return {"models_used": [{"model": m} for m in models]}


def test_the_model_that_did_most_of_the_work_comes_first(monkeypatch):
    # "What answered me?" means the model that carried the turn, not whichever
    # span happens to be first in the graph.
    monkeypatch.setattr(tr, "get_last_completed_graph", lambda: None, raising=False)
    monkeypatch.setattr(
        "prax.agent.trace.get_last_completed_graph",
        lambda: _Graph([_node("cheap"), _node("big"), _node("big"), _node("big")]))
    assert tr._models_from_trace() == ["big", "cheap"]


def test_every_model_used_is_reported_not_just_one(monkeypatch):
    # A turn is not one model — the orchestrator escalates, spokes differ.
    # Collapsing that to a single name would be a tidier lie.
    monkeypatch.setattr(
        "prax.agent.trace.get_last_completed_graph",
        lambda: _Graph([_node("a"), _node("b")]))
    assert set(tr._models_from_trace()) == {"a", "b"}


def test_repeats_within_a_turn_are_not_listed_twice(monkeypatch):
    monkeypatch.setattr(
        "prax.agent.trace.get_last_completed_graph",
        lambda: _Graph([_node("a"), _node("a")]))
    assert tr._models_from_trace() == ["a"]


def test_no_trace_means_no_models_not_an_exception(monkeypatch):
    monkeypatch.setattr("prax.agent.trace.get_last_completed_graph", lambda: None)
    assert tr._models_from_trace() == []


def test_a_broken_trace_costs_the_label_not_the_reply(monkeypatch):
    """Labelling must never be the reason a message fails to arrive."""
    def boom():
        raise RuntimeError("trace store is on fire")

    monkeypatch.setattr("prax.agent.trace.get_last_completed_graph", boom)
    assert tr._models_from_trace() == []


def test_metadata_carries_the_model_alongside_the_trace_id(monkeypatch):
    monkeypatch.setattr("prax.agent.trace.last_root_trace_id",
                        type("V", (), {"get": staticmethod(lambda: "t-1")})())
    monkeypatch.setattr("prax.agent.trace.get_last_completed_graph",
                        lambda: _Graph([_node("gpt-x"), _node("gpt-x"), _node("claude-y")]))
    meta = tr._build_trace_metadata()
    assert meta["trace_id"] == "t-1"
    assert meta["model"] == "gpt-x"
    assert meta["models"] == ["gpt-x", "claude-y"]


def test_a_turn_with_no_recorded_model_omits_the_field(monkeypatch):
    # Better an absent label than an invented one.
    monkeypatch.setattr("prax.agent.trace.last_root_trace_id",
                        type("V", (), {"get": staticmethod(lambda: "t-2")})())
    monkeypatch.setattr("prax.agent.trace.get_last_completed_graph",
                        lambda: _Graph([{"models_used": []}]))
    meta = tr._build_trace_metadata()
    assert "model" not in meta and "models" not in meta
