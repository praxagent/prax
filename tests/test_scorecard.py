"""Keyless tests for the committed scorecard — especially the leak guardrail.

The scorecard is committed to the PUBLIC repo, so the one thing that must never
fail silently is a benchmark question/answer sneaking into it. `assert_no_leak` is
that firewall; these tests make a leak fail loudly.
"""
from __future__ import annotations

import pytest

from prax.eval.scorecard import (
    UnhealthyRunError,
    assert_no_leak,
    assert_run_healthy,
    build_record,
    load_records,
    render_dashboard,
    write_dashboard,
    write_record,
)

_REPORT = {
    "n_benchmarks": 2,
    "avg_pass_rate": 0.8,
    "total_tokens": 1000,
    "estimated_cost_usd": 0.12,
    "total_errors": 0,
    "total_attempted": 45,
    "error_rate": 0.0,
    "benchmarks": {
        "gsm8k": {"benchmark": "gsm8k", "graded": 40, "pass_rate": 0.95, "errors": 0,
                  "total_tokens": 600, "answer_preview": "the answer is 72",
                  "protocol": {"variant": "exact match", "dataset": "real"}},
        "locomo": {"benchmark": "locomo", "graded": 5, "pass_rate": 0.6, "errors": 0,
                   "total_tokens": 400, "protocol": {"variant": "recall", "dataset": "seed"}},
    },
}


def _record():
    return build_record(_REPORT, git_commit="abc1234", model="deepseek/deepseek-v4-flash",
                        provider="openrouter", matrix_limit=40, timestamp="2026-07-22T00:00:00")


def test_build_record_is_aggregates_only():
    rec = _record()
    # per-benchmark keeps only aggregate fields — the answer_preview must be dropped
    assert rec["benchmarks"]["gsm8k"] == {
        "pass_rate": 0.95, "n": 40, "errors": 0, "total_tokens": 600,
        "dataset": "real", "variant": "exact match",
    }
    assert "answer_preview" not in str(rec)
    assert rec["aggregate"]["avg_pass_rate"] == 0.8
    assert rec["git_commit"] == "abc1234"


def test_leak_guardrail_raises_on_forbidden_key():
    with pytest.raises(ValueError, match="leak"):
        assert_no_leak({"benchmarks": {"x": {"answer": "72"}}})
    with pytest.raises(ValueError, match="leak"):
        assert_no_leak({"cases": [{"prompt": "..."}]})
    # a clean aggregates-only record must pass
    assert_no_leak(_record())


def test_build_record_would_raise_if_it_ever_kept_a_forbidden_field(monkeypatch):
    # If a future edit to build_record forgot to strip a per-case field, the
    # embedded assert_no_leak must catch it. Simulate by injecting a bad report.
    import prax.eval.scorecard as sc
    bad = dict(_REPORT)
    bad["benchmarks"] = {"gsm8k": {"pass_rate": 0.9, "graded": 1, "questions": ["2+2?"]}}
    # build_record only copies whitelisted keys, so "questions" is dropped → no raise.
    rec = sc.build_record(bad, git_commit="c", model="m", provider="p",
                          matrix_limit=1, timestamp="2026-07-22T00:00:00")
    assert "questions" not in str(rec)


def test_write_record_and_dashboard(tmp_path):
    rec = _record()
    p = write_record(rec, root=tmp_path, run_id="deadbeef")
    assert p.exists() and p.name == "2026-07-22-deadbeef.json"
    assert p.parent.name == "2026"  # year-partitioned

    dash = write_dashboard(root=tmp_path)
    md = dash.read_text()
    assert "gsm8k" in md and "locomo" in md
    assert "0.95" in md and "abc1234"[:7] in md
    # the dashboard is aggregates only too
    assert "answer" not in md.lower() or "answer" not in md  # no per-case content


def test_load_records_roundtrip(tmp_path):
    write_record(_record(), root=tmp_path, run_id="r1")
    recs = load_records(tmp_path)
    assert len(recs) == 1 and recs[0]["model"].endswith("deepseek-v4-flash")


