"""Key-free tests for the IFEval verifiers + the benchmark-adapter seam."""
from __future__ import annotations

from prax.eval.benchmarks import run_benchmark
from prax.eval.benchmarks.ifeval import SEED_CASES, IFEvalAdapter, score


def test_verifiers_basic():
    from prax.eval.benchmarks import ifeval as I
    assert I.all_lowercase("all good lowercase") is True
    assert I.all_lowercase("Has Capitals") is False
    assert I.all_lowercase("12345") is False  # no letters → not satisfied
    assert I.no_commas("no commas here") is True
    assert I.no_commas("yes, commas") is False
    assert I.min_words("one two three", 3) is True
    assert I.min_words("one two", 3) is False
    assert I.keyword_frequency("wave wave wave", "wave", 3, "at least") is True
    assert I.keyword_frequency("wave", "wave", 3, "at least") is False
    assert I.num_bullets("* a\n* b\n* c", 3) is True
    assert I.num_bullets("* a\n* b", 3) is False
    assert I.json_format('{"a": 1}') is True
    assert I.json_format("```json\n{\"a\": 1}\n```") is True  # code-fenced
    assert I.json_format("not json") is False
    assert I.end_with("... Happy running!", "Happy running!") is True
    assert I.has_title("<<My Title>>\nbody") is True
    assert I.has_title("no title") is False


def test_score_all_or_nothing():
    case = {"id": "x", "instructions": [{"fn": "all_lowercase"}, {"fn": "no_commas"}]}
    good = score(case, "all lowercase and no commas")
    assert good["passed"] is True and good["score"] == 1.0
    partial = score(case, "all lowercase, but a comma")
    assert partial["passed"] is False and partial["score"] == 0.5  # one of two


def test_seed_cases_wellformed():
    for c in SEED_CASES:
        assert c["id"] and c["base"] and c["text"] and c["instructions"]
        for ins in c["instructions"]:
            assert "fn" in ins


def test_adapter_prompt_includes_instruction():
    ad = IFEvalAdapter()
    c = ad.cases()[0]
    p = ad.prompt(c)
    assert c["base"] in p and c["text"] in p


def test_run_benchmark_seam(tmp_path):
    # A one-case adapter + a compliant fake executor → pass_rate 1.0 through the
    # generic seam (which drives the real resumable run_batch).
    case = [{"id": "lc", "base": "say hi", "text": "lowercase please",
             "instructions": [{"fn": "all_lowercase"}]}]
    ad = IFEvalAdapter(cases=case)
    out = run_benchmark(ad, replay_fn=lambda p: "hello there in all lowercase",
                        out_dir=tmp_path, resume=False)
    agg = out["aggregate"]
    assert agg["benchmark"] == "ifeval"
    assert agg["graded"] == 1 and agg["passed"] == 1 and agg["pass_rate"] == 1.0


def test_run_benchmark_seam_scores_failure(tmp_path):
    case = [{"id": "lc", "base": "say hi", "text": "lowercase please",
             "instructions": [{"fn": "all_lowercase"}]}]
    ad = IFEvalAdapter(cases=case)
    out = run_benchmark(ad, replay_fn=lambda p: "SHOUTING RESPONSE",
                        out_dir=tmp_path, resume=False)
    assert out["aggregate"]["passed"] == 0 and out["aggregate"]["pass_rate"] == 0.0
