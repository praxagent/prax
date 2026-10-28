"""The #29 loop's protocol (docs/research/harness-learning.md) — keyless.

- the deterministic spike veto reads the answers inside regex checks;
- ``split``: the proposer sees failed FEEDBACK cases and a change is kept only
  on a disjoint SCORING set, split within each family;
- ``revise_kept``: each round revises the kept overlay with fresh feedback;
- every proposal is recorded in ``revisions.jsonl`` (training data for later).
"""
from __future__ import annotations

import json

import pytest

import prax.eval.self_regen as sr
from prax.eval.capability import CapabilityCase, CapCheck
from prax.eval.case_generators import family_of
from prax.eval.self_regen import (
    Evaluation,
    _deterministic_spike_veto,
    _regex_literal_numbers,
    _spike_answer_tokens,
    run_self_regen,
    split_cases,
)


def _records(out_dir):
    return [json.loads(line) for line in (out_dir / "revisions.jsonl").read_text().splitlines()]


# --------------------------------------------------------------------------
# The spike veto
# --------------------------------------------------------------------------

class TestSpikeVeto:
    def test_regex_checks_contribute_their_answers(self):
        toks = _spike_answer_tokens()
        # computation_aggregate_exact, computation_unit_conversion,
        # honesty_source_disagreement: all checked by regex, all missed before.
        for answer in ("604", "51.5", "388.8", "227"):
            assert answer in toks, answer
        assert {"526", "536"} <= toks  # the contains/absent tokens stay

    def test_counts_and_round_figures_are_not_answers(self):
        toks = _spike_answer_tokens()
        for prose in ("3", "20", "50", "100", "1000", "000"):
            assert prose not in toks, prose

    def test_quantifiers_and_classes_are_not_literals(self):
        assert _regex_literal_numbers(r"(?i)median[^.\n]{0,30}\b(470|561)\b") == {"470", "561"}
        assert _regex_literal_numbers(r"\b\d{4}\b|[0-9]{3}") == set()
        assert _regex_literal_numbers(r"\b51\.50?\b") == {"51.5"}
        assert _regex_literal_numbers(r"(exactly)\s+4,?000|\b3,?9\d\d") == set()

    @pytest.mark.parametrize("patch,vetoed", [
        ("When summing, the total is 604.", True),
        ("A median like 51.50 is fine.", True),
        ("Convert 388.8 cubic metres carefully.", True),
        ("Use 1,604 as an example.", False),      # a different number
        ("Report 16045 records.", False),
        ("Keep answers under 20 lines and 3 sentences.", False),
        ("Show the arithmetic before the result.", False),
    ])
    def test_numbers_match_on_boundaries(self, patch, vetoed):
        toks = {"604", "51.5", "388.8"}
        assert bool(_deterministic_spike_veto(patch, toks)) is vetoed


# --------------------------------------------------------------------------
# The split
# --------------------------------------------------------------------------

def _case(cid: str) -> CapabilityCase:
    return CapabilityCase(id=cid, prompt=f"prompt for {cid}",
                          checks=[CapCheck("contains", "ok")], title=cid)


class TestSplit:
    def test_disjoint_and_within_each_family(self):
        cases = ([_case(f"honesty_{i}") for i in range(12)]
                 + [_case(f"computation_{i}") for i in range(6)]
                 + [_case("summary_only")])
        fb, sc = split_cases(cases, seed=3)
        assert not {c.id for c in fb} & {c.id for c in sc}
        assert len(fb) + len(sc) == len(cases)
        for fam, n in (("honesty", 12), ("computation", 6)):
            f = [c for c in fb if family_of(c.id) == fam]
            s = [c for c in sc if family_of(c.id) == fam]
            assert f and s and len(f) == n // 3 and len(f) + len(s) == n
        # A one-case family is never shown; it still guards against regressions.
        assert "summary_only" in {c.id for c in sc}

    def test_deterministic_in_the_seed(self):
        cases = [_case(f"honesty_{i}") for i in range(12)]
        a, b = split_cases(cases, seed=1), split_cases(cases, seed=1)
        assert [c.id for c in a[0]] == [c.id for c in b[0]]
        assert {c.id for c in split_cases(cases, seed=2)[0]} != {c.id for c in a[0]}

    def test_the_real_pool_splits_every_family_with_two_or_more(self):
        fb, sc = split_cases(sr.case_pool(generated_per_family=8), seed=0)
        fams_fb = {family_of(c.id) for c in fb}
        assert "computation" in fams_fb and "honesty" in fams_fb
        # computation: 4 authored + 8 generated = 12 → 4 shown, 8 scored
        assert sum(family_of(c.id) == "computation" for c in fb) == 4
        assert sum(family_of(c.id) == "computation" for c in sc) == 8


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------

