"""Key-free tests for the GSM8K numeric-answer adapter."""
from __future__ import annotations

from prax.eval.benchmarks.gsm8k import SEED_CASES, GSM8KAdapter, _final_number, score


def test_final_number_extraction():
    assert _final_number("The answer is 72.") == "72"
    assert _final_number("48 + 24, so 72") == "72"
    assert _final_number("1,200 apples") == "1200"
    assert _final_number("no numbers here") is None


def test_gsm8k_scoring():
    c = next(x for x in SEED_CASES if x["id"] == "gsm_seedlings")  # answer 51
    assert score(c, "9 x 7 = 63, minus the 12 voucher = 51")["passed"] is True
    assert score(c, "The answer is 63")["passed"] is False


def test_seed_labels_are_numeric():
    for c in SEED_CASES:
        assert c["question"] and float(c["answer"])  # every label parses as a number


def test_seed_answers_are_the_authored_arithmetic():
    # The seed set is AUTHORED for this repo (never copied from the benchmark —
    # contamination firewall); the labels must match the arithmetic the
    # problems state, checked here independently of the trailing comments.
    expected = {
        "gsm_seedlings": 9 * 7 - 12,
        "gsm_sprint_avg": (13 + 12 + 14 + 13) / 4 - 11,
        "gsm_trough": 250 - 12 * (60 / 4),
        "gsm_chairs": 240 * (1 - 25 / 100) - 30,
        "gsm_bill_split": 96 / (1 + 2 + 5) * 5,
    }
    assert {c["id"] for c in SEED_CASES} == set(expected)
    for c in SEED_CASES:
        assert float(c["answer"]) == expected[c["id"]], c["id"]


def test_registered_and_runs(tmp_path):
    from prax.eval.benchmarks import ADAPTER_NAMES, get_adapter, run_benchmark
    assert "gsm8k" in ADAPTER_NAMES and get_adapter("gsm8k").name == "gsm8k"
    ad = GSM8KAdapter(cases=[next(x for x in SEED_CASES if x["id"] == "gsm_bill_split")])  # answer 60
    out = run_benchmark(ad, replay_fn=lambda p: "8 shares, 96 / 8 = 12 per share, 5 shares = 60",
                        out_dir=tmp_path, resume=False)
    assert out["aggregate"]["benchmark"] == "gsm8k" and out["aggregate"]["pass_rate"] == 1.0
