"""Pre-registered kill conditions: the refutation standard is written first.

The cheapest place an authored evaluation degrades is not the metric but the
interpretation — a disappointing result becomes "inconclusive" when nobody wrote
down beforehand that it counts as disproof. These tests pin the properties that
make pre-registration worth anything: it exists before the result, it cannot be
empty, and the verdict comes from the registered condition rather than from
whoever is looking at the numbers.
"""
from __future__ import annotations

import pytest

from prax.eval import prereg


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAX_EVAL_DIR", str(tmp_path))


def test_a_disappointing_result_is_killed_by_the_words_written_earlier():
    reg = prereg.register(
        experiment="flag_x",
        hypothesis="flag_x improves private avg at equal cost",
        kill_condition="private delta < 0.02 or cost ratio > 1.05",
        kill=lambda r: r["private_delta"] < 0.02 or r["cost_ratio"] > 1.05,
    )
    verdict = reg.judge({"private_delta": 0.01, "cost_ratio": 1.0})
    assert verdict.killed is True
    assert "KILLED" in verdict.summary()


def test_a_passing_result_survives():
    reg = prereg.register(
        experiment="flag_y", hypothesis="h",
        kill_condition="delta < 0",
        kill=lambda r: r["delta"] < 0,
    )
    assert reg.judge({"delta": 0.5}).killed is False


def test_nothing_would_change_my_mind_is_refused():
    """An experiment with no possible disproof is a decision in a lab coat."""
    with pytest.raises(ValueError, match="kill condition"):
        prereg.register(experiment="e", hypothesis="h",
                        kill_condition="   ", kill=lambda r: False)


def test_the_registration_is_persisted_before_any_result_exists(tmp_path):
    prereg.register(experiment="early", hypothesis="h",
                    kill_condition="x < 1", kill=lambda r: r["x"] < 1)

    entries = prereg.history("early")
    assert entries and entries[0]["event"] == "registered"
    assert "judged" not in {e["event"] for e in entries}, (
        "nothing has been judged yet — the registration alone must be on disk")


def test_the_verdict_is_recorded_against_the_registration(tmp_path):
    reg = prereg.register(experiment="both", hypothesis="h",
                          kill_condition="x < 1", kill=lambda r: r["x"] < 1)
    reg.judge({"x": 0})

    events = [e["event"] for e in prereg.history("both")]
    assert events == ["registered", "judged"]
    judged = prereg.history("both")[-1]
    assert judged["killed"] is True
    assert judged["kill_condition"] == "x < 1", (
        "the verdict must carry the words written before the result")