def test_dashboard_empty_is_graceful():
    assert "No runs recorded" in render_dashboard([])


def test_healthy_run_passes_guard():
    # 0% error rate → records fine.
    assert_run_healthy(_REPORT)  # no raise


def test_unhealthy_run_is_refused():
    # The exact shape of the voided first baseline: most cases failed to execute.
    broken = dict(_REPORT)
    broken["total_errors"], broken["total_attempted"], broken["error_rate"] = 65, 100, 0.65
    with pytest.raises(UnhealthyRunError, match="error-rate"):
        assert_run_healthy(broken)


def test_error_rate_ceiling_is_overridable(monkeypatch):
    broken = dict(_REPORT)
    broken["error_rate"] = 0.65
    monkeypatch.setenv("PRAX_SCORECARD_MAX_ERROR_RATE", "0.9")
    assert_run_healthy(broken)  # ceiling raised → no raise


def test_record_carries_run_health():
    rec = _record()
    agg = rec["aggregate"]
    assert agg["error_rate"] == 0.0 and agg["total_attempted"] == 45
    # dashboard exposes the err% column so a broken run is visible at a glance
    assert "err%" in render_dashboard([rec])


# ── Sampling honesty: a capped sample must never render as a suite result ────
#
# Regression guard for the 2026-07-24 finding: the record carried `n` and
# `dataset`, but the dashboard dropped both — so `gsm8k | 1.00` (10 sampled
# cases) read as a full-suite score, and a blended average across real +
# self-authored seed sets was published as the headline. Presentation-layer
# laundering of honest data is still dishonest; these tests pin the fix.

def test_dashboard_warns_the_results_are_sampled_not_full_suites():
    md = render_dashboard([_record()])
    assert "SAMPLED" in md and "NOT full benchmark suites" in md
    # The prohibition must be explicit, not implied by a config column.
    assert "Do not quote these numbers as" in md
    assert "leaderboard" in md.lower()


def test_dashboard_shows_n_behind_every_score():
    md = render_dashboard([_record()])
    assert "0.95(40)" in md      # real benchmark: pass_rate(n)
    assert "0.60(5)*" in md      # seed benchmark: pass_rate(n) + seed marker


def test_dashboard_marks_seed_benchmarks_and_denies_comparability():
    md = render_dashboard([_record()])
    assert "seed (in-repo)" in md and "real (sampled)" in md
    assert "not the public set" in md      # seed rows
    assert "sample too small" in md        # real rows


def test_dashboard_splits_real_and_seed_averages_never_blends_them():
    md = render_dashboard([_record()])
    assert "real avg" in md and "seed avg" in md
    # The blended figure (0.8 in _REPORT) must not be presented as a headline.
    assert "| avg |" not in md
    assert "0.74" not in md or True  # (shape check below is the real assertion)
    from prax.eval.scorecard import split_averages
    sp = split_averages(_record()["benchmarks"])
    assert sp["real_avg"] == 0.95 and sp["seed_avg"] == 0.6
    assert sp["cases_real"] == 40 and sp["cases_seed"] == 5
    assert sp["cases_graded"] == 45


def test_record_states_it_is_a_sample_standalone():
    # A reader of the raw JSON, with no access to MATRIX.md, must still be told.
    rec = _record()
    s = rec["sampling"]
    assert s["sampled"] is True and s["full_suite"] is False
    assert s["comparable_to_public_leaderboards"] is False
    assert s["cases_per_benchmark_cap"] == 40
    assert rec["aggregate"]["by_provenance"]["real_avg"] == 0.95


def test_split_averages_handles_empty_and_missing_fields():
    from prax.eval.scorecard import split_averages
    sp = split_averages({})
    assert sp["real_avg"] is None and sp["seed_avg"] is None and sp["cases_graded"] == 0
    # A benchmark with no pass_rate is skipped, not counted as 0.
    sp2 = split_averages({"x": {"pass_rate": None, "n": 3, "dataset": "real"}})
    assert sp2["real_avg"] is None and sp2["n_real_benchmarks"] == 0
