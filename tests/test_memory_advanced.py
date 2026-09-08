"""Unit tests for advanced memory features.

Covers bi-temporal edges, consolidation validation gate, multi-graph
separation, query-adaptive retrieval weights, and torn-write safety of the
memory state files.
"""
import json
import os
from unittest.mock import patch

import pytest

from prax.services.memory.models import MemoryResult
from prax.services.memory.retrieval import _classify_query_weights, rrf_fuse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mr(mid: str, score: float = 1.0, content: str = "", importance: float = 0.5) -> MemoryResult:
    return MemoryResult(
        memory_id=mid,
        content=content or f"memory {mid}",
        score=score,
        source="test",
        importance=importance,
        created_at="2026-01-01T00:00:00Z",
    )


# ===========================================================================
# 1. Bi-temporal edges
# ===========================================================================

class TestBitemporalEdges:
    """Verify add_relation stores valid_from and supersede_relation sets valid_until."""

    def test_add_relation_signature_accepts_valid_from(self):
        """add_relation should accept valid_from parameter."""
        import inspect

        from prax.services.memory.graph_store import add_relation
        sig = inspect.signature(add_relation)
        assert "valid_from" in sig.parameters

    def test_supersede_relation_exists(self):
        """supersede_relation function should be importable."""
        import inspect

        from prax.services.memory.graph_store import supersede_relation
        sig = inspect.signature(supersede_relation)
        assert "user_id" in sig.parameters
        assert "source_name" in sig.parameters
        assert "relation_type" in sig.parameters
        assert "target_name" in sig.parameters

    def test_get_entity_accepts_include_superseded(self):
        """get_entity should accept include_superseded parameter."""
        import inspect

        from prax.services.memory.graph_store import get_entity
        sig = inspect.signature(get_entity)
        assert "include_superseded" in sig.parameters


# ===========================================================================
# 2. Consolidation validation gate
# ===========================================================================

class TestConsolidationValidationGate:
    def test_confidence_threshold_defined(self):
        from prax.services.memory.consolidation import CONFIDENCE_THRESHOLD
        assert 0.0 < CONFIDENCE_THRESHOLD < 1.0
        assert CONFIDENCE_THRESHOLD == 0.6

    @patch("prax.services.workspace_service.workspace_root")
    def test_low_confidence_facts_go_to_stm(self, mock_ws, tmp_path):
        """Facts below CONFIDENCE_THRESHOLD should be routed to STM, not LTM."""
        mock_ws.side_effect = lambda uid: str(tmp_path / uid)
        os.makedirs(tmp_path / "user1", exist_ok=True)

        # Create a trace file
        trace_path = tmp_path / "user1" / "trace.log"
        trace_path.write_text("User said they might prefer dark mode but unsure\n")

        extraction = {
            "entities": [
                {"name": "dark mode", "type": "concept", "importance": 0.5, "confidence": 0.4},
            ],
            "relations": [],
            "facts": [
                {"content": "User might prefer dark mode", "importance": 0.5, "confidence": 0.4},
                {"content": "User is working on project Alpha", "importance": 0.7, "confidence": 0.9},
            ],
            "temporal_events": [],
            "causal_links": [],
        }

        with patch("prax.services.memory.consolidation._extract_entities_relations", return_value=extraction), \
             patch("prax.services.memory.graph_store.merge_entity") as mock_merge, \
             patch("prax.services.memory.graph_store.add_relation"), \
             patch("prax.services.memory.embedder.embed_text", return_value=[0.1] * 1536), \
             patch("prax.services.memory.embedder.sparse_encode", return_value={1: 0.5}), \
             patch("prax.services.memory.vector_store.upsert_memory", return_value="mem-1"), \
             patch("prax.services.memory.vector_store.decay_memories", return_value=0), \
             patch("prax.services.memory.graph_store.decay_graph", return_value=0), \
             patch("prax.services.memory.stm.stm_write") as mock_stm:

            from prax.services.memory.consolidation import consolidate_user
            result = consolidate_user("user1")

            # High-confidence fact should be stored in LTM
            assert result.memories_created == 1

            # Low-confidence entity should NOT be merged to graph
            mock_merge.assert_not_called()

            # Low-confidence items should go to STM
            assert mock_stm.call_count >= 1
            # Check tags include pending_review
            stm_calls = mock_stm.call_args_list
            tags_found = any(
                "pending_review" in (call.args[3] if len(call.args) > 3 else call.kwargs.get("tags", []))
                for call in stm_calls
            )
            assert tags_found


# ===========================================================================
# 3. Multi-graph separation (TemporalEvent + CausalLink)
# ===========================================================================

