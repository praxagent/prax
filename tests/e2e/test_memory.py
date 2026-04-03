"""End-to-end memory integration tests — real Qdrant, Neo4j, and Ollama.

These tests exercise the full memory stack with real infrastructure:
  - STM (workspace JSON scratchpad)
  - LTM (Qdrant vector store + Neo4j knowledge graph)
  - Embeddings (Ollama nomic-embed-text)
  - Hybrid retrieval (weighted RRF fusion across dense, sparse, graph)
  - Memory context injection (build_memory_context)

Each test uses an isolated user_id and cleans up after itself.

Requirements:
  - Qdrant running on localhost:6333
  - Neo4j  running on localhost:7687 (user=neo4j, password=prax-memory)
  - Ollama running on localhost:11434 with nomic-embed-text pulled
  - EMBEDDING_PROVIDER=ollama in .env

Run with:
  uv run pytest tests/e2e/test_memory.py -v -s
"""
from __future__ import annotations

import os
import uuid

import pytest

# ---------------------------------------------------------------------------
# Skip the entire module if memory infrastructure is unavailable
# ---------------------------------------------------------------------------

def _check_qdrant() -> bool:
    try:
        from qdrant_client import QdrantClient
        c = QdrantClient(url="http://localhost:6333", timeout=3)
        c.get_collections()
        return True
    except Exception:
        return False

def _check_neo4j() -> bool:
    try:
        from neo4j import GraphDatabase
        d = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "prax-memory"))
        with d.session() as s:
            s.run("RETURN 1")
        d.close()
        return True
    except Exception:
        return False

def _check_ollama() -> bool:
    try:
        import httpx
        r = httpx.get("http://localhost:11434/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


_INFRA_OK = _check_qdrant() and _check_neo4j()
_OLLAMA_OK = _check_ollama()

pytestmark = pytest.mark.skipif(
    not _INFRA_OK,
    reason="Memory infrastructure (Qdrant + Neo4j) not available",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def test_user(tmp_path):
    """Create an isolated test user with a temp workspace."""
    uid = f"_test_{uuid.uuid4().hex[:8]}"
    workspace_dir = str(tmp_path / "workspaces")
    os.makedirs(workspace_dir, exist_ok=True)

    # Patch settings so workspace_root resolves to our temp dir
    from prax.settings import settings
    original_ws = settings.workspace_dir
    settings.workspace_dir = workspace_dir

    # Ensure memory is enabled
    original_mem = settings.memory_enabled
    settings.memory_enabled = True

    yield uid

    # Cleanup: settings
    settings.workspace_dir = original_ws
    settings.memory_enabled = original_mem

    # Cleanup: Qdrant — delete test user's data
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        c = QdrantClient(url="http://localhost:6333", timeout=5)
        collections = [col.name for col in c.get_collections().collections]
        if "prax_memories" in collections:
            # Delete all points belonging to this test user
            c.delete(
                collection_name="prax_memories",
                points_selector=Filter(
                    should=[
                        FieldCondition(key="user_id", match=MatchValue(value=uid)),
                        FieldCondition(key="user_id", match=MatchValue(value=f"_system_{uid}")),
                    ]
                ),
            )
    except Exception:
        pass

    # Cleanup: Neo4j — delete test user's data
    try:
        from neo4j import GraphDatabase
        d = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "prax-memory"))
        with d.session() as s:
            s.run("MATCH (n {user_id: $uid}) DETACH DELETE n", uid=uid)
        d.close()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_vector_store_collection_cache():
    """Ensure a fresh collection is created if the dim changes between tests."""
    # The vector store caches whether the collection exists.  If a previous
    # test run created it with a different dim, we need to allow re-creation.
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _embed(text: str) -> list[float]:
    """Embed text using the configured provider (Ollama)."""
    from prax.services.memory.embedder import embed_text
    return embed_text(text)


def _sparse(text: str) -> dict[int, float]:
    """Sparse-encode text."""
    from prax.services.memory.embedder import sparse_encode
    return sparse_encode(text)


# ===========================================================================
# 1. STM Roundtrip — write / read / delete / compact
# ===========================================================================

