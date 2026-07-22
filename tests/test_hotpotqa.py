"""Key-free tests for the HotpotQA multi-hop reading-comprehension adapter."""
from __future__ import annotations

from prax.eval.benchmarks.hotpotqa import SEED_CASES, HotpotQAAdapter, score


def _case(cid):
    return next(c for c in SEED_CASES if c["id"] == cid)


def test_bridge_span_answer():
    c = _case("hotpot_bridge_city")  # Kraków
    assert score(c, "The director was born in Kraków, Poland.")["passed"] is True
    assert score(c, "She was born in Warsaw.")["passed"] is False


def test_comparison_answer():
    c = _case("hotpot_comparison_taller")  # Aster Tower (312 > 268)
    assert score(c, "Aster Tower is taller.")["passed"] is True
    assert score(c, "The Cobalt Spire.")["passed"] is False


def test_numeric_bridge_year():
    c = _case("hotpot_bridge_year")  # 2004
    assert score(c, "She earned her PhD in 2004.")["passed"] is True
    assert score(c, "In 2016.")["passed"] is False  # the prize year, not the PhD year


def test_prompt_includes_all_passages_and_hop_instruction():
    ad = HotpotQAAdapter()
    p = ad.prompt(_case("hotpot_bridge_city"))
    assert "[Blue Harvest (film)]" in p and "[Marta Ostrowski]" in p
    assert "more than one passage" in p.lower()


def test_registered_and_runs_end_to_end(tmp_path):
    from prax.eval.benchmarks import ADAPTER_NAMES, get_adapter, run_benchmark
    assert "hotpotqa" in ADAPTER_NAMES and get_adapter("hotpotqa").name == "hotpotqa"
    ad = HotpotQAAdapter(cases=[_case("hotpot_bridge_nationality")])  # Indian
    out = run_benchmark(ad, replay_fn=lambda p: "The leader is Indian.",
                        out_dir=tmp_path, resume=False)
    assert out["aggregate"]["benchmark"] == "hotpotqa" and out["aggregate"]["pass_rate"] == 1.0


def test_seed_has_bridge_and_comparison():
    types = {c["type"] for c in SEED_CASES}
    assert {"bridge", "comparison"} <= types
    for c in SEED_CASES:
        assert len(c["passages"]) >= 2  # multi-hop needs ≥2 passages
