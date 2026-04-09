"""Tests for prax.services.memory.knowledge_graph."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — build a mock Neo4j session that records Cypher queries
# ---------------------------------------------------------------------------


class FakeRecord:
    """Minimal record returned by session.run().single()."""

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def __contains__(self, key):
        return key in self._data

    def get(self, key, default=None):
        return self._data.get(key, default)


class FakeResult:
    """Minimal result returned by session.run()."""

    def __init__(self, records: list[dict] | None = None, single_data: dict | None = None):
        self._records = records or []
        self._single_data = single_data

    def single(self):
        if self._single_data is not None:
            return FakeRecord(self._single_data)
        return None

    def __iter__(self):
        return iter(self._records)


class FakeSession:
    """Mock Neo4j session that stores calls and returns configurable results."""

    def __init__(self):
        self.queries: list[tuple[str, dict]] = []
        self._run_results: list[FakeResult] = []
        self._default_result = FakeResult()

    def set_results(self, results: list[FakeResult]):
        self._run_results = list(results)

    def run(self, query: str, **kwargs):
        self.queries.append((query, kwargs))
        if self._run_results:
            return self._run_results.pop(0)
        return self._default_result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _reset_kg_indexes():
    """Reset the indexes-created flag before each test."""
    import prax.services.memory.knowledge_graph as kg

    kg._kg_indexes_created = True  # Skip index creation in tests
    yield
    kg._kg_indexes_created = False


@pytest.fixture()
def fake_session():
    """Provide a FakeSession and patch _session() to return it."""
    session = FakeSession()
    with patch(
        "prax.services.memory.knowledge_graph._session",
        return_value=session,
    ):
        yield session


# ---------------------------------------------------------------------------
# list_namespaces
# ---------------------------------------------------------------------------


def test_list_namespaces_empty(fake_session):
    from prax.services.memory.knowledge_graph import list_namespaces

    fake_session._default_result = FakeResult(records=[])
    result = list_namespaces("user1")
    assert result == []
    assert len(fake_session.queries) == 1
    assert "KnowledgeConcept" in fake_session.queries[0][0]


def test_list_namespaces_returns_data(fake_session):
    from prax.services.memory.knowledge_graph import list_namespaces

    fake_session._default_result = FakeResult(
        records=[
            {"namespace": "papers", "concept_count": 5},
            {"namespace": "docs", "concept_count": 3},
        ]
    )
    result = list_namespaces("user1")
    assert len(result) == 2
    assert result[0]["namespace"] == "papers"
    assert result[0]["concept_count"] == 5


# ---------------------------------------------------------------------------
# add_concept
# ---------------------------------------------------------------------------


def test_add_concept_creates_node(fake_session):
    from prax.services.memory.knowledge_graph import add_concept

    fake_session._default_result = FakeResult(single_data={"id": "test-uuid"})
    cid = add_concept(
        user_id="user1",
        namespace="papers",
        name="Attention Mechanism",
        description="Core component of transformer architecture",
        source="attention.pdf",
        source_type="pdf",
        importance=0.8,
    )
    assert cid == "test-uuid"
    assert len(fake_session.queries) == 1
    query, params = fake_session.queries[0]
    assert "MERGE" in query
    assert "KnowledgeConcept" in query
    assert params["name"] == "attention mechanism"  # canonical lowercase
    assert params["ns"] == "papers"
    assert params["importance"] == 0.8


# ---------------------------------------------------------------------------
# add_knowledge_relation
# ---------------------------------------------------------------------------


def test_add_knowledge_relation(fake_session):
    from prax.services.memory.knowledge_graph import add_knowledge_relation

    fake_session._default_result = FakeResult(single_data={"cnt": 1})
    ok = add_knowledge_relation(
        user_id="user1",
        namespace="papers",
        source_name="Transformer",
        relation_type="uses",
        target_name="Attention Mechanism",
        evidence="Section 3.1",
    )
    assert ok is True
    query, params = fake_session.queries[0]
    assert "KNOWLEDGE_RELATES" in query
    assert params["src"] == "transformer"
    assert params["tgt"] == "attention mechanism"
    assert params["rtype"] == "uses"


def test_add_knowledge_relation_no_match(fake_session):
    from prax.services.memory.knowledge_graph import add_knowledge_relation

    fake_session._default_result = FakeResult(single_data={"cnt": 0})
    ok = add_knowledge_relation(
        user_id="user1",
        namespace="papers",
        source_name="A",
        relation_type="uses",
        target_name="B",
    )
    assert ok is False


# ---------------------------------------------------------------------------
# search_knowledge
# ---------------------------------------------------------------------------


def test_search_knowledge_finds_concepts(fake_session):
    from prax.services.memory.knowledge_graph import search_knowledge

    fake_session._default_result = FakeResult(
        records=[
            {
                "id": "c1",
                "name": "attention",
                "display_name": "Attention",
                "namespace": "papers",
                "description": "Self-attention mechanism",
                "importance": 0.8,
                "source": "paper.pdf",
                "source_type": "pdf",
            }
        ]
    )
    results = search_knowledge("user1", "attention")
    assert len(results) == 1
    assert results[0]["name"] == "attention"


def test_search_knowledge_with_namespace(fake_session):
    from prax.services.memory.knowledge_graph import search_knowledge

    fake_session._default_result = FakeResult(records=[])
    search_knowledge("user1", "transformer", namespace="docs")
    _, params = fake_session.queries[0]
    assert params["ns"] == "docs"


def test_search_knowledge_namespace_isolation(fake_session):
    """Concepts in 'papers' should not appear when searching 'docs'."""
    from prax.services.memory.knowledge_graph import search_knowledge

    # When searching with namespace="docs", the query filters by namespace
    fake_session._default_result = FakeResult(records=[])
    results = search_knowledge("user1", "attention", namespace="docs")
    assert results == []
    # Verify the query includes namespace filtering
    query, params = fake_session.queries[0]
    assert "namespace: $ns" in query
    assert params["ns"] == "docs"


# ---------------------------------------------------------------------------
# ingest_document (mock the LLM)
# ---------------------------------------------------------------------------


def test_ingest_document_extracts_concepts(fake_session):
    from prax.services.memory.knowledge_graph import ingest_document

    # Mock the LLM extraction
    mock_extraction = {
        "concepts": [
            {"name": "Neural Network", "description": "A computational model", "importance": 0.9},
            {"name": "Backpropagation", "description": "Training algorithm", "importance": 0.7},
        ],
        "relations": [
            {"source": "Neural Network", "type": "uses", "target": "Backpropagation", "evidence": "Section 2"},
        ],
    }

    with patch(
        "prax.services.memory.knowledge_graph._extract_concepts_and_relations",
        return_value=mock_extraction,
    ):
        # The session needs to return results for the MERGE and CREATE queries
        fake_session._default_result = FakeResult(single_data={"id": "test-id", "cnt": 1})
        result = ingest_document(
            user_id="user1",
            namespace="papers",
            title="Deep Learning Basics",
            content="Text about neural networks...",
            source_path="deep_learning.md",
            source_type="markdown",
        )

    assert result["concepts"] == 2
    assert result["relations"] == 1
    assert "document_id" in result


def test_ingest_document_empty_extraction(fake_session):
    from prax.services.memory.knowledge_graph import ingest_document

    with patch(
        "prax.services.memory.knowledge_graph._extract_concepts_and_relations",
        return_value={"concepts": [], "relations": []},
    ):
        fake_session._default_result = FakeResult(single_data={"id": "test-id"})
        result = ingest_document(
            user_id="user1",
            namespace="docs",
            title="Empty Doc",
            content="Nothing here",
            source_path="empty.md",
        )

    assert result["concepts"] == 0
    assert result["relations"] == 0


# ---------------------------------------------------------------------------
# delete_namespace
# ---------------------------------------------------------------------------


def test_delete_namespace(fake_session):
    from prax.services.memory.knowledge_graph import delete_namespace

    fake_session.set_results([
        FakeResult(),  # DELETE documents
        FakeResult(single_data={"deleted": 5}),  # DELETE concepts
    ])
    count = delete_namespace("user1", "papers")
    assert count == 5
    # Should have run two queries: one for docs, one for concepts
    assert len(fake_session.queries) == 2


# ---------------------------------------------------------------------------
# link_to_memory
# ---------------------------------------------------------------------------


def test_link_to_memory_creates_edge(fake_session):
    from prax.services.memory.knowledge_graph import link_to_memory

    fake_session._default_result = FakeResult(single_data={"cnt": 1})
    ok = link_to_memory("user1", "Transformer", "ML Research")
    assert ok is True
    query, params = fake_session.queries[0]
    assert "REFERENCES_ENTITY" in query
    assert params["cname"] == "transformer"
    assert params["ename"] == "ml research"


def test_link_to_memory_no_match(fake_session):
    from prax.services.memory.knowledge_graph import link_to_memory

    fake_session._default_result = FakeResult(single_data={"cnt": 0})
    ok = link_to_memory("user1", "nonexistent", "also_nonexistent")
    assert ok is False


# ---------------------------------------------------------------------------
# User isolation
# ---------------------------------------------------------------------------


def test_user_isolation_search(fake_session):
    """search_knowledge scopes queries by user_id."""
    from prax.services.memory.knowledge_graph import search_knowledge

    fake_session._default_result = FakeResult(records=[])

    search_knowledge("user_a", "topic")
    _, params_a = fake_session.queries[0]

    search_knowledge("user_b", "topic")
    _, params_b = fake_session.queries[1]

    assert params_a["uid"] == "user_a"
    assert params_b["uid"] == "user_b"


def test_user_isolation_add_concept(fake_session):
    """add_concept scopes by user_id."""
    from prax.services.memory.knowledge_graph import add_concept

    fake_session._default_result = FakeResult(single_data={"id": "x"})

    add_concept("user_a", "ns", "concept1")
    _, params = fake_session.queries[0]
    assert params["uid"] == "user_a"


# ---------------------------------------------------------------------------
# get_concept
# ---------------------------------------------------------------------------


def test_get_concept_found(fake_session):
    from prax.services.memory.knowledge_graph import get_concept

    mock_node = MagicMock()
    mock_node.get = lambda k, d=None: {
        "id": "c1",
        "name": "attention",
        "display_name": "Attention",
        "namespace": "papers",
        "description": "Self-attention",
        "source": "paper.pdf",
        "source_type": "pdf",
        "importance": 0.9,
        "created_at": "2025-01-01",
        "updated_at": "2025-01-01",
    }.get(k, d)

    fake_session._default_result = FakeResult(
        single_data={
            "c": mock_node,
            "relations": [
                {
                    "type": "uses",
                    "weight": 1.0,
                    "direction": "outgoing",
                    "other_name": "Transformer",
                    "other_namespace": "papers",
                    "evidence": "Section 3",
                }
            ],
        }
    )
    result = get_concept("user1", "attention", namespace="papers")
    assert result is not None
    assert result["name"] == "attention"
    assert len(result["relations"]) == 1


def test_get_concept_not_found(fake_session):
    from prax.services.memory.knowledge_graph import get_concept

    fake_session._default_result = FakeResult()
    result = get_concept("user1", "nonexistent", namespace="papers")
    assert result is None


# ---------------------------------------------------------------------------
# get_namespace_stats
# ---------------------------------------------------------------------------


def test_get_namespace_stats(fake_session):
    from prax.services.memory.knowledge_graph import get_namespace_stats

    fake_session._default_result = FakeResult(
        single_data={"concepts": 10, "relations": 5, "documents": 2}
    )
    stats = get_namespace_stats("user1", "papers")
    assert stats["namespace"] == "papers"
    assert stats["concepts"] == 10
    assert stats["relations"] == 5
    assert stats["documents"] == 2


def test_get_namespace_stats_empty(fake_session):
    from prax.services.memory.knowledge_graph import get_namespace_stats

    fake_session._default_result = FakeResult()
    stats = get_namespace_stats("user1", "empty_ns")
    assert stats["concepts"] == 0