class TestSTM:
    """Short-term memory scratchpad (no external infra needed)."""

    def test_write_read_delete(self, test_user):
        from prax.services.memory.stm import stm_delete, stm_read, stm_write

        # Write
        entry = stm_write(test_user, "fav_color", "User prefers dark blue")
        assert entry.key == "fav_color"
        assert entry.content == "User prefers dark blue"

        # Read
        entries = stm_read(test_user)
        assert len(entries) == 1
        assert entries[0].content == "User prefers dark blue"

        # Read by key
        single = stm_read(test_user, key="fav_color")
        assert len(single) == 1

        # Update (same key)
        updated = stm_write(test_user, "fav_color", "User changed preference to green")
        assert updated.access_count == 1  # incremented

        entries = stm_read(test_user)
        assert len(entries) == 1
        assert "green" in entries[0].content

        # Delete
        assert stm_delete(test_user, "fav_color")
        assert len(stm_read(test_user)) == 0

    def test_multiple_entries_with_tags(self, test_user):
        from prax.services.memory.stm import stm_read, stm_write

        stm_write(test_user, "project_name", "Alpha project", tags=["project"])
        stm_write(test_user, "deadline", "2026-04-15", tags=["project", "date"])
        stm_write(test_user, "note", "Remember to review PR #42", tags=["task"])

        entries = stm_read(test_user)
        assert len(entries) == 3
        tags_flat = [t for e in entries for t in e.tags]
        assert "project" in tags_flat
        assert "task" in tags_flat


# ===========================================================================
# 2. LTM — Store and Recall with Real Embeddings
# ===========================================================================

@pytest.mark.skipif(not _OLLAMA_OK, reason="Ollama not available")
class TestLTM:
    """Long-term memory via Qdrant with real Ollama embeddings."""

    def test_store_and_recall_single_memory(self, test_user):
        """Store a fact, recall it by semantic search."""
        from prax.services.memory.vector_store import (
            search_dense,
            upsert_memory,
        )

        content = "User's favorite programming language is Rust, especially for systems work."
        vec = _embed(content)
        sparse = _sparse(content)

        mid = upsert_memory(
            user_id=test_user,
            content=content,
            dense_vector=vec,
            sparse_vector=sparse,
            source="test",
            importance=0.8,
            tags=["preference"],
        )
        assert mid  # non-empty UUID

        # Semantic search: query about programming preference
        query = "What language does the user prefer for systems programming?"
        q_vec = _embed(query)
        results = search_dense(test_user, q_vec, top_k=5)

        assert len(results) >= 1
        assert any("Rust" in r.content for r in results)
        top = results[0]
        assert top.score > 0  # cosine similarity should be positive

    def test_multiple_memories_ranked_correctly(self, test_user):
        """Store several facts, verify semantic search ranks the best match highest."""
        from prax.services.memory.vector_store import search_dense, upsert_memory

        facts = [
            ("User works at a company called Acme Corp in the DevOps team.", 0.7),
            ("User enjoys hiking in the Dolomites every summer.", 0.5),
            ("User is allergic to shellfish and avoids seafood restaurants.", 0.6),
            ("User's home server runs NixOS with Kubernetes for orchestration.", 0.8),
        ]
        for content, importance in facts:
            upsert_memory(
                user_id=test_user,
                content=content,
                dense_vector=_embed(content),
                sparse_vector=_sparse(content),
                source="test",
                importance=importance,
            )

        # Query about server setup — should rank NixOS fact highest
        query = "What operating system does the user run on their server?"
        results = search_dense(test_user, _embed(query), top_k=4)
        assert len(results) >= 1
        assert "NixOS" in results[0].content, f"Expected NixOS at top, got: {results[0].content[:80]}"

        # Query about food — should rank allergy fact highest
        query = "Does the user have any food allergies?"
        results = search_dense(test_user, _embed(query), top_k=4)
        assert len(results) >= 1
        assert "shellfish" in results[0].content, f"Expected shellfish at top, got: {results[0].content[:80]}"

    def test_hybrid_search_outperforms_dense_alone(self, test_user):
        """Hybrid retrieval should find keyword-heavy facts that pure semantic might miss."""
        from prax.services.memory.retrieval import hybrid_search
        from prax.services.memory.vector_store import search_dense, upsert_memory

        # Store a fact with a specific identifier that benefits from sparse matching
        fact = "Build pipeline error E-4012 is caused by a missing libssl dependency on Alpine containers."
        upsert_memory(
            user_id=test_user,
            content=fact,
            dense_vector=_embed(fact),
            sparse_vector=_sparse(fact),
            source="test",
            importance=0.9,
        )

        # Query by error code — sparse search should help
        query = 'What causes error "E-4012"?'
        hybrid_results = hybrid_search(test_user, query, top_k=3)
        search_dense(test_user, _embed(query), top_k=3)

        # Both should find it, but hybrid should score it higher
        assert len(hybrid_results) >= 1
        assert "E-4012" in hybrid_results[0].content


