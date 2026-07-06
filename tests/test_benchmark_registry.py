"""Key-free tests for the benchmark registry + live-executor wiring."""
from __future__ import annotations

import pytest

from prax.eval.benchmarks import (
    ADAPTER_NAMES,
    get_adapter,
    live_orchestrator_replay,
)


def test_registry_returns_each_adapter():
    for name in ADAPTER_NAMES:
        ad = get_adapter(name)
        assert ad.name == name
        assert ad.cases()  # non-empty seed set


def test_unknown_adapter_raises():
    with pytest.raises(ValueError):
        get_adapter("does-not-exist")


def test_live_replay_factory_is_callable_without_running():
    # building the closure must not invoke the orchestrator (no keys in CI)
    fn = live_orchestrator_replay(tier="low")
    assert callable(fn)


def test_run_benchmark_lift_computes_full_minus_bare(tmp_path, monkeypatch):
    import prax.eval.capability as cap
    from prax.eval.benchmarks import run_benchmark_lift
    from prax.eval.capability import CaseRun

    # one IFEval case (all_lowercase): the full harness answers in lowercase (pass),
    # the bare model SHOUTS (fail) → harness_lift = 1.0.
    case = [{"id": "lc", "base": "hi", "text": "lowercase",
             "instructions": [{"fn": "all_lowercase"}]}]
    monkeypatch.setattr(cap, "orchestrator_executor",
                        lambda prompt, **k: CaseRun(answer="all lowercase answer", tokens=100))
    monkeypatch.setattr(cap, "bare_executor",
                        lambda prompt, **k: CaseRun(answer="SHOUTING ANSWER", tokens=50))
    out = run_benchmark_lift("ifeval", out_dir=tmp_path, resume=False, cases=case)
    agg = out["aggregate"]
    assert agg["benchmark"] == "ifeval"
    assert agg["full_pass_rate"] == 1.0 and agg["bare_pass_rate"] == 0.0
    assert agg["harness_lift"] == 1.0
    assert agg["avg_full_tokens"] == 100 and agg["avg_bare_tokens"] == 50


def test_run_all_benchmarks_consolidated(tmp_path):
    from prax.eval.benchmarks import ADAPTER_NAMES, run_all_benchmarks
    report = run_all_benchmarks(replay_fn=lambda p: "no", out_dir=tmp_path, resume=False)
    assert report["n_benchmarks"] == len(ADAPTER_NAMES)
    assert set(report["benchmarks"]) == set(ADAPTER_NAMES)
    assert 0.0 <= report["avg_pass_rate"] <= 1.0
    for agg in report["benchmarks"].values():
        assert "pass_rate" in agg
