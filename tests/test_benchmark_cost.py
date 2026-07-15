"""Key-free tests for per-benchmark cost tracking (prax.eval.pricing + the
token/cost fields run_benchmark now reports)."""
from __future__ import annotations

from prax.eval import pricing
from prax.eval.benchmarks import get_adapter, run_all_benchmarks, run_benchmark

# --------------------------------------------------------------------------- #
# pricing
# --------------------------------------------------------------------------- #

def test_estimate_cost_exact_and_suffix():
    # deepseek-v4-flash: 0.09 in / 0.28 out → 1M in + 0.5M out = 0.09 + 0.14 = 0.23
    assert pricing.estimate_cost("deepseek/deepseek-v4-flash", 1_000_000, 500_000) == 0.23
    # suffix match strips the provider prefix
    assert pricing.estimate_cost("openai/gpt-5.4-nano", 1_000_000, 0) == 0.05


def test_estimate_cost_unknown_is_none_not_zero():
    assert pricing.estimate_cost("who/knows-model", 1000, 1000) is None
    assert pricing.estimate_cost(None, 1000, 1000) is None


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("EVAL_COST_INPUT_PER_M", "1.0")
    monkeypatch.setenv("EVAL_COST_OUTPUT_PER_M", "2.0")
    # 1M in @ $1 + 1M out @ $2 = $3, even for an otherwise-unknown model
    assert pricing.estimate_cost("who/knows", 1_000_000, 1_000_000) == 3.0


def test_format_cost():
    assert pricing.format_cost(None) == "n/a"
    assert pricing.format_cost(0.0123) == "$0.0123"


# --------------------------------------------------------------------------- #
# run_benchmark carries token + cost fields (fake replay → 0 tokens, keyless)
# --------------------------------------------------------------------------- #

def _fake_replay(_prompt: str) -> str:
    return "Answer: A"  # arbitrary; we're testing the cost plumbing, not pass rate


def test_run_benchmark_reports_tokens_and_cost(tmp_path):
    ad = get_adapter("gpqa")
    summary = run_benchmark(ad, _fake_replay, out_dir=tmp_path / "gpqa",
                            cost_model="deepseek/deepseek-v4-flash")
    agg = summary.get("aggregate") or {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens",
                "cost_model", "estimated_cost_usd"):
        assert key in agg
    # No live LLM in CI → zero tokens → zero (known) cost, not None.
    assert agg["total_tokens"] == 0
    assert agg["estimated_cost_usd"] == 0.0
    assert agg["cost_model"] == "deepseek/deepseek-v4-flash"


def test_run_benchmark_unknown_model_cost_is_none(tmp_path):
    ad = get_adapter("gpqa")
    summary = run_benchmark(ad, _fake_replay, out_dir=tmp_path / "gpqa2", cost_model=None)
    agg = summary.get("aggregate") or {}
    assert agg["estimated_cost_usd"] is None  # unknown price ≠ fabricated zero


def test_run_all_benchmarks_consolidated_cost(tmp_path):
    report = run_all_benchmarks(replay_fn=_fake_replay, out_dir=tmp_path / "all",
                                model="deepseek/deepseek-v4-flash", resume=False)
    assert "total_tokens" in report and "estimated_cost_usd" in report
    assert report["cost_model"] == "deepseek/deepseek-v4-flash"
    assert report["total_tokens"] == 0
    assert report["estimated_cost_usd"] == 0.0
