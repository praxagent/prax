"""Key-free tests for the HaluEval + TruthfulQA grounding adapters."""
from __future__ import annotations

from prax.eval.benchmarks import run_benchmark
from prax.eval.benchmarks.halueval import (
    SEED_CASES as HAL,
)
from prax.eval.benchmarks.halueval import (
    HaluEvalAdapter,
    _predict_hallucinated,
)
from prax.eval.benchmarks.halueval import score as hscore
from prax.eval.benchmarks.truthfulqa import (
    SEED_CASES as TQA,
)
from prax.eval.benchmarks.truthfulqa import (
    TruthfulQAAdapter,
    _predict_index,
)
from prax.eval.benchmarks.truthfulqa import score as tscore


def test_halueval_yes_no_prediction():
    assert _predict_hallucinated("Yes, that claim is wrong.") is True
    assert _predict_hallucinated("No, it is correct.") is False
    assert _predict_hallucinated("I'm not certain either way.") is None


def test_halueval_scoring_both_labels():
    bad = next(c for c in HAL if c["id"] == "hal_au_bad")   # hallucinated=True
    assert hscore(bad, "Yes")["passed"] is True             # correctly flagged
    assert hscore(bad, "No")["passed"] is False
    ok = next(c for c in HAL if c["id"] == "hal_au_ok")     # hallucinated=False
    assert hscore(ok, "No, it's accurate.")["passed"] is True


def test_halueval_prompt_shape():
    ad = HaluEvalAdapter()
    p = ad.prompt(ad.cases()[0])
    assert "Yes" in p and "No" in p and "hallucinat" in p.lower()


def test_truthfulqa_index_prediction():
    assert _predict_index("0") == 0
    assert _predict_index("The answer is 1.") == 1
    assert _predict_index("none of these") is None


def test_truthfulqa_scoring():
    c = next(x for x in TQA if x["id"] == "tqa_brain")  # correct index 0
    assert tscore(c, "0")["passed"] is True
    assert tscore(c, "1")["passed"] is False


def test_grounding_registered_and_runs(tmp_path):
    from prax.eval.benchmarks import ADAPTER_NAMES, get_adapter
    assert "halueval" in ADAPTER_NAMES and "truthfulqa" in ADAPTER_NAMES
    assert get_adapter("halueval").name == "halueval"
    ad = TruthfulQAAdapter(cases=[next(x for x in TQA if x["id"] == "tqa_brain")])
    out = run_benchmark(ad, replay_fn=lambda p: "0", out_dir=tmp_path, resume=False)
    assert out["aggregate"]["benchmark"] == "truthfulqa" and out["aggregate"]["pass_rate"] == 1.0
