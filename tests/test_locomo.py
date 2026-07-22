"""Key-free tests for the LoCoMo long-term memory adapter."""
from __future__ import annotations

from prax.eval.benchmarks.locomo import SEED_CASES, LoCoMoAdapter, score


def _case(cid):
    return next(c for c in SEED_CASES if c["id"] == cid)


def test_string_recall_single_hop():
    c = _case("locomo_single_hop")  # answer "Biscuit"
    assert score(c, "Her dog is named Biscuit.")["passed"] is True
    assert score(c, "I think it's Rex.")["passed"] is False


def test_multi_hop_requires_the_right_fact():
    c = _case("locomo_multi_hop")  # Portland
    assert score(c, "She works in Portland.")["passed"] is True
    assert score(c, "Seattle, probably.")["passed"] is False


def test_temporal_update_takes_the_latest_state():
    c = _case("locomo_temporal_update")  # boat (not car)
    assert score(c, "A boat.")["passed"] is True
    assert score(c, "A car.")["passed"] is False   # the superseded fact must fail


def test_numeric_multi_hop():
    c = _case("locomo_multi_hop_numeric")  # 3 kids x $5 = 15
    assert score(c, "3 times 5 is 15 dollars.")["passed"] is True
    assert score(c, "About 5 dollars.")["passed"] is False


def test_adversarial_absent_fact():
    c = _case("locomo_adversarial_absent")  # rent never stated
    # correct: decline
    assert score(c, "His rent wasn't mentioned in the conversation.")["passed"] is True
    assert score(c, "I don't know — he never said.")["passed"] is True
    # wrong: fabricate an amount
    assert score(c, "His rent is about $1,200 a month.")["passed"] is False
    # wrong: confidently answer without declining
    assert score(c, "It is 900 dollars.")["passed"] is False


def test_prompt_renders_sessions_and_instructs_no_guessing():
    ad = LoCoMoAdapter()
    p = ad.prompt(_case("locomo_single_hop"))
    assert "Session 1" in p and "Biscuit" in p
    assert "not" in p.lower() and "guess" in p.lower()   # anti-hallucination instruction


def test_registered_and_runs_end_to_end(tmp_path):
    from prax.eval.benchmarks import ADAPTER_NAMES, get_adapter, run_benchmark
    assert "locomo" in ADAPTER_NAMES and get_adapter("locomo").name == "locomo"
    ad = LoCoMoAdapter(cases=[_case("locomo_multi_hop")])  # Portland
    out = run_benchmark(ad, replay_fn=lambda p: "She works in Portland.",
                        out_dir=tmp_path, resume=False)
    assert out["aggregate"]["benchmark"] == "locomo" and out["aggregate"]["pass_rate"] == 1.0


def test_seed_categories_are_diverse():
    cats = {c["category"] for c in SEED_CASES}
    assert {"single-hop", "multi-hop", "temporal-update", "adversarial"} <= cats
