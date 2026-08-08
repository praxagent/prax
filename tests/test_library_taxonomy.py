"""Taxonomy checks must fire on badly-shaped stores and stay silent on good ones.

Implements the computable subset of the P1–P5 contract from arXiv 2607.26637
(see docs/research/filesystem-agent-memory.md). Every check is exercised in
both directions — a health metric that cannot come back clean is an alarm, and
one that cannot fire is decoration.

Keyless and model-free by construction; that is the point of implementing only
P1 and P5.
"""

import pytest

from prax.services.library_taxonomy import (
    check_sibling_distinction,
    check_structural_economy,
    normalise_label,
    taxonomy_report,
)


class TestNormaliseLabel:
    @pytest.mark.parametrize("a,b", [
        ("Q2 Forecast (final copy)", "q2-forecast-FINAL"),
        ("Meeting Notes", "meeting_notes"),
        ("Café Plans", "cafe plans"),
        ("The Budget", "budget"),
    ])
    def test_routing_equivalent_labels_collide(self, a, b):
        """If a reader cannot tell two labels apart in a listing, neither
        should the metric."""
        assert normalise_label(a) == normalise_label(b)

    def test_genuinely_different_labels_do_not_collide(self):
        assert normalise_label("Q2 Forecast") != normalise_label("Q3 Forecast")
        assert normalise_label("invoices") != normalise_label("receipts")

    def test_all_stopword_label_keeps_something(self):
        """A label made entirely of stopwords must not normalise to empty and
        then collide with every other such label by accident."""
        assert normalise_label("Misc Notes") != ""


class TestP1SiblingDistinction:
    def test_duplicate_labels_are_flagged(self):
        f = check_sibling_distinction("drafts", [
            ("q2-forecast.md", ""), ("Q2 Forecast (copy).md", "")])
        assert len(f) == 1 and f[0].principle == "P1"
        assert len(f[0].items) == 2

    def test_distinct_descriptions_rescue_a_duplicate_name(self):
        """The contract is 'by name, at worst name plus description'. A real
        description separating them means the store is usable."""
        assert check_sibling_distinction("drafts", [
            ("q2-forecast.md", "the version sent to the board"),
            ("Q2 Forecast copy.md", "scratch working file, superseded"),
        ]) == []

    def test_empty_descriptions_do_not_rescue(self):
        assert check_sibling_distinction("drafts", [
            ("plan.md", ""), ("The Plan.md", "")]) != []

    def test_identical_descriptions_do_not_rescue(self):
        assert check_sibling_distinction("drafts", [
            ("plan.md", "a plan"), ("The Plan.md", "A PLAN")]) != []

    def test_clean_siblings_are_silent(self):
        assert check_sibling_distinction("space", [
            ("invoices.md", ""), ("suppliers.md", ""), ("shipping.md", "")]) == []

    def test_single_child_is_not_a_p1_problem(self):
        assert check_sibling_distinction("space", [("only.md", "")]) == []


class TestP5StructuralEconomy:
    def test_chain_node_is_flagged(self):
        f = check_structural_economy("a/b", child_count=1, grandchild_counts=[3])
        assert len(f) == 1 and f[0].principle == "P5"
        assert "single child" in f[0].detail

    def test_empty_level_is_flagged(self):
        f = check_structural_economy("a/b", child_count=0, grandchild_counts=[])
        assert len(f) == 1 and "nothing" in f[0].detail

    def test_branching_node_is_silent(self):
        assert check_structural_economy("a", 4, [1, 2, 0, 3]) == []

    def test_wide_node_is_not_flagged(self):
        """Deliberate: 'too many siblings' is a relatedness judgement (P2), not
        economy. Flagging it would push stores toward arbitrary sharding, which
        the paper observes agents already resist."""
        assert check_structural_economy("a", 40, [0] * 40) == []


class TestTaxonomyReport:
    GOOD = {
        "path": "space", "name": "space", "description": "", "kind": "folder", "children": [
            {"path": "space/invoices", "name": "invoices", "description": "billing", "kind": "folder",
             "children": [{"path": "space/invoices/2026.md", "name": "2026.md",
                           "description": "this year", "children": []},
                          {"path": "space/invoices/2025.md", "name": "2025.md",
                           "description": "last year", "children": []}]},
            {"path": "space/suppliers", "name": "suppliers", "description": "who we buy from", "kind": "folder",
             "children": [{"path": "space/suppliers/uk.md", "name": "uk.md",
                           "description": "domestic", "children": []},
                          {"path": "space/suppliers/eu.md", "name": "eu.md",
                           "description": "european", "children": []}]},
        ],
    }

    BAD = {
        "path": "space", "name": "space", "description": "", "kind": "folder", "children": [
            # P5: chain node — descending narrows nothing.
            {"path": "space/stuff", "name": "stuff", "description": "", "kind": "folder",
             "children": [{"path": "space/stuff/misc", "name": "misc", "description": "", "kind": "folder",
                           "children": [{"path": "space/stuff/misc/a.md", "name": "a.md",
                                         "description": "", "children": []}]}]},
            # P1: two siblings that normalise identically, no descriptions.
            {"path": "space/drafts", "name": "drafts", "description": "", "kind": "folder",
             "children": [{"path": "space/drafts/plan.md", "name": "plan.md",
                           "description": "", "children": []},
                          {"path": "space/drafts/The Plan (copy).md",
                           "name": "The Plan (copy).md", "description": "",
                           "children": []}]},
        ],
    }

    def test_well_shaped_store_reports_clean(self):
        r = taxonomy_report(self.GOOD)
        assert r["findings"] == [], r["findings"]
        assert r["counts"] == {}

    def test_badly_shaped_store_reports_both_principles(self):
        r = taxonomy_report(self.BAD)
        assert r["counts"].get("P1", 0) >= 1
        assert r["counts"].get("P5", 0) >= 1

    def test_report_carries_growth_counters(self):
        """The paper's finding is that adherence ERODES as a store grows, so a
        snapshot is much less useful than a series — the counters are what make
        drift visible."""
        r = taxonomy_report(self.GOOD)
        assert r["nodes"] > 0 and r["max_depth"] >= 2

    def test_unchecked_principles_are_named_not_silent(self):
        """Silence must never read as a pass on P2/P3/P4."""
        r = taxonomy_report(self.GOOD)
        assert set(r["not_checked"]) == {"P2", "P3", "P4"}
        assert all(r["not_checked"].values())

    def test_empty_store_does_not_crash(self):
        r = taxonomy_report({"path": "space", "name": "space", "kind": "folder",
                             "description": "", "children": []})
        assert isinstance(r["findings"], list)
