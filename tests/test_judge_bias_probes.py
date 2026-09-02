"""Bias probes for the LLM-judge surfaces (task #36, the bias half).

Every probe is driven by a SYNTHETIC judge with a known, deliberate bias, and
each is asserted in BOTH directions — it fires on a biased judge and stays quiet
on a fair one. A probe that only ever fires is a broken alarm, and a probe that
never fires is worse: it publishes "no bias detected" while measuring nothing.
"""
from __future__ import annotations

import json

import pytest

from prax.eval.goldens import Golden, RubricCriterion
from prax.eval.judge_bias import (
    ProbeResult,
    pad_with_filler,
    probe_criterion_order,
    probe_length_bias,
    probe_self_preference,
    probe_verdict_drift,
    run_bias_audit,
)


def _golden(*, verify_key: bool = False) -> Golden:
    rubric = [
        RubricCriterion(key="depth", weight=1.0, description="Goes into depth."),
        RubricCriterion(key="sources", weight=1.0, description="Cites sources."),
        RubricCriterion(key="critique", weight=1.0, description="Self-critiques."),
    ]
    if verify_key:
        rubric.append(RubricCriterion(key="has_num", weight=1.0,
                                      description="Contains a number.", verify=r"\d+"))
    return Golden(id="g1", title="T", kind="research", prompt="Explain X.", rubric=rubric)


def _reply(scores: dict) -> str:
    return json.dumps({"scores": scores, "reasoning": "ok"})


def _answer_of(prompt: str) -> str:
    """The answer section of a judge prompt — what a length-biased judge reacts to."""
    return prompt.split("## The assistant's answer", 1)[-1]


def _keys_in(prompt: str, golden: Golden) -> list[str]:
    """Criterion keys in the order they appear in the judge prompt."""
    seen = []
    for line in prompt.splitlines():
        for c in golden.rubric:
            if line.strip().startswith(f"- {c.key} ") and c.key not in seen:
                seen.append(c.key)
    return seen


# ---------------------------------------------------------------------------
# Length bias
# ---------------------------------------------------------------------------

def test_length_bias_fires_on_a_judge_that_rewards_length():
    g = _golden()

    def judge(prompt: str) -> str:
        # Grades purely on how long the ANSWER is — the bias under test.
        long = len(_answer_of(prompt)) > 200
        return _reply({c.key: (1 if long else 0) for c in g.rubric})

    r = probe_length_bias(g, "Short answer.", judge=judge)
    assert r.flagged
    assert r.effect > 0
    assert r.detail["padded_chars"] > r.detail["terse_chars"]


def test_length_bias_quiet_on_a_content_only_judge():
    g = _golden()

    def judge(prompt: str) -> str:
        # Depends only on whether the substantive phrase is present.
        hit = "Kepler" in _answer_of(prompt)
        return _reply({c.key: (1 if hit else 0) for c in g.rubric})

    r = probe_length_bias(g, "Kepler measured it.", judge=judge)
    assert not r.flagged
    assert r.effect == 0.0


def test_length_bias_voids_itself_when_padding_moves_a_verified_criterion():
    """The probe's precondition: filler must add no gradeable content."""
    g = _golden(verify_key=True)

    def judge(prompt: str) -> str:
        return _reply({c.key: 1 for c in g.rubric if not c.verify})

    # Padding that smuggles in a number satisfies the `has_num` verify regex.
    r = probe_length_bias(g, "No digits here.", judge=judge,
                          pad=lambda t: t + "\nAdditionally, there were 42 of them.")
    assert r.void
    assert "has_num" in r.void
    assert not r.flagged  # a void probe is never reported as clean OR biased


def test_filler_carries_no_digits_or_citations():
    """INVARIANT the default padding rests on: length only, never substance."""
    padded = pad_with_filler("Base answer.", factor=4)
    added = padded.replace("Base answer.", "")
    assert not any(ch.isdigit() for ch in added)
    assert "http" not in added
    assert len(padded) > len("Base answer.")


