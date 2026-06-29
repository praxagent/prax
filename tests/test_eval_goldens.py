"""Key-free guard for the curated golden evals (prax.eval.goldens).

This test is the "don't lose track of it" mechanism: it runs in the default
key-free `make ci`, so deleting or breaking the STORM research golden (or the
loader) fails CI loudly. LLM scoring is exercised with an injected fake judge —
no provider keys, fully deterministic.
"""
from __future__ import annotations

import json

from prax.eval import goldens as g

# --------------------------------------------------------------------------- #
# Loading + the STORM golden must exist and be well-formed
# --------------------------------------------------------------------------- #

def test_goldens_load():
    loaded = g.load_goldens()
    assert loaded, "no goldens loaded — the goldens/ directory is empty or broken"


def test_storm_research_golden_present_and_wellformed():
    by_id = {gd.id: gd for gd in g.load_goldens()}
    assert "research_multiperspective" in by_id, "STORM research golden is missing"
    golden = by_id["research_multiperspective"]
    assert golden.kind == "research"
    assert golden.prompt.strip()
    assert golden.weight_total() > 0
    # The rubric must encode the STORM criteria we care about.
    keys = {c.key for c in golden.rubric}
    assert {"perspective_coverage", "contradiction_mapping", "grounding_citations"} <= keys
    # Reference must point at the paper (STORM), not be empty.
    assert "STORM" in golden.source


def test_malformed_golden_is_skipped_not_raised(tmp_path):
    (tmp_path / "bad.yaml").write_text("id: x\nrubric: [oops")  # invalid YAML
    (tmp_path / "ok.yaml").write_text(
        "id: ok\ntitle: t\nkind: research\nprompt: p\nrubric:\n  - key: a\n    weight: 1\n    description: d\n"
    )
    loaded = {gd.id for gd in g.load_goldens(tmp_path)}
    assert loaded == {"ok"}


# --------------------------------------------------------------------------- #
# Scoring with an injected judge (no LLM / no keys)
# --------------------------------------------------------------------------- #

def _fake_judge_factory(scores: dict):
    def _judge(prompt: str) -> str:
        return json.dumps({"scores": scores, "reasoning": "fake"})
    return _judge


def test_score_golden_weighted_average():
    golden = next(gd for gd in g.load_goldens() if gd.id == "research_multiperspective")
    perfect = {c.key: 1.0 for c in golden.rubric}
    # `grounding_citations` is now VERIFIED (deterministic regex), so the output
    # must actually carry a source marker for a perfect score — the judge's value
    # for that key is ignored.
    res = g.score_golden(golden, "an answer, see https://example.com [1]", judge=_fake_judge_factory(perfect))
    assert res["total"] == 1.0
    assert set(res["scores"]) == {c.key for c in golden.rubric}

    zero = {c.key: 0.0 for c in golden.rubric}
    assert g.score_golden(golden, "x", judge=_fake_judge_factory(zero))["total"] == 0.0


def test_binary_judging_snaps_partial_scores():
    # A judge that returns fuzzy/Likert values gets binarized: >=0.5 -> 1, else 0.
    golden = next(gd for gd in g.load_goldens() if gd.id == "research_multiperspective")
    res = g.score_golden(golden, "grounded https://x.com", judge=_fake_judge_factory(
        {c.key: 0.6 for c in golden.rubric}))  # 0.6 -> 1 for judged criteria
    assert all(v in (0.0, 1.0) for v in res["scores"].values())  # never fractional
    assert res["total"] == 1.0


def test_verify_criterion_is_deterministic_without_a_judge():
    # A golden whose every criterion is `verify`-checked needs no LLM at all.
    from prax.eval.goldens import Golden, RubricCriterion, score_golden
    gd = Golden(id="t", title="t", kind="t", prompt="p", rubric=[
        RubricCriterion(key="has_url", weight=1.0, description="", verify=r"https?://"),
    ])

    def _boom(_prompt):
        raise AssertionError("judge must NOT be called when all criteria are verifiable")

    assert score_golden(gd, "see https://a.com", judge=_boom)["total"] == 1.0
    assert score_golden(gd, "no link here", judge=_boom)["total"] == 0.0


def test_supervising_auditor_vetoes_a_passed_criterion():
    # Cheap judge passes everything; the high-tier auditor vetoes one of the PASSED
    # judged criteria (1 -> 0). `grounding_citations` is `verify` (deterministic),
    # so it's never sent to the auditor.
    golden = next(gd for gd in g.load_goldens() if gd.id == "research_multiperspective")
    cheap = _fake_judge_factory({c.key: 1.0 for c in golden.rubric})
    auditor = _fake_judge_factory({  # only the judged-pass keys reach the auditor
        "perspective_coverage": 1, "contradiction_mapping": 1,
        "synthesis": 0, "self_critique": 1,  # veto synthesis
    })
    res = g.score_golden(golden, "grounded answer https://x.com [1]",
                         judge=cheap, audit=True, auditor=auditor)
    assert res["audited"] is True
    assert res["vetoed"] == ["synthesis"]
    assert res["scores"]["synthesis"] == 0.0
    assert res["total"] < 1.0  # the veto pulled it below perfect


def test_auditor_failure_degrades_to_base_scores():
    golden = next(gd for gd in g.load_goldens() if gd.id == "research_multiperspective")
    cheap = _fake_judge_factory({c.key: 1.0 for c in golden.rubric})

    def _down(_prompt):
        raise RuntimeError("auditor unavailable")

    res = g.score_golden(golden, "grounded https://x.com", judge=cheap, audit=True, auditor=_down)
    assert res["total"] == 1.0 and res["vetoed"] == []  # base scores stand, no crash


def test_score_golden_handles_unparseable_judge():
    golden = next(gd for gd in g.load_goldens() if gd.id == "research_multiperspective")
    res = g.score_golden(golden, "x", judge=lambda _p: "not json at all")
    assert res["total"] == 0.0 and "error" in res


# --------------------------------------------------------------------------- #
# Suite: listing is key-free; scoring uses injected replay + judge
# --------------------------------------------------------------------------- #

def test_suite_listing_is_keyfree():
    report = g.run_golden_suite(replay=False)
    assert report["total"] >= 1
    assert report["scored"] == 0
    assert all(r["total"] is None for r in report["results"])


def test_suite_scoring_with_injected_replay_and_judge():
    # Scope to the rubric-scored 'research' goldens so this stays deterministic as
    # other (comparator-scored) goldens are added.
    report = g.run_golden_suite(
        replay=True,
        kind="research",
        replay_fn=lambda prompt: "a thorough multi-perspective sourced answer, see https://example.com [1]",
        judge=_fake_judge_factory({
            "perspective_coverage": 1.0, "contradiction_mapping": 1.0,
            "grounding_citations": 1.0, "synthesis": 1.0, "self_critique": 1.0,
        }),
    )
    assert report["scored"] == report["total"] >= 1
    assert report["avg"] == 1.0


def test_doc_extract_golden_tracked_but_comparator_scored():
    by_id = {gd.id: gd for gd in g.load_goldens()}
    assert "document_extract" in by_id, "doc-extraction golden is missing"
    de = by_id["document_extract"]
    assert de.kind == "doc_extract"
    assert de.rubric == []  # comparator-scored — no LLM rubric on purpose
    # Even with replay + a judge, a comparator golden is LISTED, never fake-scored.
    report = g.run_golden_suite(
        replay=True, kind="doc_extract",
        replay_fn=lambda p: "x", judge=lambda p: '{"scores": {}}',
    )
    assert report["total"] == 1 and report["scored"] == 0
    assert report["results"][0]["total"] is None