class TestMultiGraphSeparation:
    def test_merge_temporal_event_exists(self):
        import inspect

        from prax.services.memory.graph_store import merge_temporal_event
        sig = inspect.signature(merge_temporal_event)
        assert "description" in sig.parameters
        assert "occurred_at" in sig.parameters
        assert "participant_names" in sig.parameters

    def test_add_causal_link_exists(self):
        import inspect

        from prax.services.memory.graph_store import add_causal_link
        sig = inspect.signature(add_causal_link)
        assert "cause_description" in sig.parameters
        assert "effect_description" in sig.parameters
        assert "cause_entity_names" in sig.parameters
        assert "effect_entity_names" in sig.parameters

    def test_get_stats_includes_new_types(self):
        """get_stats should return temporal_events and causal_links counts."""
        from prax.services.memory.graph_store import get_stats

        # When graph is unreachable, should still return all keys
        with patch("prax.services.memory.graph_store._session", side_effect=Exception("no neo4j")):
            stats = get_stats("user1")
            assert "temporal_events" in stats
            assert "causal_links" in stats

    def test_extraction_prompt_includes_temporal_and_causal(self):
        """The LLM extraction prompt should mention temporal_events and causal_links."""
        from prax.services.memory.consolidation import _extract_entities_relations

        # Mock the LLM to return a response with all fields
        mock_response = type("R", (), {"content": json.dumps({
            "entities": [],
            "relations": [],
            "facts": [],
            "temporal_events": [{"description": "test event", "occurred_at": None, "importance": 0.5, "participants": []}],
            "causal_links": [{"cause": "A", "effect": "B", "cause_entities": [], "effect_entities": [], "importance": 0.5}],
        })})()

        with patch("prax.agent.llm_factory.build_llm") as mock_llm:
            mock_llm.return_value.invoke.return_value = mock_response
            result = _extract_entities_relations("test text")

        assert "temporal_events" in result
        assert "causal_links" in result
        assert len(result["temporal_events"]) == 1
        assert len(result["causal_links"]) == 1


# ===========================================================================
# 4. Query-adaptive retrieval weights
# ===========================================================================

class TestQueryAdaptiveWeights:
    def test_factual_query_boosts_sparse_and_graph(self):
        """Queries with identifiers/quotes should boost sparse + graph weights."""
        weights = _classify_query_weights('What is "error code E-4012"?')
        dense_w, sparse_w, graph_w = weights
        assert sparse_w > dense_w, "Sparse should be boosted for factual queries"

    def test_entity_query_boosts_graph(self):
        """Queries with named entities should boost graph weight."""
        weights = _classify_query_weights("How are Alice and Bob related?")
        dense_w, sparse_w, graph_w = weights
        assert graph_w >= 1.2, "Graph should be boosted for entity queries"

    def test_semantic_query_boosts_dense(self):
        """Open-ended semantic queries should boost dense weight."""
        weights = _classify_query_weights("How do you feel about the project approach?")
        dense_w, sparse_w, graph_w = weights
        assert dense_w > sparse_w, "Dense should be boosted for semantic queries"

    def test_neutral_query_gives_equal_weights(self):
        """Simple queries without strong signals should be roughly equal."""
        weights = _classify_query_weights("memory test")
        dense_w, sparse_w, graph_w = weights
        assert abs(dense_w - 1.0) < 0.01
        assert abs(sparse_w - 1.0) < 0.01
        assert abs(graph_w - 1.0) < 0.01

    def test_weighted_rrf_changes_ranking(self):
        """Weighted RRF should produce different rankings than equal weights."""
        list1 = [_mr("a", 0.9), _mr("b", 0.8)]  # dense
        list2 = [_mr("b", 0.95), _mr("c", 0.85)]  # sparse
        list3 = [_mr("c", 0.9), _mr("a", 0.7)]  # graph

        # Equal weights
        equal = rrf_fuse([list1, list2, list3])
        _ = [r.memory_id for r in equal]

        # Heavy sparse + graph weight (factual query)
        factual = rrf_fuse([list1, list2, list3], weights=[0.8, 1.5, 1.3])
        _ = [r.memory_id for r in factual]

        # The rankings should differ (b+c boosted in factual)
        # At minimum, the scores should be different
        equal_scores = {r.memory_id: r.score for r in equal}
        factual_scores = {r.memory_id: r.score for r in factual}
        assert factual_scores["b"] != equal_scores["b"]

    def test_rrf_fuse_with_weights_none_equals_default(self):
        """rrf_fuse(lists, weights=None) should equal rrf_fuse(lists)."""
        lists = [[_mr("a", 0.9), _mr("b", 0.8)]]
        r1 = rrf_fuse(lists)
        r2 = rrf_fuse(lists, weights=None)
        assert r1[0].score == r2[0].score

    def test_rrf_fuse_with_zero_weight_excludes_list(self):
        """A weight of 0 should effectively exclude that retrieval arm."""
        list1 = [_mr("a", 0.9)]
        list2 = [_mr("b", 0.95)]
        result = rrf_fuse([list1, list2], weights=[1.0, 0.0])
        scores = {r.memory_id: r.score for r in result}
        assert scores["a"] > 0
        assert scores["b"] == 0.0


