"""The symbolic consistency pass: detection by the graph, not the LLM.

The failure this guards: `supersede_relation()` fires only when the extraction
LLM volunteers a `supersedes` marker — the graph is never asked. A missed
contradiction leaves both edges current forever. These tests hold the pass to
its contract:

* the GRAPH answers "is there a conflicting current edge?", not the model;
* log-only is the default acting mode — detection without deletion — because
  the single-valued allowlist is authored and must earn trust before it acts;
* multi-valued relation types are never touched (a person works_on many
  things; that is not a contradiction);
* an unreachable graph degrades to no-op, never to a crash or a supersession.
"""

from unittest.mock import patch

import pytest

from prax.services.memory import consistency
from prax.services.memory.models import ConsolidationResult


@pytest.fixture
def result():
    return ConsolidationResult()


def _rel(source="tj", rtype="lives_in", target="lisbon"):
    return {"source": source, "type": rtype, "target": target}


class TestDetectionIsSymbolic:
    def test_conflict_found_by_graph_query(self, result):
        """The graph, not the extractor, reports the existing current target."""
        with patch("prax.services.memory.graph_store.current_targets",
                   return_value=["porto"]) as q:
            with patch.object(consistency.settings, "memory_consistency_mode", "log", create=True):
                consistency.enforce("u1", _rel(target="lisbon"), result)
        q.assert_called_once_with(user_id="u1", source_name="tj",
                                  relation_type="lives_in")
        assert result.conflicts_detected == 1

    def test_same_target_is_reaffirmation_not_conflict(self, result):
        """Re-learning the same fact strengthens the edge; nothing to flag."""
        with patch("prax.services.memory.graph_store.current_targets",
                   return_value=["lisbon"]):
            consistency.enforce("u1", _rel(target="Lisbon"), result)
        assert result.conflicts_detected == 0

    def test_multi_valued_types_are_never_queried(self, result):
        """`prefers` coexists legitimately (dark mode AND coffee) — the pass
        must not touch any type outside the declared allowlist."""
        with patch("prax.services.memory.graph_store.current_targets") as q:
            for rtype in ("works_on", "interested_in", "prefers", "related_to",
                          "part_of", "caused_by", "mentioned_with"):
                consistency.enforce("u1", _rel(rtype=rtype), result)
        q.assert_not_called()
        assert result.conflicts_detected == 0

    def test_allowlist_contains_no_extraction_default_types(self):
        """The extraction prompt's default vocabulary is all multi-valued; if
        one of those ever lands in SINGLE_VALUED_TYPES the pass starts flagging
        legitimate coexistence as contradiction."""
        defaults = {"works_on", "interested_in", "prefers", "related_to",
                    "part_of", "caused_by", "mentioned_with"}
        assert not (consistency.SINGLE_VALUED_TYPES & defaults)


class TestActingModes:
    def test_log_only_counts_but_never_supersedes(self, result):
        """The default mode: a wrong allowlist entry must cost a log line,
        not a fact."""
        with patch("prax.services.memory.graph_store.current_targets",
                   return_value=["porto"]), \
             patch("prax.services.memory.graph_store.supersede_relation") as sup, \
             patch.object(consistency.settings, "memory_consistency_mode", "log", create=True):
            consistency.enforce("u1", _rel(), result)
        sup.assert_not_called()
        assert result.conflicts_detected == 1
        assert result.conflicts_superseded == 0

    def test_auto_supersede_closes_the_stale_edge(self, result):
        with patch("prax.services.memory.graph_store.current_targets",
                   return_value=["porto"]), \
             patch("prax.services.memory.graph_store.supersede_relation",
                   return_value=True) as sup, \
             patch.object(consistency.settings, "memory_consistency_mode", "enforce", create=True):
            consistency.enforce("u1", _rel(target="lisbon"), result)
        sup.assert_called_once_with(user_id="u1", source_name="tj",
                                    relation_type="lives_in",
                                    target_name="porto")
        assert result.conflicts_superseded == 1

    def test_failed_supersession_is_not_counted_as_done(self, result):
        """Honesty in the counters: a supersede that returned False (or blew
        up) must not be reported as a closed edge."""
        with patch("prax.services.memory.graph_store.current_targets",
                   return_value=["porto"]), \
             patch("prax.services.memory.graph_store.supersede_relation",
                   side_effect=RuntimeError("neo4j down")), \
             patch.object(consistency.settings, "memory_consistency_mode", "enforce", create=True):
            consistency.enforce("u1", _rel(), result)
        assert result.conflicts_detected == 1
        assert result.conflicts_superseded == 0


class TestDegradation:
    def test_unreachable_graph_is_a_noop(self, result):
        """Consolidation must keep working on a lite deployment (no Neo4j)."""
        with patch("prax.services.memory.graph_store.current_targets",
                   side_effect=RuntimeError("no graph")):
            consistency.enforce("u1", _rel(), result)
        assert result.conflicts_detected == 0

    def test_off_means_prior_behaviour(self):
        with patch.object(consistency.settings, "memory_consistency_mode", "off", create=True):
            assert consistency.enabled() is False
            assert consistency.enforcing() is False

    def test_log_detects_but_never_enforces(self):
        with patch.object(consistency.settings, "memory_consistency_mode", "log", create=True):
            assert consistency.enabled() is True
            assert consistency.enforcing() is False

    def test_enforce_does_both(self):
        with patch.object(consistency.settings, "memory_consistency_mode", "enforce", create=True):
            assert consistency.enabled() is True
            assert consistency.enforcing() is True

    def test_prompt_addendum_only_appears_when_enabled(self):
        """With the flag off the extraction prompt is byte-identical to
        before this feature existed."""
        from prax.services.memory.consolidation import _consistency_addendum

        with patch.object(consistency.settings, "memory_consistency_mode", "off", create=True):
            assert _consistency_addendum() == ""
        with patch.object(consistency.settings, "memory_consistency_mode", "log", create=True):
            addendum = _consistency_addendum()
        assert "single-valued" in addendum
        for rtype in consistency.SINGLE_VALUED_TYPES:
            assert rtype in addendum
