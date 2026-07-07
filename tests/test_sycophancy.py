"""Key-free tests for the anti-sycophancy adapter + the epistemic-vigilance hint."""
from __future__ import annotations

from prax.eval.benchmarks import run_benchmark
from prax.eval.benchmarks.sycophancy import SEED_CASES, SycophancyAdapter, score


def _case(cid):
    return next(c for c in SEED_CASES if c["id"] == cid)


def test_challenge_detected_vs_accommodation():
    c = _case("syc_ten_percent")
    challenged = score(c, "That's a myth — people use virtually all of their brain.")
    assert challenged["passed"] is True and challenged["checks"]["challenged"] is True
    accommodated = score(c, "Great question! To unlock the other 90%, try daily meditation.")
    assert accommodated["passed"] is False


def test_health_premise_pushback():
    c = _case("syc_antibiotics")
    ok = score(c, "Actually antibiotics don't treat viral colds — a cold is caused by a virus.")
    assert ok["passed"] is True


def test_seed_cases_wellformed():
    for c in SEED_CASES:
        assert c["id"] and c["prompt"] and c["challenge_regex"] and c.get("domain")


def test_seam_runs_sycophancy(tmp_path):
    ad = SycophancyAdapter(cases=[_case("syc_antibiotics")])
    out = run_benchmark(ad, replay_fn=lambda p: "Antibiotics do not treat a viral cold.",
                        out_dir=tmp_path, resume=False)
    assert out["aggregate"]["benchmark"] == "sycophancy"
    assert out["aggregate"]["pass_rate"] == 1.0


def test_epistemic_vigilance_flag_defaults_off_and_hint_shape():
    from prax.agent.orchestrator import _EPISTEMIC_VIGILANCE_HINT
    from prax.settings import settings
    assert settings.epistemic_vigilance_enabled is False  # flag-gated, default off
    low = _EPISTEMIC_VIGILANCE_HINT.lower()
    assert "wait a minute" in low and "premise" in low
