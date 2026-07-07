"""Key-free tests for the GSM8K numeric-answer adapter."""
from __future__ import annotations

from prax.eval.benchmarks.gsm8k import SEED_CASES, GSM8KAdapter, _final_number, score


def test_final_number_extraction():
    assert _final_number("The answer is 72.") == "72"
    assert _final_number("48 + 24, so 72") == "72"
    assert _final_number("1,200 apples") == "1200"
    assert _final_number("no numbers here") is None


def test_gsm8k_scoring():
    c = next(x for x in SEED_CASES if x["id"] == "gsm_clips")  # answer 72
    assert score(c, "She sold 48 + 24 = 72")["passed"] is True
    assert score(c, "The answer is 70")["passed"] is False


def test_seed_labels_are_numeric():
    for c in SEED_CASES:
        assert c["question"] and float(c["answer"])  # every label parses as a number


def test_registered_and_runs(tmp_path):
    from prax.eval.benchmarks import ADAPTER_NAMES, get_adapter, run_benchmark
    assert "gsm8k" in ADAPTER_NAMES and get_adapter("gsm8k").name == "gsm8k"
    ad = GSM8KAdapter(cases=[next(x for x in SEED_CASES if x["id"] == "gsm_trees")])  # answer 6
    out = run_benchmark(ad, replay_fn=lambda p: "there were 21, minus 15, equals 6",
                        out_dir=tmp_path, resume=False)
    assert out["aggregate"]["benchmark"] == "gsm8k" and out["aggregate"]["pass_rate"] == 1.0
