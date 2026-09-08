"""The self-regen accept gate requires a REAL margin and a REPLICATE.

Before this, `_gate_on_private_holdout` ran the golden suite once per arm and
`accept_change` adopted any positive private delta; the capability keep-rule
was a bare 0.02 fraction. The judge flips ~30% of criteria on an identical
re-grade at temperature 0 (docs/research/judge-noise-floor.md), so a single
baseline run against a single candidate run cannot tell an improvement from
re-running the baseline. Now:

- each arm runs >= 2 replicates and the accept threshold is the MEASURED
  baseline-vs-replicate spread (the larger of the two arms);
- the capability keep-rule is expressed in CASES (>= 1 case of the suite);
- PROPOSAL.md carries n_private, the noise floor and the tuning-vs-held-out gap.

Keyless: fake evaluators and a fake golden suite that yields scripted values.
"""
from __future__ import annotations

import prax.eval.goldens as g
from prax.eval import self_regen


def _scripted_suite(values):
    """A fake run_golden_suite yielding scripted (private, public) pairs in call
    order: baseline replicates first, then candidate replicates."""
    it = iter(values)

    def fake(**kw):
        priv, pub = next(it)
        return {"avg_private": priv, "avg_public": pub, "n_private": 2, "n_public": 6}
    return fake


def _install(monkeypatch, values):
    calls = {"n": 0}
    inner = _scripted_suite(values)

    def counting(**kw):
        calls["n"] += 1
        return inner(**kw)

    monkeypatch.setattr(g, "run_golden_suite", counting)
    monkeypatch.setattr(self_regen, "_base_system_prompt", lambda: "BASE")
    return calls


# --------------------------------------------------------------------------- #
# aggregate_replicates — the pure fold
# --------------------------------------------------------------------------- #

class TestAggregateReplicates:
    def test_mean_spread_and_min_n(self):
        agg = g.aggregate_replicates([
            {"avg_private": 0.50, "avg_public": 0.70, "n_private": 2, "n_public": 6},
            {"avg_private": 0.60, "avg_public": 0.72, "n_private": 1, "n_public": 6},
        ])
        assert agg["avg_private"] == 0.55 and agg["private_spread"] == 0.1
        assert agg["avg_public"] == 0.71 and agg["public_spread"] == 0.02
        assert agg["n_private"] == 1 and agg["replicates"] == 2
        assert agg["private_values"] == [0.5, 0.6]

    def test_a_replicate_without_a_holdout_fails_closed(self):
        agg = g.aggregate_replicates([
            {"avg_private": 0.5, "avg_public": 0.7, "n_private": 2},
            {"avg_private": None, "avg_public": 0.7, "n_private": 0},
        ])
        assert agg["avg_private"] is None
        verdict = g.accept_change(agg, agg)
        assert verdict["accept"] is False and "fail-closed" in verdict["reason"]


# --------------------------------------------------------------------------- #
# accept_change with a measured noise floor
# --------------------------------------------------------------------------- #

class TestAcceptChangeNoiseFloor:
    def test_delta_inside_the_floor_is_rejected(self):
        d = g.accept_change({"avg_private": 0.55, "avg_public": 0.5, "n_private": 2},
                            {"avg_private": 0.61, "avg_public": 0.6, "n_private": 2},
                            noise_floor=0.10)
        assert d["accept"] is False                          # old rule: +0.06 > 0 → accept
        assert d["noise_floor"] == 0.1 and "noise floor" in d["reason"]
        assert d["private_delta"] == 0.06 and d["n_private"] == 2

    def test_delta_beyond_the_floor_is_accepted_and_gap_reported(self):
        d = g.accept_change({"avg_private": 0.51, "avg_public": 0.5, "n_private": 2},
                            {"avg_private": 0.79, "avg_public": 0.9, "n_private": 2},
                            noise_floor=0.02)
        assert d["accept"] is True
        assert d["private_delta"] == 0.28 and d["public_delta"] == 0.4
        assert d["generalization_gap"] == 0.12               # public gain that did not transfer

    def test_zero_floor_is_the_pure_rule(self):
        # Callers with no replicate keep the previous behaviour exactly.
        d = g.accept_change({"avg_private": 0.5, "avg_public": 0.5},
                            {"avg_private": 0.51, "avg_public": 0.5})
        assert d["accept"] is True and d["noise_floor"] == 0.0


# --------------------------------------------------------------------------- #
# _gate_on_private_holdout — replicated, threshold measured
# --------------------------------------------------------------------------- #