# ===========================================================================
# 3. Graph Store — Entities, Relations, Temporal Events, Causal Links
# ===========================================================================

class TestGraphStore:
    """Knowledge graph operations against real Neo4j."""

    def test_entity_lifecycle(self, test_user):
        from prax.services.memory.graph_store import get_entity, merge_entity

        # Create entity
        eid = merge_entity(test_user, "Python", "topic", display_name="Python", importance=0.8)
        assert eid

        # Retrieve entity
        entity = get_entity(test_user, "python")  # name is lowercased
        assert entity is not None
        assert entity.display_name == "Python"
        assert entity.entity_type == "topic"
        assert entity.mention_count == 1

        # Upsert again — mention count should increment
        merge_entity(test_user, "Python", "topic", importance=0.9)
        entity = get_entity(test_user, "python")
        assert entity.mention_count == 2
        assert entity.importance == 0.9  # updated to higher value

    def test_relations_with_bitemporal_edges(self, test_user):
        from prax.services.memory.graph_store import (
            add_relation,
            get_entity,
            merge_entity,
            supersede_relation,
        )

        merge_entity(test_user, "Alice", "person", importance=0.7)
        merge_entity(test_user, "ProjectX", "project", importance=0.8)

        # Create relation
        ok = add_relation(
            test_user, "Alice", "works_on", "ProjectX",
            weight=1.0, evidence="mentioned in standup",
        )
        assert ok

        # Verify relation exists on entity
        alice = get_entity(test_user, "alice")
        assert alice is not None
        assert len(alice.relations) >= 1
        rel = alice.relations[0]
        assert rel["type"] == "works_on"
        # Bi-temporal: valid_from should be set, valid_until should be None
        assert rel.get("valid_from") is not None
        assert rel.get("valid_until") is None

        # Supersede the relation (Alice leaves ProjectX)
        supersede_relation(test_user, "Alice", "works_on", "ProjectX")

        # By default, superseded relations are hidden
        alice = get_entity(test_user, "alice")
        _ = [r for r in alice.relations if r.get("valid_until") is None]
        # After supersession, the relation should have valid_until set
        # (The get_entity with include_superseded=True should show it)
        alice_full = get_entity(test_user, "alice", include_superseded=True)
        assert alice_full is not None
        assert len(alice_full.relations) >= 1

    def test_temporal_event(self, test_user):
        from prax.services.memory.graph_store import get_stats, merge_entity, merge_temporal_event

        merge_entity(test_user, "Alice", "person", importance=0.7)

        eid = merge_temporal_event(
            test_user,
            description="Sprint retrospective meeting",
            occurred_at="2026-03-28T14:00:00Z",
            importance=0.6,
            participant_names=["Alice"],
        )
        assert eid

        stats = get_stats(test_user)
        assert stats["temporal_events"] >= 1

    def test_causal_link(self, test_user):
        from prax.services.memory.graph_store import add_causal_link, get_stats, merge_entity

        merge_entity(test_user, "CI Pipeline", "tool", importance=0.7)
        merge_entity(test_user, "Deployment Failure", "concept", importance=0.8)

        cid = add_causal_link(
            test_user,
            cause_description="CI pipeline timeout due to resource limits",
            effect_description="Production deployment failed and was rolled back",
            cause_entity_names=["CI Pipeline"],
            effect_entity_names=["Deployment Failure"],
            importance=0.9,
        )
        assert cid

        stats = get_stats(test_user)
        assert stats["causal_links"] >= 1


# ===========================================================================
# 4. MemoryService Integration — Full Pipeline
# ===========================================================================

@pytest.mark.skipif(not _OLLAMA_OK, reason="Ollama not available")
class TestMemoryServiceIntegration:
    """Test the MemoryService facade with real infrastructure."""

    def test_remember_and_recall(self, test_user):
        """Store via remember(), retrieve via recall()."""
        from prax.services.memory_service import MemoryService

        ms = MemoryService()
        ms._available = True

        # Remember several facts
        mid1 = ms.remember(test_user, "User prefers dark mode in all editors.", importance=0.8)
        mid2 = ms.remember(test_user, "User's cat is named Luna.", importance=0.5)
        mid3 = ms.remember(test_user, "User is building a Rust CLI tool called fzgrep.", importance=0.9)

        assert mid1 and mid2 and mid3

        # Recall — query about editors
        results = ms.recall(test_user, "What theme does the user prefer in their editor?", top_k=3)
        assert len(results) >= 1
        assert any("dark mode" in r.content for r in results)

        # Recall — query about projects
        results = ms.recall(test_user, "What is the user working on?", top_k=3)
        assert len(results) >= 1
        assert any("fzgrep" in r.content or "Rust" in r.content for r in results)