# ---------------------------------------------------------------------------
# Verdict drift
# ---------------------------------------------------------------------------

def test_verdict_drift_fires_on_a_nondeterministic_judge():
    g = _golden()
    calls = {"n": 0}

    def judge(prompt: str) -> str:
        calls["n"] += 1
        return _reply({"depth": calls["n"] % 2, "sources": 1, "critique": 1})

    r = probe_verdict_drift(g, "answer", judge=judge, trials=4)
    assert r.flagged
    assert r.detail["unstable_criteria"] == ["depth"]
    assert r.n == 4


def test_verdict_drift_quiet_on_a_deterministic_judge():
    g = _golden()

    def judge(prompt: str) -> str:
        return _reply({"depth": 1, "sources": 0, "critique": 1})

    r = probe_verdict_drift(g, "answer", judge=judge, trials=5)
    assert not r.flagged
    assert r.effect == 0.0
    assert r.detail["total_spread"] == 0.0


def test_verdict_drift_ignores_deterministic_criteria_in_the_denominator():
    """A `verify` criterion cannot drift; counting it would dilute the rate."""
    g = _golden(verify_key=True)
    calls = {"n": 0}

    def judge(prompt: str) -> str:
        calls["n"] += 1
        return _reply({"depth": calls["n"] % 2, "sources": 1, "critique": 1})

    r = probe_verdict_drift(g, "answer 7", judge=judge, trials=4)
    assert "has_num" not in r.detail["judged_criteria"]
    assert r.effect == round(1 / 3, 4)  # 1 of 3 judged, not 1 of 4


# ---------------------------------------------------------------------------
# Criterion order (the position-bias analogue)
# ---------------------------------------------------------------------------

def test_criterion_order_fires_on_a_position_sensitive_judge():
    g = _golden()

    def judge(prompt: str) -> str:
        order = _keys_in(prompt, g)
        # Only whatever is listed FIRST gets credit — pure position bias.
        return _reply({k: (1 if i == 0 else 0) for i, k in enumerate(order)})

    r = probe_criterion_order(g, "answer", judge=judge, orders=3)
    assert r.flagged
    assert r.effect > 0


def test_criterion_order_quiet_on_a_key_addressed_judge():
    g = _golden()

    def judge(prompt: str) -> str:
        return _reply({"depth": 1, "sources": 0, "critique": 1})

    r = probe_criterion_order(g, "answer", judge=judge, orders=3)
    assert not r.flagged
    assert r.effect == 0.0


def test_criterion_order_permutations_preserve_the_criterion_set():
    """INVARIANT: only the ORDER differs — same keys, every time."""
    g = _golden()
    seen: list[list[str]] = []

    def judge(prompt: str) -> str:
        seen.append(_keys_in(prompt, g))
        return _reply({c.key: 1 for c in g.rubric})

    probe_criterion_order(g, "answer", judge=judge, orders=3)
    assert len(seen) == 3
    assert all(sorted(o) == sorted(seen[0]) for o in seen)
    assert len({tuple(o) for o in seen}) == 3  # and the orders are genuinely distinct


def test_criterion_order_voids_on_a_single_criterion_rubric():
    g = Golden(id="g2", title="T", kind="research", prompt="p",
               rubric=[RubricCriterion(key="only", weight=1.0, description="d")])
    r = probe_criterion_order(g, "answer", judge=lambda p: _reply({"only": 1}))
    assert r.void
    assert not r.flagged


# ---------------------------------------------------------------------------
# Self-preference
# ---------------------------------------------------------------------------