class TestSplitLoop:
    def test_proposer_sees_feedback_and_keep_is_scored_on_the_scoring_set(self, tmp_path):
        seen_inputs, scored, reported = [], [], []

        def evaluator(patch):
            scored.append(patch)
            return {"": 0.5, "general fix": 0.8}.get(patch, 0.5)

        def feedback(patch):
            reported.append(patch)
            return "FEEDBACK-REPORT: task X answered 12 instead of 13"

        summary = run_self_regen(
            rounds=2, split=True, out_dir=tmp_path, gate=False,
            proposer=lambda ctx: seen_inputs.append(ctx) or "general fix",
            evaluator=evaluator, feedback=feedback,
            auditor=lambda _p: (True, "ok"),
        )
        assert all("FEEDBACK-REPORT" in ctx for ctx in seen_inputs)
        # Without revise_kept every round revises from the bare prompt.
        assert reported == [""]
        assert summary["best_patch"] == "general fix"
        assert summary["n_scoring_cases"] and summary["n_feedback_cases"]
        assert summary["n_capability_cases"] == summary["n_scoring_cases"]
        recs = _records(tmp_path)
        assert not set(recs[0]["feedback_case_ids"]) & set(recs[0]["scoring_case_ids"])
        assert recs[0]["template"] == "revision-v1"
        assert recs[0]["mode"] == {"revise_kept": False, "split": True}

    def test_the_default_feedback_runs_only_the_feedback_set(self, tmp_path, monkeypatch):
        runs = []

        def fake_eval(patch, *, base_prompt, tier, cases=None):
            runs.append({c.id for c in cases} if cases is not None else None)
            return Evaluation(0.5, [])

        monkeypatch.setattr(sr, "_default_evaluator", fake_eval)
        run_self_regen(rounds=1, split=True, out_dir=tmp_path, gate=False,
                       proposer=lambda _ctx: "x", auditor=lambda _p: (True, "ok"))
        fb, sc = split_cases(sr.case_pool(generated_per_family=8), seed=0)
        fb_ids, sc_ids = {c.id for c in fb}, {c.id for c in sc}
        # baseline scored on the scoring set, the report built from the feedback
        # set, the candidate scored on the scoring set — never mixed.
        assert sc_ids in runs and fb_ids in runs
        assert all(r in (fb_ids, sc_ids) for r in runs)

    def test_feedback_answers_are_vetoed(self, tmp_path):
        recs = []
        summary = run_self_regen(
            rounds=1, split=True, out_dir=tmp_path, gate=False,
            # 388.8 is computation_unit_conversion's answer.
            proposer=lambda _ctx: "Always say 388.8 cubic metres.",
            evaluator=lambda p: recs.append(p) or 0.9,
            feedback=lambda _p: "report", auditor=lambda _p: (True, "ok"),
        )
        assert summary["variants_kept"] == 0
        assert "Always say 388.8 cubic metres." not in recs
        assert _records(tmp_path)[0]["veto"].startswith("contains eval answer token")


class TestReviseKept:
    def test_each_round_revises_the_kept_overlay_with_its_own_feedback(self, tmp_path):
        inputs, reported = [], []
        patches = iter(["first overlay", "first overlay, refined"])
        scores = {"": 0.5, "first overlay": 0.7, "first overlay, refined": 0.9}
        summary = run_self_regen(
            rounds=2, revise_kept=True, out_dir=tmp_path, gate=False,
            proposer=lambda ctx: inputs.append(ctx) or next(patches),
            evaluator=lambda p: scores[p],
            feedback=lambda p: reported.append(p) or f"report on [{p}]",
            auditor=lambda _p: (True, "ok"),
        )
        assert reported == ["", "first overlay"]
        assert "report on [first overlay]" in inputs[1]
        assert "first overlay" in inputs[1].split("## Current overlay", 1)[1]
        assert summary["best_patch"] == "first overlay, refined"
        recs = _records(tmp_path)
        assert recs[1]["parent_patch"] == "first overlay"
        assert recs[1]["parent_score"] == 0.7 and recs[1]["score"] == 0.9

    def test_without_injected_feedback_it_reports_the_parents_own_evaluation(self, tmp_path):
        inputs = []
        results = [{"id": "a", "content": 0.25, "harness": None}]
        run_self_regen(
            rounds=1, revise_kept=True, out_dir=tmp_path, gate=False,
            proposer=lambda ctx: inputs.append(ctx) or "x",
            evaluator=lambda p: Evaluation(0.5, results),
            auditor=lambda _p: (True, "ok"),
        )
        assert "content: avg 0.25" in inputs[0]


