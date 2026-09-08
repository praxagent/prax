"""A failed memory write must surface as a failure, at every layer.

`vector_store.upsert_memory` used to catch every exception from Qdrant and
`return mid` anyway.  Every caller keyed success on "got an id back", so:

* `MemoryService.remember` returned the id -> the `memory_remember` tool said
  "Remembered (id=...)" and the TeamWork API answered 201 with a memory_id,
* consolidation counted `memories_created += 1`,

for writes that never happened.  It now raises `MemoryWriteError`; the
callers that already had a "no id" branch take it, and consolidation counts
the failure separately.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from prax.services.memory import consolidation, vector_store
from prax.services.memory.vector_store import MemoryWriteError


class _FailingQdrant:
    def upsert(self, collection_name, points):
        raise ConnectionError("qdrant unreachable")


class _RecordingQdrant:
    def __init__(self):
        self.points = []

    def upsert(self, collection_name, points):
        self.points.extend(points)


@pytest.fixture
def failing_store():
    with patch.object(vector_store, "_get_client", return_value=_FailingQdrant()), \
         patch.object(vector_store, "_ensure_collection", lambda client: None):
        yield


@pytest.fixture
def enabled_service():
    with patch("prax.services.memory_service.settings") as mock_settings:
        mock_settings.memory_enabled = True
        import prax.services.memory_service as mod

        mod._instance = None
        yield mod.get_memory_service()
        mod._instance = None


def test_upsert_raises_instead_of_returning_an_id(failing_store):
    with pytest.raises(MemoryWriteError) as info:
        vector_store.upsert_memory("u1", "a fact", dense_vector=[0.1] * 8)
    assert isinstance(info.value.__cause__, ConnectionError)


def test_upsert_returns_the_id_when_the_write_lands():
    """Equivalence on the success path: caller-supplied id comes back unchanged."""
    fake = _RecordingQdrant()
    with patch.object(vector_store, "_get_client", return_value=fake), \
         patch.object(vector_store, "_ensure_collection", lambda client: None):
        mid = vector_store.upsert_memory(
            "u1", "a fact", dense_vector=[0.1] * 8, sparse_vector={2: 0.4},
            memory_id="11111111-1111-1111-1111-111111111111",
        )
    assert mid == "11111111-1111-1111-1111-111111111111"
    assert len(fake.points) == 1
    assert fake.points[0].vector["dense"] == [0.1] * 8
    assert fake.points[0].payload["content"] == "a fact"


def test_memory_service_remember_returns_empty_on_failed_write(failing_store, enabled_service):
    with patch("prax.services.memory.embedder.embed_text", return_value=[0.1] * 8), \
         patch("prax.services.memory.embedder.sparse_encode", return_value={1: 0.5}):
        assert enabled_service.remember("u1", "I prefer dark mode") == ""


def test_memory_remember_tool_reports_failure_not_an_id(failing_store, enabled_service):
    from prax.agent.memory_tools import memory_remember

    with patch("prax.services.memory_service.get_memory_service", return_value=enabled_service), \
         patch("prax.services.memory.embedder.embed_text", return_value=[0.1] * 8), \
         patch("prax.services.memory.embedder.sparse_encode", return_value={1: 0.5}):
        out = memory_remember.invoke({"content": "the user prefers dark mode"})

    assert "Remembered" not in out
    assert "id=" not in out
    assert out == "Failed to store memory."


def test_consolidation_counts_failed_writes_separately(failing_store, tmp_path):
    def _root(user_id: str) -> str:
        path = os.path.join(str(tmp_path), user_id)
        os.makedirs(path, exist_ok=True)
        return path

    extraction = {
        "entities": [], "relations": [], "temporal_events": [], "causal_links": [],
        "facts": [
            {"content": "User is building a Rust CLI called fzgrep", "importance": 0.8, "confidence": 0.95},
            {"content": "User works from Lisbon most of the year", "importance": 0.6, "confidence": 0.9},
        ],
    }
    (tmp_path / "u1").mkdir()
    (tmp_path / "u1" / "trace.log").write_text("[USER] some trace content\n")

    with patch("prax.services.workspace_service.workspace_root", side_effect=_root), \
         patch.object(consolidation, "_extract_entities_relations", return_value=extraction), \
         patch.object(consolidation, "_build_daily_summary", return_value=""), \
         patch("prax.services.memory.embedder.embed_text", return_value=[0.1] * 8), \
         patch("prax.services.memory.embedder.sparse_encode", return_value={1: 0.5}), \
         patch("prax.services.memory.vector_store.decay_memories", return_value=0), \
         patch("prax.services.memory.graph_store.decay_graph", return_value=0):
        result = consolidation.consolidate_user("u1")

    assert result.memories_created == 0, "nothing was written, nothing may be counted as created"
    assert result.memories_failed == 2