def test_self_preference_fires_when_each_judge_favours_its_own_family():
    g = _golden()

    def make_judge(family: str):
        def judge(prompt: str) -> str:
            mine = f"[{family}]" in _answer_of(prompt)
            return _reply({c.key: (1 if mine else 0) for c in g.rubric})
        return judge

    answers = {"alpha": "[alpha] answer", "beta": "[beta] answer"}
    judges = {"alpha": make_judge("alpha"), "beta": make_judge("beta")}
    r = probe_self_preference(g, answers, judges)
    assert r.flagged
    assert r.effect == pytest.approx(1.0)
    assert r.detail["grid"]["alpha"]["alpha"] > r.detail["grid"]["beta"]["alpha"]


def test_self_preference_quiet_on_family_blind_judges():
    g = _golden()

    def judge(prompt: str) -> str:
        return _reply({c.key: 1 for c in g.rubric})

    answers = {"alpha": "[alpha] a", "beta": "[beta] b"}
    r = probe_self_preference(g, answers, {"alpha": judge, "beta": judge})
    assert not r.flagged
    assert r.effect == 0.0


def test_self_preference_grades_the_full_grid():
    """INVARIANT: every judge grades every answer — no missing cell."""
    g = _golden()
    seen = []

    def make_judge(fam):
        def judge(prompt):
            seen.append(fam)
            return _reply({c.key: 1 for c in g.rubric})
        return judge

    answers = {"a": "one", "b": "two", "c": "three"}
    judges = {k: make_judge(k) for k in answers}
    r = probe_self_preference(g, answers, judges)
    assert len(seen) == 9
    assert r.n == 9
    assert all(len(row) == 3 for row in r.detail["grid"].values())


def test_self_preference_voids_without_two_shared_families():
    """Disagreement between one judge and another is not self-preference."""
    g = _golden()
    j = lambda p: _reply({c.key: 1 for c in g.rubric})  # noqa: E731
    r = probe_self_preference(g, {"alpha": "a"}, {"alpha": j, "beta": j})
    assert r.void
    assert not r.flagged


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def test_run_bias_audit_reports_a_skipped_probe_rather_than_omitting_it():
    """"Not measured" must be distinguishable from "no bias found"."""
    g = _golden()
    out = run_bias_audit(g, "answer", judge=lambda p: _reply({c.key: 1 for c in g.rubric}),
                         trials=2, orders=2)
    assert "self_preference" not in out["results"]
    assert out["skipped"] and "self_preference" in out["skipped"][0]
    assert set(out["results"]) == {"length_bias", "verdict_drift", "criterion_order"}
    assert out["flagged"] == []


def test_run_bias_audit_collects_flags_from_every_probe():
    g = _golden()
    calls = {"n": 0}

    def judge(prompt: str) -> str:
        calls["n"] += 1
        long = len(_answer_of(prompt)) > 200
        return _reply({"depth": calls["n"] % 2, "sources": int(long), "critique": 1})

    out = run_bias_audit(g, "Short.", judge=judge, trials=4, orders=2)
    assert "verdict_drift" in out["flagged"]
    assert all(isinstance(r, ProbeResult) for r in out["results"].values())
    assert len(out["summary"]) == 3


def test_probe_result_summary_always_carries_n():
    """An effect size without an n is the MATRIX.md presentation defect."""
    r = ProbeResult(probe="p", n=7, effect=0.2, threshold=0.05)
    assert "n=7" in r.summary()
    assert "BIAS DETECTED" in r.summary()


# ---------------------------------------------------------------------------
# Baseline saturation — a null is only evidence when the test had power
# ---------------------------------------------------------------------------

def test_drift_at_the_floor_is_reported_as_low_power_not_clean():
    """A judge that scores everything 0 is never near a decision boundary.

    Found in the first real run: the answer scored 0/5 on every criterion and all
    three probes returned exactly +0.000. Publishing that as "no bias detected"
    would claim evidence the run does not contain.
    """
    g = _golden()

    def judge(prompt: str) -> str:
        return _reply({c.key: 0 for c in g.rubric})

    r = probe_verdict_drift(g, "answer", judge=judge, trials=3)
    assert not r.flagged
    assert r.low_power
    assert "floor" in r.low_power
    assert "LOW POWER" in r.summary()


