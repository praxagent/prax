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


def test_collect_disagreements_surfaces_vetoed_criteria():
    """Disagreement-driven curation: auditor-vetoed criteria become the review
    queue (judge↔auditor disagreement = candidate mislabel)."""
    from prax.eval.goldens import _collect_disagreements
    suite = {"results": [
        {"id": "g1", "title": "T1", "vetoed": ["c1", "c2"]},
        {"id": "g2", "title": "T2", "vetoed": []},
        {"id": "g3", "title": "T3"},  # no audit key at all
    ]}
    d = _collect_disagreements(suite)
    assert len(d) == 2
    assert {x["criterion"] for x in d} == {"c1", "c2"}
    assert all(x["golden"] == "g1" for x in d)


def test_run_golden_curation_key_free_with_injected_replay(tmp_path):
    """End-to-end key-free: injected replay_fn + judge; no auditor veto path fires
    without a real auditor, so it should simply return an empty review queue."""
    from prax.eval.goldens import run_golden_curation
    out = run_golden_curation(
        judge=lambda prompt, rubric: {c.key: 1.0 for c in rubric},
        replay_fn=lambda prompt: "a plausible answer",
    )
    assert "disagreements" in out and "n_disagreements" in out
    assert out["n_disagreements"] == len(out["disagreements"])


# --------------------------------------------------------------------------- #
# Public/private split + AIDE² selection gate (docs/research/aide2-...)
# --------------------------------------------------------------------------- #

def test_visibility_defaults_public_and_loads_private():
    by_id = {gd.id: gd for gd in g.load_goldens()}
    # Existing goldens are unchanged (default public)...
    assert by_id["research_multiperspective"].visibility == "public"
    # ...and the two designated held-out goldens are private.
    assert by_id["skill_capture_reuse"].visibility == "private"
    assert by_id["proactive_signal_management"].visibility == "private"


def test_unknown_visibility_fails_safe_to_public(tmp_path):
    (tmp_path / "typo.yaml").write_text(
        "id: t\ntitle: t\nkind: k\nprompt: p\nvisibility: privat\n"  # typo
        "rubric:\n  - key: a\n    weight: 1\n    description: d\n"
    )
    gd = g.load_goldens(tmp_path)[0]
    assert gd.visibility == "public"  # never silently promoted into the holdout


def test_summarize_split_averages_by_visibility():
    results = [
        {"visibility": "public", "total": 1.0},
        {"visibility": "public", "total": 0.0},
        {"visibility": "private", "total": 0.5},
        {"visibility": "private", "total": None},     # unscored — ignored
        {"total": 1.0},                                # missing → counted public
    ]
    s = g.summarize_split(results)
    assert s["avg_public"] == round((1.0 + 0.0 + 1.0) / 3, 3)
    assert s["n_public"] == 3
    assert s["avg_private"] == 0.5
    assert s["n_private"] == 1


def test_suite_reports_public_and_private_split():
    # Listing-only run still carries the split keys (both empty — nothing scored).
    listing = g.run_golden_suite(replay=False)
    for key in ("avg_public", "avg_private", "n_public", "n_private"):
        assert key in listing
    assert listing["n_private"] == 0 and listing["n_public"] == 0

    # A scored run (injected replay + judge, no keys) populates both sides; the two
    # held-out goldens put the private set at >= 2.
    scored = g.run_golden_suite(
        replay=True,
        replay_fn=lambda prompt: "grounded answer https://example.com [1]",
        judge=lambda prompt: json.dumps({"scores": {}}),  # rows scored (total float), no keys matched
    )
    assert scored["n_private"] >= 2
    assert scored["n_public"] >= 1


def test_accept_change_selects_on_private_score():
    base = {"avg_public": 0.5, "avg_private": 0.5}
    better = {"avg_public": 0.6, "avg_private": 0.7}
    d = g.accept_change(base, better)
    assert d["accept"] is True
    assert d["private_delta"] == 0.2 and d["gamed_public"] is False


def test_accept_change_flags_reward_hacking_signature():
    # Public went UP, private went DOWN — the classic gamed-the-visible-metric shape.
    base = {"avg_public": 0.5, "avg_private": 0.6}
    gamed = {"avg_public": 0.9, "avg_private": 0.4}
    d = g.accept_change(base, gamed)
    assert d["accept"] is False
    assert d["gamed_public"] is True
    assert "reward-hacking" in d["reason"]


def test_accept_change_fails_closed_without_private_holdout():
    d = g.accept_change({"avg_public": 0.5, "avg_private": None},
                        {"avg_public": 0.9, "avg_private": None})
    assert d["accept"] is False
    assert "fail-closed" in d["reason"]


def test_accept_change_boundary_and_threshold():
    # Exactly at the threshold is NOT an improvement — guards `>` vs `>=`.
    tie = g.accept_change({"avg_private": 0.5, "avg_public": 0.5},
                          {"avg_private": 0.5, "avg_public": 0.5})
    assert tie["accept"] is False and tie["private_delta"] == 0.0
    # A non-zero min_private_delta must actually gate: +0.02 < required +0.05.
    small = g.accept_change({"avg_private": 0.5, "avg_public": 0.5},
                            {"avg_private": 0.52, "avg_public": 0.5},
                            min_private_delta=0.05)
    assert small["accept"] is False
    # Clearing the bar accepts.
    big = g.accept_change({"avg_private": 0.5, "avg_public": 0.5},
                          {"avg_private": 0.6, "avg_public": 0.5},
                          min_private_delta=0.05)
    assert big["accept"] is True


def test_accept_change_never_accepts_reward_hack_even_with_negative_threshold():
    # A negative threshold absorbs noise, but a public-up/private-down change is
    # still a HARD reject — the reward-hacking signature is never adopted.
    base = {"avg_public": 0.5, "avg_private": 0.6}
    gamed = {"avg_public": 0.9, "avg_private": 0.58}  # private down 0.02, within -0.1 noise band
    d = g.accept_change(base, gamed, min_private_delta=-0.1)
    assert d["accept"] is False
    assert d["gamed_public"] is True and "reward-hacking" in d["reason"]


def test_accept_change_enforces_cost_budget():
    base = {"avg_public": 0.5, "avg_private": 0.5}
    better = {"avg_public": 0.6, "avg_private": 0.7}
    # Same private win, but 50% more expensive at the default max_cost_ratio=1.0.
    d = g.accept_change(base, better, baseline_cost=1000, candidate_cost=1500)
    assert d["accept"] is False
    assert d["cost_ratio"] == 1.5 and "cost" in d["reason"]
    # Cheaper-or-equal passes.
    ok = g.accept_change(base, better, baseline_cost=1000, candidate_cost=900)
    assert ok["accept"] is True and ok["cost_ratio"] == 0.9
