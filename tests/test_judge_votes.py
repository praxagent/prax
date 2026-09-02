"""k-vote majority grading for rubric judges (task #36).

Pinning JUDGE_TEMPERATURE to 0.0 removed the *configured* randomness but not the
provider's — the 2026-08-20 bias audit re-graded one answer five times at
temperature 0.0 and watched 3 of 5 criteria flip. These tests pin the voting
behaviour, and above all that **votes=1 is byte-for-byte the previous
behaviour**: a scorer change that silently re-baselines every published number
would be worse than the drift it fixes.
"""
from __future__ import annotations

import json

from prax.eval.goldens import Golden, RubricCriterion, score_golden


def _golden() -> Golden:
    return Golden(
        id="g", title="T", kind="research", prompt="p",
        rubric=[
            RubricCriterion(key="a", weight=1.0, description="d"),
            RubricCriterion(key="b", weight=1.0, description="d"),
        ],
    )


def _ballot(**scores) -> str:
    return json.dumps({"scores": scores, "reasoning": "r"})


def test_votes_1_calls_the_judge_once_and_adds_no_vote_metadata():
    """INVARIANT: the default path is unchanged — one call, one verdict."""
    calls = {"n": 0}

    def judge(prompt: str) -> str:
        calls["n"] += 1
        return _ballot(a=1, b=0)

    r = score_golden(_golden(), "out", judge=judge, votes=1)
    assert calls["n"] == 1
    assert r["scores"] == {"a": 1.0, "b": 0.0}
    assert "votes" not in r and "unanimous" not in r


def test_majority_wins_over_a_single_dissenting_ballot():
    seq = [_ballot(a=1, b=1), _ballot(a=0, b=1), _ballot(a=1, b=1)]
    it = iter(seq)
    r = score_golden(_golden(), "out", judge=lambda p: next(it), votes=3)
    assert r["scores"] == {"a": 1.0, "b": 1.0}
    assert r["votes"] == 3 and r["ballots_used"] == 3
    assert r["unanimous"] is False


def test_a_tie_falls_to_zero():
    """A split panel is not a satisfied criterion."""
    seq = [_ballot(a=1), _ballot(a=0)]
    it = iter(seq)
    g = Golden(id="g", title="T", kind="k", prompt="p",
               rubric=[RubricCriterion(key="a", weight=1.0, description="d")])
    r = score_golden(g, "out", judge=lambda p: next(it), votes=2)
    assert r["scores"] == {"a": 0.0}


def test_unanimous_is_reported_when_every_ballot_agrees():
    r = score_golden(_golden(), "out", judge=lambda p: _ballot(a=1, b=0), votes=3)
    assert r["unanimous"] is True
    assert r["scores"] == {"a": 1.0, "b": 0.0}


def test_voting_suppresses_a_drifting_criterion():
    """The whole point: a criterion that flips 1 of 3 times settles on its mode."""
    seq = [_ballot(a=1, b=1), _ballot(a=1, b=0), _ballot(a=1, b=1)]
    it = iter(seq)
    r = score_golden(_golden(), "out", judge=lambda p: next(it), votes=3)
    assert r["scores"]["b"] == 1.0  # the single 0 is outvoted


def test_a_failed_ballot_is_dropped_not_counted_as_zero():
    """A crashed call is missing evidence, never evidence of failure.

    Same fail-closed-vs-fail-silent distinction as the eval error accounting: an
    errored ballot must not become a vote against the criterion.
    """
    state = {"n": 0}

    def judge(prompt: str) -> str:
        state["n"] += 1
        if state["n"] == 2:
            raise RuntimeError("provider 500")
        return _ballot(a=1, b=1)

    r = score_golden(_golden(), "out", judge=judge, votes=3)
    assert r["ballots_used"] == 2
    assert r["scores"] == {"a": 1.0, "b": 1.0}
    assert "error" not in r


def test_an_unparseable_ballot_is_dropped_not_counted_as_zero():
    state = {"n": 0}

    def judge(prompt: str) -> str:
        state["n"] += 1
        return "not json at all" if state["n"] == 1 else _ballot(a=1, b=1)

    r = score_golden(_golden(), "out", judge=judge, votes=3)
    assert r["ballots_used"] == 2
    assert r["scores"]["a"] == 1.0


def test_every_ballot_failing_reports_an_error_not_a_silent_zero():
    def judge(prompt: str) -> str:
        raise RuntimeError("provider down")

    r = score_golden(_golden(), "out", judge=judge, votes=3)
    assert r["error"]
    assert r["total"] == 0.0
    assert r["scores"] == {}


def test_verify_criteria_are_never_voted_on():
    """Deterministic criteria need no ballots — and must not consume any."""
    g = Golden(id="g", title="T", kind="k", prompt="p",
               rubric=[RubricCriterion(key="num", weight=1.0, description="d",
                                       verify=r"\d+")])
    calls = {"n": 0}

    def judge(prompt: str) -> str:
        calls["n"] += 1
        return _ballot()

    r = score_golden(g, "answer 42", judge=judge, votes=5)
    assert calls["n"] == 0  # no judge invoked at all
    assert r["scores"] == {"num": 1.0}


def test_votes_below_one_is_clamped_to_one():
    calls = {"n": 0}

    def judge(prompt: str) -> str:
        calls["n"] += 1
        return _ballot(a=1, b=1)

    score_golden(_golden(), "out", judge=judge, votes=0)
    assert calls["n"] == 1


def test_settings_default_keeps_one_ballot():
    """The shipped default must not change any existing number."""
    from prax.settings import settings
    assert int(getattr(settings, "judge_votes", 1)) == 1
