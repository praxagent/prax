"""The failure journal's Qdrant leg has to actually reach Qdrant.

It never did (2026-09 review): `_store_qdrant` called `upsert_memory` without
the required `dense_vector` (TypeError, swallowed at DEBUG) and with a point
id Qdrant would have rejected anyway (`"failure-<hex>"` is neither a UUID nor
an int); `search_similar_failures` passed the query STRING where
`search_dense` takes a vector, then read `.payload` off `MemoryResult`
objects that have no such attribute.  Both were caught by broad excepts and
returned nothing, so "find similar past failures" was a no-op with a
docstring.

These tests drive the real `upsert_memory` body against a fake Qdrant client
and assert the vector is on the point; and drive the search with a fake
`search_dense` that insists on a vector.
"""
from __future__ import annotations

import logging
import uuid
from unittest.mock import patch

import pytest

from prax.services.memory import failure_journal, vector_store
from prax.services.memory.embedder import EmbeddingUnavailableError
from prax.services.memory.failure_journal import (
    FAILURE_SOURCE,
    FailureCase,
    _failure_point_id,
    _store_qdrant,
    record_failure,
    search_similar_failures,
)
from prax.services.memory.models import MemoryResult

DENSE = [0.25] * 8
SPARSE = {3: 0.7, 9: 0.2}


class _RecordingQdrant:
    def __init__(self):
        self.points = []

    def upsert(self, collection_name, points):
        self.points.extend(points)


@pytest.fixture
def fake_qdrant():
    fake = _RecordingQdrant()
    with patch.object(vector_store, "_get_client", return_value=fake), \
         patch.object(vector_store, "_ensure_collection", lambda client: None), \
         patch("prax.services.memory.embedder.embed_text", return_value=DENSE), \
         patch("prax.services.memory.embedder.sparse_encode", return_value=SPARSE):
        yield fake


@pytest.fixture
def local_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(failure_journal, "_journal_dir", lambda: tmp_path)
    monkeypatch.setattr(failure_journal, "_store_neo4j", lambda c: None)
    monkeypatch.setattr(failure_journal, "_store_qdrant", lambda c: None)
    return tmp_path


def test_store_qdrant_upserts_a_point_with_the_dense_vector(fake_qdrant):
    case = FailureCase(user_id="u1", user_input="make a slide deck", agent_output="can't",
                       feedback_comment="you have a tool for that", failure_category="wrong_tool")

    _store_qdrant(case)

    assert len(fake_qdrant.points) == 1, "no point reached the client — the write was swallowed"
    point = fake_qdrant.points[0]
    assert point.vector["dense"] == DENSE
    assert "sparse" in point.vector
    assert point.payload["source"] == FAILURE_SOURCE
    assert point.payload["user_id"] == "u1"
    assert "failure" in point.payload["tags"] and "wrong_tool" in point.payload["tags"]
    assert "FAILURE: make a slide deck" in point.payload["content"]


def test_failure_point_id_is_a_deterministic_uuid():
    pid = _failure_point_id("abc123")
    assert pid == _failure_point_id("abc123")
    assert pid != _failure_point_id("abc124")
    assert str(uuid.UUID(pid)) == pid, "Qdrant point ids must be UUIDs (or unsigned ints)"


def test_stored_point_id_is_the_deterministic_uuid(fake_qdrant):
    case = FailureCase(user_id="u1", user_input="x", agent_output="y")
    _store_qdrant(case)
    assert str(fake_qdrant.points[0].id) == _failure_point_id(case.id)


def test_search_embeds_the_query_and_maps_hits_back_to_cases(local_journal):
    case = record_failure(user_id="u1", user_input="rename the branch", agent_output="deleted it",
                          feedback_comment="that was the wrong thing to do")
    other = record_failure(user_id="u1", user_input="unrelated", agent_output="fine",
                           feedback_comment="ok")
    received: list = []

    def _search_dense(user_id, query_vector, top_k=10, min_importance=0.0):
        received.append(query_vector)
        assert user_id == "u1"
        return [
            MemoryResult(memory_id=str(uuid.uuid4()), content="a chat memory", score=0.99,
                         source="conversation", importance=0.5, created_at=""),
            MemoryResult(memory_id=_failure_point_id(case.id), content="FAILURE: ...", score=0.9,
                         source=FAILURE_SOURCE, importance=0.8, created_at=""),
        ]

    with patch("prax.services.memory.embedder.embed_text", return_value=DENSE), \
         patch.object(vector_store, "search_dense", side_effect=_search_dense):
        hits = search_similar_failures("u1", "deleted a branch instead of renaming", top_k=5)

    assert received == [DENSE], "search_dense must be given the embedded query, not the text"
    assert [h.id for h in hits] == [case.id]
    assert other.id not in [h.id for h in hits]


def test_search_returns_empty_when_nothing_matches(local_journal):
    with patch("prax.services.memory.embedder.embed_text", return_value=DENSE), \
         patch.object(vector_store, "search_dense", return_value=[]):
        assert search_similar_failures("u1", "anything") == []


def test_store_qdrant_is_best_effort_and_says_so(caplog):
    """Embedding unavailable (keyless box): the local JSONL already has the
    case, so this must not raise — but it must not hide at DEBUG either."""
    case = FailureCase(user_id="u1", user_input="x", agent_output="y")
    with patch("prax.services.memory.embedder.embed_text",
               side_effect=EmbeddingUnavailableError("no provider")), \
         caplog.at_level(logging.WARNING, logger="prax.services.memory.failure_journal"):
        _store_qdrant(case)
    assert any("Qdrant leg" in r.getMessage() and case.id in r.getMessage()
               for r in caplog.records)