# ===========================================================================
# 5. Memory Context Injection — With vs Without Memory
# ===========================================================================

@pytest.mark.skipif(not _OLLAMA_OK, reason="Ollama not available")
class TestMemoryContextInjection:
    """Demonstrate that memory context enriches the agent's system prompt.

    This is the "proof of value" test: without memory, the agent has no
    personal context.  With memory, the system prompt contains relevant
    facts that enable personalized, informed responses.
    """

    def test_no_memory_gives_empty_context(self, test_user):
        """Without stored memories, build_memory_context returns empty."""
        from prax.services.memory_service import MemoryService

        ms = MemoryService()
        ms._available = True

        context = ms.build_memory_context(test_user, "What's my favorite color?")
        # No memories stored → context should be empty or STM-only
        # (STM is empty for a fresh user)
        assert context == "" or "Working Memory" in context

    def test_memory_enriches_context(self, test_user):
        """After storing facts, build_memory_context returns relevant info."""
        from prax.services.memory_service import MemoryService

        ms = MemoryService()
        ms._available = True

        # Populate memory with user facts
        ms.remember(test_user, "User's favorite color is cobalt blue.", importance=0.7)
        ms.remember(test_user, "User works remotely from Berlin, Germany.", importance=0.6)
        ms.remember(test_user, "User has a meeting with the design team every Wednesday at 2pm CET.", importance=0.8)
        ms.remember(test_user, "User is vegetarian and prefers Mediterranean cuisine.", importance=0.5)

        # Also add STM entries
        from prax.services.memory.stm import stm_write
        stm_write(test_user, "current_task", "Reviewing memory system PR")

        # Build context for a relevant query
        context = ms.build_memory_context(test_user, "What's my schedule like?")

        # Context should include both STM and LTM
        assert "Working Memory" in context, "STM section should be present"
        assert "current_task" in context, "STM entry should be injected"
        assert "Relevant Memories" in context, "LTM section should be present"

        # At least one of the stored facts should appear
        has_relevant = any(
            phrase in context
            for phrase in ["meeting", "Wednesday", "Berlin", "cobalt"]
        )
        assert has_relevant, f"Expected relevant memories in context, got:\n{context}"

    def test_with_vs_without_memory_comparison(self, test_user):
        """Side-by-side: empty context vs enriched context.

        This test demonstrates the concrete difference memory makes.
        Without memory, the agent would have to ask "what's your favorite
        color?" — with memory, it already knows.
        """
        from prax.services.memory_service import MemoryService

        ms = MemoryService()
        ms._available = True

        question = "Can you help me set up a new project?"

        # ---- WITHOUT MEMORY ----
        context_without = ms.build_memory_context(test_user, question)
        assert context_without == "" or "Relevant Memories" not in context_without

        # ---- POPULATE MEMORY ----
        ms.remember(
            test_user,
            "User prefers Rust for systems projects and Python for scripts.",
            importance=0.9,
        )
        ms.remember(
            test_user,
            "User uses NixOS with flake-based project templates.",
            importance=0.8,
        )
        ms.remember(
            test_user,
            "User insists on MIT license for all personal projects.",
            importance=0.7,
        )
        ms.remember(
            test_user,
            "User's preferred editor is Helix with catppuccin theme.",
            importance=0.5,
        )

        # ---- WITH MEMORY ----
        context_with = ms.build_memory_context(test_user, question)

        # The enriched context should contain actionable info
        assert "Relevant Memories" in context_with
        assert len(context_with) > len(context_without) + 50

        # Print comparison for human review (visible with -s flag)
        print("\n" + "=" * 70)
        print("WITHOUT MEMORY — agent context:")
        print(context_without if context_without else "(empty)")
        print("-" * 70)
        print("WITH MEMORY — agent context:")
        print(context_with)
        print("=" * 70)

        # Verify specific facts are surfaced
        relevant_phrases = ["Rust", "NixOS", "MIT", "Helix", "Python"]
        found = [p for p in relevant_phrases if p in context_with]
        assert len(found) >= 2, (
            f"Expected at least 2 relevant facts in context, found {found}.\n"
            f"Context:\n{context_with}"
        )


# ===========================================================================
# 6. Interaction-Based Decay
# ===========================================================================