class TestRecords:
    def test_the_default_loop_records_too_and_is_otherwise_unchanged(self, tmp_path):
        inputs = []
        run_self_regen(rounds=2, weak_signal="WEAK", out_dir=tmp_path, gate=False,
                       proposer=lambda s: inputs.append(s) or "p",
                       evaluator=lambda p: {"": 0.5, "p": 0.6}[p],
                       auditor=lambda _p: (True, "ok"))
        assert inputs == ["WEAK", "WEAK"]       # the additive loop's input, as before
        recs = _records(tmp_path)
        assert len(recs) == 2
        assert recs[0]["template"] == "additive-v1"
        assert recs[0]["proposer_input"] == "WEAK"
        assert recs[0]["schema"] == sr.REVISION_RECORD_SCHEMA
        assert {"patch", "score", "parent_score", "kept", "audit_ok"} <= set(recs[0])

    def test_a_vetoed_proposal_is_recorded_unevaluated(self, tmp_path):
        run_self_regen(rounds=1, weak_signal="w", out_dir=tmp_path, gate=False,
                       proposer=lambda _s: "x" * (sr.MAX_PATCH_CHARS + 1),
                       evaluator=lambda p: 0.5, auditor=lambda _p: (True, "ok"))
        rec = _records(tmp_path)[0]
        assert rec["evaluated"] is False and rec["veto"].startswith("over-length")


def test_the_generators_are_part_of_the_scorer_fingerprint(tmp_path, monkeypatch):
    """Generated cases decide scores under --split, so editing a generator
    mid-run must void the run like editing a case file does."""
    import inspect
    src = inspect.getsource(sr._scorer_fingerprint)
    assert "case_generators.py" in src


class TestReplicatedKeep:
    """keep_replicates: a gain must beat the margin AND the run-to-run spread
    (docs/research/mass-multi-agent-self-supervision.md: the same prompt won
    6-0 and then lost 0-6 on consecutive runs)."""

    @staticmethod
    def _evaluator(runs: dict):
        its = {k: iter(v) for k, v in runs.items()}
        calls = []

        def ev(patch):
            calls.append(patch)
            return next(its[patch])
        return ev, calls

    def test_a_lucky_run_is_not_a_gain(self, tmp_path):
        ev, _ = self._evaluator({"": [0.50, 0.50], "lucky": [0.90, 0.50]})
        s = run_self_regen(rounds=1, weak_signal="w", out_dir=tmp_path, gate=False,
                           min_margin=0.05, keep_replicates=2,
                           proposer=lambda _s: "lucky", evaluator=ev,
                           auditor=lambda _p: (True, "ok"))
        # mean 0.70 is +0.20 over 0.50, above the margin — but the two runs
        # disagree by 0.40, so the gain is inside the noise.
        assert s["variants_kept"] == 0 and s["best_patch"] == ""
        rec = _records(tmp_path)[0]
        assert rec["replicate_scores"] == [0.9, 0.5] and rec["noise_floor"] == 0.4

    def test_a_steady_gain_is_kept(self, tmp_path):
        ev, _ = self._evaluator({"": [0.50, 0.52], "steady": [0.70, 0.72]})
        s = run_self_regen(rounds=1, weak_signal="w", out_dir=tmp_path, gate=False,
                           min_margin=0.05, keep_replicates=2,
                           proposer=lambda _s: "steady", evaluator=ev,
                           auditor=lambda _p: (True, "ok"))
        assert s["best_patch"] == "steady"
        assert s["keep_replicates"] == 2 and s["baseline_scores"] == [0.5, 0.52]

    def test_the_incumbents_spread_counts_too(self, tmp_path):
        # "first" is kept, but its own runs disagree by 0.10. A later candidate
        # that improves on it by less than that is inside the incumbent's noise.
        ev, _ = self._evaluator({"": [0.50, 0.50], "first": [0.75, 0.65],
                                 "second": [0.78, 0.78], "third": [0.90, 0.90]})
        patches = iter(["first", "second", "third"])
        s = run_self_regen(rounds=3, weak_signal="w", out_dir=tmp_path, gate=False,
                           min_margin=0.05, keep_replicates=2,
                           proposer=lambda _s: next(patches), evaluator=ev,
                           auditor=lambda _p: (True, "ok"))
        recs = _records(tmp_path)
        # first: +0.20 over a steady baseline → kept.
        # second: +0.08 ≥ margin, steady itself, but ≤ the incumbent's 0.10 spread.
        # third: +0.20 over first → kept.
        assert [r["kept"] for r in recs] == [True, False, True]
        assert recs[1]["noise_floor"] == 0.1
        assert s["best_patch"] == "third"

    def test_default_is_one_run_per_patch(self, tmp_path):
        ev, calls = self._evaluator({"": [0.5], "p": [0.6]})
        run_self_regen(rounds=1, weak_signal="w", out_dir=tmp_path, gate=False,
                       proposer=lambda _s: "p", evaluator=ev,
                       auditor=lambda _p: (True, "ok"))
        assert calls == ["", "p"]