def test_drift_at_the_ceiling_is_reported_as_low_power():
    g = _golden()

    def judge(prompt: str) -> str:
        return _reply({c.key: 1 for c in g.rubric})

    r = probe_verdict_drift(g, "answer", judge=judge, trials=3)
    assert r.low_power
    assert "ceiling" in r.low_power


def test_mid_range_baseline_is_not_low_power():
    """Power exists where the judge is actually discriminating."""
    g = _golden()

    def judge(prompt: str) -> str:
        return _reply({"depth": 1, "sources": 0, "critique": 1})

    r = probe_verdict_drift(g, "answer", judge=judge, trials=3)
    assert not r.low_power
    assert "within threshold" in r.summary()


def test_length_bias_low_power_only_at_the_CEILING():
    """A 0.0 baseline still permits the upward effect the hypothesis predicts."""
    g = _golden()
    floor = probe_length_bias(g, "a", judge=lambda p: _reply({c.key: 0 for c in g.rubric}))
    ceil = probe_length_bias(g, "a", judge=lambda p: _reply({c.key: 1 for c in g.rubric}))
    assert not floor.low_power  # padding could still push it up — the test has power
    assert ceil.low_power and "ceiling" in ceil.low_power


def test_criterion_order_reports_saturation_and_keeps_its_totals():
    g = _golden()
    r = probe_criterion_order(g, "answer", orders=2,
                              judge=lambda p: _reply({c.key: 0 for c in g.rubric}))
    assert r.low_power
    assert r.detail["totals"] == [0.0, 0.0]


def test_self_preference_low_power_when_every_cell_is_identical():
    g = _golden()
    j = lambda p: _reply({c.key: 1 for c in g.rubric})  # noqa: E731
    r = probe_self_preference(g, {"a": "x", "b": "y"}, {"a": j, "b": j})
    assert r.effect == 0.0
    assert r.low_power  # every judge said 1 to everything — nothing could differ


def test_a_flagged_probe_is_never_downgraded_to_low_power():
    """Saturation explains a null; it must not soften a positive finding."""
    g = _golden()
    calls = {"n": 0}

    def judge(prompt: str) -> str:
        calls["n"] += 1
        return _reply({"depth": calls["n"] % 2, "sources": 0, "critique": 0})

    r = probe_verdict_drift(g, "answer", judge=judge, trials=4)
    assert r.flagged
    assert "BIAS DETECTED" in r.summary()


def test_run_bias_audit_lists_low_power_probes():
    g = _golden()
    out = run_bias_audit(g, "answer", judge=lambda p: _reply({c.key: 0 for c in g.rubric}),
                         trials=2, orders=2)
    assert out["flagged"] == []
    assert "verdict_drift" in out["low_power"]
    assert "criterion_order" in out["low_power"]


def test_as_record_keeps_the_derived_verdict_that_asdict_drops():
    """`dataclasses.asdict` omits computed fields — a record built from it looks
    complete and has no verdict in it. This crashed the first campaign's summary
    after every golden had already been graded."""
    from dataclasses import asdict

    r = ProbeResult(probe="p", n=3, effect=0.5, threshold=0.05)
    assert "flagged" not in asdict(r)
    rec = r.as_record()
    assert rec["flagged"] is True
    assert rec["effect"] == 0.5 and rec["n"] == 3
    assert "BIAS DETECTED" in rec["summary"]


def test_as_record_carries_low_power_and_void_through():
    r = ProbeResult(probe="p", n=2, effect=0.0, threshold=0.0,
                    low_power="floor", void="")
    rec = r.as_record()
    assert rec["low_power"] == "floor" and rec["flagged"] is False
    assert "LOW POWER" in rec["summary"]
