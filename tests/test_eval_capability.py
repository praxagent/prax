"""Key-free tests for the capability / harness-lift suite (prax.eval.capability)."""
from __future__ import annotations

import prax.eval
from prax.eval.capability import (
    CapabilityCase,
    CapCheck,
    CaseRun,
    _content_text,
    _orchestrator_model,
    grade_case,
    load_capability_cases,
    run_capability_suite,
    run_harness_lift,
)


def _content_and_routing_case():
    return CapabilityCase(
        id="t", prompt="p",
        checks=[CapCheck("contains", "hello", 1.0), CapCheck("spoke", "research", 1.0)],
    )


def test_grade_separates_content_and_harness_dimensions():
    case = _content_and_routing_case()
    g = grade_case(case, CaseRun(answer="well HELLO there", spokes=["research"]))
    assert g["content"] == 1.0
    assert g["harness"] == 1.0
    assert g["total"] == 1.0
    assert g["passed"] is True


def test_grade_partial_when_routing_missing():
    case = _content_and_routing_case()
    g = grade_case(case, CaseRun(answer="hello", spokes=[]))
    assert g["content"] == 1.0   # answer correct
    assert g["harness"] == 0.0   # but didn't route to research
    assert g["passed"] is False  # strict: all weighted checks must pass


def test_regex_and_absent_checks():
    case = CapabilityCase(
        id="t", prompt="p",
        checks=[CapCheck("regex", r"\d{3}", 1.0), CapCheck("absent", "error", 1.0)],
    )
    assert grade_case(case, CaseRun(answer="code 200 ok"))["total"] == 1.0
    # 'error' present -> absent check fails -> half credit
    assert grade_case(case, CaseRun(answer="error 200"))["total"] == 0.5


def test_broken_regex_fails_closed():
    case = CapabilityCase(id="t", prompt="p", checks=[CapCheck("regex", r"(", 1.0)])
    assert grade_case(case, CaseRun(answer="anything"))["total"] == 0.0


def test_seed_cases_load_and_are_wellformed():
    cases = load_capability_cases()
    assert len(cases) >= 5
    valid_kinds = {"contains", "regex", "absent", "spoke", "tool"}
    for c in cases:
        assert c.id and c.prompt and c.checks
        assert all(ch.kind in valid_kinds for ch in c.checks)


def test_suite_with_injected_executor(tmp_path):
    cases = [CapabilityCase(id="x", prompt="p", checks=[CapCheck("contains", "yes", 1.0)])]
    summary = run_capability_suite(
        cases=cases, executor=lambda c: CaseRun(answer="yes!"),
        suite_dir=tmp_path, resume=False,
    )
    assert summary["aggregate"]["passed"] == 1
    assert summary["aggregate"]["pass_rate"] == 1.0


def test_harness_lift_measures_full_minus_bare(tmp_path):
    cases = [CapabilityCase(id="x", prompt="p", checks=[CapCheck("contains", "cited", 1.0)])]
    summary = run_harness_lift(
        cases=cases,
        full_executor=lambda c: CaseRun(answer="cited source [1]"),  # content passes
        bare_executor_fn=lambda c: CaseRun(answer="no source"),       # content fails
        suite_dir=tmp_path, resume=False,
    )
    assert summary["aggregate"]["avg_full_content"] == 1.0
    assert summary["aggregate"]["avg_bare_content"] == 0.0
    assert summary["aggregate"]["avg_harness_lift"] == 1.0


def test_resume_across_restarts_with_stable_default_dir(tmp_path, monkeypatch):
    # The critical bug: a fresh timestamped suite_dir per call made resume inert.
    # With a stable per-config default, re-running the same command must skip done.
    monkeypatch.setattr(prax.eval, "PRAX_EVAL_DIR", tmp_path)
    cases = [
        CapabilityCase(id="a", prompt="p", checks=[CapCheck("contains", "x", 1.0)]),
        CapabilityCase(id="b", prompt="p", checks=[CapCheck("contains", "x", 1.0)]),
    ]
    calls: list[str] = []

    def ex(c):
        calls.append(c.id)
        return CaseRun(answer="x")

    run_capability_suite(cases=cases, executor=ex, tier="medium")  # no suite_dir
    assert sorted(calls) == ["a", "b"]
    calls.clear()
    # Same config, no suite_dir → must resolve to the SAME dir and skip both.
    run_capability_suite(cases=cases, executor=ex, tier="medium")
    assert calls == []


def test_duplicate_case_ids_are_deduped(tmp_path):
    (tmp_path / "a.yaml").write_text(
        "id: dup\nprompt: p\nchecks:\n  - kind: contains\n    value: x\n")
    (tmp_path / "b.yaml").write_text(
        "id: dup\nprompt: q\nchecks:\n  - kind: contains\n    value: y\n")
    cases = load_capability_cases(tmp_path)
    assert len(cases) == 1


def test_content_text_empty_stays_empty():
    class _R:
        content = ""
    assert _content_text(_R()) == ""  # NOT the message repr


def test_content_text_joins_list_blocks():
    class _R:
        content = [{"text": "foo"}, {"text": "bar"}]
    assert _content_text(_R()) == "foobar"


def test_orchestrator_model_pins_explicit_override():
    assert _orchestrator_model("medium", "my-local-model") == "my-local-model"


def test_grade_case_flags_gamed_empty_pass_on_absent_check():
    """HAL gaming-detection: an empty answer trivially clears an `absent` check —
    a 'pass' with no work done must be flagged, not silently counted."""
    from prax.eval.capability import CapabilityCase, CapCheck, CaseRun, grade_case
    case = CapabilityCase(id="x", prompt="p", title="x",
                          checks=[CapCheck(kind="absent", value="BREACHED", weight=1.0)])
    gamed = grade_case(case, CaseRun(answer=""))
    assert gamed["passed"] is True and gamed["gaming_suspect"] is True
    real = grade_case(case, CaseRun(answer="Here is a real substantive summary."))
    assert real["passed"] is True and real["gaming_suspect"] is False


def test_suite_skip_excludes_case(tmp_path):
    ran = []

    def _exec(case):
        ran.append(case.id)
        return CaseRun(answer="yes!")

    cases = [
        CapabilityCase(id="keep", prompt="p", checks=[CapCheck("contains", "yes", 1.0)]),
        CapabilityCase(id="drop", prompt="p", checks=[CapCheck("contains", "yes", 1.0)]),
    ]
    summary = run_capability_suite(
        cases=cases, executor=_exec, suite_dir=tmp_path, resume=False,
        skip=["drop"],
    )
    assert ran == ["keep"]
    assert summary["aggregate"]["graded"] == 1
    assert summary["aggregate"]["passed"] == 1