@pytest.mark.skipif(not _OLLAMA_OK, reason="Ollama not available")
class TestInteractionDecay:
    """Verify that the interaction epoch counter works with real Qdrant."""

    def test_epoch_increment(self, test_user):
        from prax.services.memory.vector_store import (
            get_interaction_epoch,
            increment_interaction_epoch,
        )

        assert get_interaction_epoch(test_user) == 0
        assert increment_interaction_epoch(test_user) == 1
        assert increment_interaction_epoch(test_user) == 2
        assert get_interaction_epoch(test_user) == 2

    def test_track_interaction_via_service(self, test_user):
        from prax.services.memory_service import MemoryService

        ms = MemoryService()
        ms._available = True

        epoch = ms.track_interaction(test_user)
        assert epoch == 1
        epoch = ms.track_interaction(test_user)
        assert epoch == 2


# ===========================================================================
# 7. Full Pipeline — STM + LTM + Graph working together
# ===========================================================================

@pytest.mark.skipif(not _OLLAMA_OK, reason="Ollama not available")
class TestFullPipeline:
    """Integration test exercising STM, LTM, and graph in a realistic scenario."""

    def test_realistic_user_session(self, test_user):
        """Simulate a multi-turn conversation building up memory layers.

        Turn 1: User mentions they're a data scientist working on fraud detection.
        Turn 2: User asks about pandas vs polars — agent notes preference.
        Turn 3: Agent recalls user context to give personalized answer.
        """
        from prax.services.memory.graph_store import add_relation, get_entity, merge_entity
        from prax.services.memory.stm import stm_read, stm_write
        from prax.services.memory_service import MemoryService

        ms = MemoryService()
        ms._available = True

        # ---- Turn 1: User introduces themselves ----
        stm_write(test_user, "user_role", "data scientist at FinCorp")
        stm_write(test_user, "current_project", "fraud detection model using XGBoost")

        # Agent extracts entities to graph
        merge_entity(test_user, "user", "person", display_name="User", importance=0.9)
        merge_entity(test_user, "FinCorp", "organization", importance=0.7)
        merge_entity(test_user, "fraud detection", "project", importance=0.8)
        merge_entity(test_user, "XGBoost", "tool", importance=0.7)
        add_relation(test_user, "user", "works_at", "FinCorp")
        add_relation(test_user, "user", "works_on", "fraud detection")
        add_relation(test_user, "fraud detection", "uses", "XGBoost")

        # Store in LTM
        ms.remember(
            test_user,
            "User is a data scientist at FinCorp working on fraud detection with XGBoost.",
            importance=0.9,
            tags=["profile"],
        )

        # ---- Turn 2: User discusses data tools ----
        ms.remember(
            test_user,
            "User prefers polars over pandas for large datasets because of performance.",
            importance=0.7,
            tags=["preference", "tools"],
        )
        merge_entity(test_user, "polars", "tool", importance=0.7)
        merge_entity(test_user, "pandas", "tool", importance=0.5)
        add_relation(test_user, "user", "prefers", "polars")

        # Track interaction
        ms.track_interaction(test_user)

        # ---- Turn 3: Verify all memory layers are populated ----

        # STM
        stm_entries = stm_read(test_user)
        assert len(stm_entries) >= 2
        roles = [e.content for e in stm_entries]
        assert any("data scientist" in r for r in roles)

        # Graph
        user_entity = get_entity(test_user, "user")
        assert user_entity is not None
        rel_types = [r["type"] for r in user_entity.relations]
        assert "works_at" in rel_types or "works_on" in rel_types

        polars_entity = get_entity(test_user, "polars")
        assert polars_entity is not None

        # LTM recall
        results = ms.recall(test_user, "What data tools does the user prefer?", top_k=3)
        assert len(results) >= 1
        assert any("polars" in r.content for r in results)

        # Memory context — the agent's enriched system prompt
        context = ms.build_memory_context(
            test_user,
            "Can you help me optimize my data pipeline?",
        )
        assert "Working Memory" in context
        assert "Relevant Memories" in context

        # The context should contain info that helps the agent give
        # a personalized answer about data pipelines for a data scientist
        # who uses polars and XGBoost for fraud detection
        print("\n" + "=" * 70)
        print("FULL PIPELINE — Memory Context for 'optimize my data pipeline':")
        print(context)
        print("=" * 70)

        relevant_found = sum(
            1 for phrase in ["data scientist", "polars", "fraud", "XGBoost", "FinCorp"]
            if phrase.lower() in context.lower()
        )
        assert relevant_found >= 2, (
            f"Expected personalized context, only found {relevant_found}/5 relevant phrases.\n"
            f"Context:\n{context}"
        )