class TestGateReplicates:
    def test_each_arm_runs_at_least_twice_even_if_asked_for_one(self, monkeypatch):
        calls = _install(monkeypatch, [(0.5, 0.5)] * 4)
        out = self_regen._gate_on_private_holdout("p", tier="low", replicates=1)
        assert calls["n"] == 4                               # old gate: 2
        assert out["replicates"] == 2

    def test_candidate_within_the_replicate_spread_is_rejected(self, monkeypatch):
        # Baseline replicates disagree by 0.10; the candidate's mean gain is 0.06.
        _install(monkeypatch, [(0.50, 0.5), (0.60, 0.5),     # baseline
                               (0.60, 0.6), (0.62, 0.6)])    # candidate
        out = self_regen._gate_on_private_holdout("p", tier="low")
        assert out["accept"] is False                        # old single-run gate: 0.60 > 0.50 → accept
        assert out["noise_floor"] == 0.1
        assert out["private_delta"] == 0.06
        assert out["baseline_private_values"] == [0.5, 0.6]
        assert out["candidate_private_values"] == [0.6, 0.62]

    def test_candidate_beyond_the_spread_is_accepted(self, monkeypatch):
        _install(monkeypatch, [(0.50, 0.5), (0.52, 0.5),
                               (0.80, 0.7), (0.78, 0.7)])
        out = self_regen._gate_on_private_holdout("p", tier="low")
        assert out["accept"] is True
        assert out["noise_floor"] == 0.02 and out["private_delta"] == 0.28
        assert out["n_private"] == 2


# --------------------------------------------------------------------------- #
# run_self_regen end to end: margin in cases + gate + PROPOSAL.md
# --------------------------------------------------------------------------- #

def _loop(tmp_path, *, scores: dict, patches: list[str], **kw):
    it = iter(patches)
    return self_regen.run_self_regen(
        rounds=len(patches), weak_signal="x", out_dir=tmp_path,
        proposer=lambda _s: next(it),
        evaluator=lambda p: scores[p],
        auditor=lambda _p: (True, "ok"),
        **kw,
    )


class TestLoopMarginInCases:
    def test_default_margin_is_one_case_of_the_suite(self, tmp_path):
        n = self_regen._capability_case_count()
        assert n > 1
        half, two = 0.5 + 0.5 / n, 0.5 + 2 / n
        summary = _loop(tmp_path, scores={"": 0.5, "half-a-case": half, "two-cases": two},
                        patches=["half-a-case", "two-cases"], gate=False)
        assert summary["margin"] == round(1 / n, 4) and summary["margin_cases"] == 1
        assert summary["n_capability_cases"] == n
        kept = {v["patch"]: v["kept"] for v in summary["variants"]}
        assert kept["half-a-case"] is False                  # old default 0.02 < 0.5/30 → kept
        assert kept["two-cases"] is True
        assert summary["best_patch"] == "two-cases"

    def test_explicit_fraction_still_overrides(self, tmp_path):
        summary = _loop(tmp_path, scores={"": 0.5, "p": 0.53}, patches=["p"],
                        min_margin=0.02, gate=False)
        assert summary["margin"] == 0.02 and summary["margin_cases"] is None
        assert summary["best_patch"] == "p"


class TestLoopGateEndToEnd:
    def test_winner_within_noise_is_rejected_and_the_proposal_says_why(self, tmp_path, monkeypatch):
        n = self_regen._capability_case_count()
        _install(monkeypatch, [(0.50, 0.5), (0.60, 0.5), (0.60, 0.7), (0.62, 0.7)])
        summary = _loop(tmp_path, scores={"": 0.5, "win": 0.5 + 2 / n}, patches=["win"])
        assert summary["gate"]["accept"] is False
        assert summary["best_patch"] == "" and summary["applied"] is False
        proposal = (tmp_path / "PROPOSAL.md").read_text()
        assert "held-out gate rejected" in proposal
        assert "n_private: 2" in proposal
        assert "noise floor" in proposal and "+0.100" in proposal
        assert "tuning-vs-held-out gap" in proposal

    def test_winner_beyond_noise_with_a_case_of_gain_is_accepted(self, tmp_path, monkeypatch):
        n = self_regen._capability_case_count()
        _install(monkeypatch, [(0.50, 0.5), (0.52, 0.5), (0.80, 0.9), (0.78, 0.9)])
        summary = _loop(tmp_path, scores={"": 0.5, "win": 0.5 + 2 / n}, patches=["win"])
        gate = summary["gate"]
        assert gate["accept"] is True and summary["best_patch"] == "win"
        assert gate["replicates"] == 2 and gate["noise_floor"] == 0.02
        assert gate["n_private"] == 2 and gate["generalization_gap"] == 0.12
        proposal = (tmp_path / "PROPOSAL.md").read_text()
        assert "verdict: ACCEPT" in proposal
        assert "n_private: 2" in proposal and "replicates per arm: 2" in proposal
        assert "noise floor" in proposal and "+0.020" in proposal
        assert "tuning-vs-held-out gap: +0.120" in proposal
        assert summary["applied"] is False                   # propose-only by default