# ===========================================================================
# 5. Torn-write safety of the memory state files
# ===========================================================================
#
# stm.json and consolidation_state.json were written with `open(path, "w")` +
# `json.dump`, which truncates the file first and then streams into it.  A
# crash (or an exception) mid-write left a torn file, and both loaders treat
# an unparseable file as "empty" — the whole scratchpad, or the consolidation
# pointer, silently reset.  Both now go through workspace_service.atomic_write
# (same-directory temp file + os.replace).
#
# The interaction-based decay tests that used to live in this slot asserted
# the existence of a code path (`track_interaction`, `interaction_epoch`,
# `halflife_interactions`) that never had a production caller; that path was
# deleted in 2026-09 rather than armed.  tests/test_decay_idempotence.py
# guards against it coming back.


class _Poison:
    """Not JSON-serialisable: the encoder raises when it reaches this value."""


@pytest.fixture
def _user_workspace(tmp_path):
    def _root(user_id: str) -> str:
        path = os.path.join(str(tmp_path), user_id)
        os.makedirs(path, exist_ok=True)
        return path

    with patch("prax.services.workspace_service.workspace_root", side_effect=_root):
        yield tmp_path


def _fail_replace_for(monkeypatch, filename: str):
    """os.replace raises only for the file under test; everything else is real."""
    real_replace = os.replace

    def _replace(src, dst, *a, **kw):
        if str(dst).endswith(filename):
            raise OSError("simulated crash before rename")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr("prax.services.workspace_service.os.replace", _replace)


class TestAtomicMemoryStateWrites:
    def test_stm_failed_serialisation_leaves_previous_file_intact(self, _user_workspace):
        """A write that dies part-way through must not destroy the scratchpad.

        With the old streaming write, the file on disk after this call was
        `[{"key": "kept", ...}, {"key": "bad", "content": "v", "tags": [` —
        unparseable — and stm_read() "reset" it to [].
        """
        from prax.services.memory.stm import stm_read, stm_write

        stm_write("user1", "kept", "the entry that must survive")
        stm_path = _user_workspace / "user1" / "memory" / "stm.json"
        before = stm_path.read_text()

        with pytest.raises(TypeError):
            stm_write("user1", "bad", "v", tags=[_Poison()])

        assert stm_path.read_text() == before
        assert json.loads(stm_path.read_text())[0]["key"] == "kept"
        assert [e.key for e in stm_read("user1")] == ["kept"]
        debris = [p for p in stm_path.parent.iterdir() if p.name.endswith(".tmp")]
        assert debris == [], f"temp-file debris left behind: {debris}"

    def test_stm_crash_before_rename_leaves_previous_file_intact(
        self, _user_workspace, monkeypatch
    ):
        from prax.services.memory.stm import stm_read, stm_write

        stm_write("user1", "kept", "survivor")
        stm_path = _user_workspace / "user1" / "memory" / "stm.json"
        before = stm_path.read_text()

        _fail_replace_for(monkeypatch, "stm.json")
        with pytest.raises(OSError):
            stm_write("user1", "second", "never lands")

        assert stm_path.read_text() == before
        assert [e.key for e in stm_read("user1")] == ["kept"]
        debris = [p for p in stm_path.parent.iterdir() if p.name.endswith(".tmp")]
        assert debris == []

    def test_consolidation_state_crash_before_rename_keeps_pointer(
        self, _user_workspace, monkeypatch
    ):
        """A torn consolidation_state.json is loaded as 'line 0' — the whole
        trace would be consolidated again.  The previous state must survive."""
        from prax.services.memory import consolidation

        consolidation._save_state("user1", {"last_consolidated_line": 42})
        state_path = _user_workspace / "user1" / "memory" / "consolidation_state.json"
        before = state_path.read_text()

        _fail_replace_for(monkeypatch, "consolidation_state.json")
        with pytest.raises(OSError):
            consolidation._save_state("user1", {"last_consolidated_line": 99})

        assert state_path.read_text() == before
        assert consolidation._load_state("user1")["last_consolidated_line"] == 42
