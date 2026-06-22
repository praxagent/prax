"""Knowledge graph namespace system — structured knowledge from documents.

Stores concepts and relations extracted from documents, PDFs, uploaded files,
and code — organized by namespace so it doesn't pollute the user's
conversational memory (Entity/Relation graph in graph_store.py).

Node labels (separate from the memory graph):
  (:KnowledgeConcept {id, user_id, namespace, name, display_name,
                      description, source, source_type, importance,
                      created_at, updated_at, properties})
  (:KnowledgeDocument {id, user_id, namespace, title, source_path,
                       source_type, summary, extracted_at, concept_count})

Edge types:
  -[:KNOWLEDGE_RELATES {type, weight, evidence, namespace}]->
      (concept ↔ concept)
  -[:EXTRACTED_FROM]->
      (document → concept)
  -[:REFERENCES_ENTITY]->
      (concept → Entity from memory graph)
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Neo4j session — reuses the same driver from graph_store
# ---------------------------------------------------------------------------


def _session():
    """Get a Neo4j session via graph_store's shared driver."""
    from prax.services.memory.graph_store import _session as _gs_session

    return _gs_session()


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------

_kg_indexes_created = False


def ensure_kg_indexes() -> None:
    """Create indexes for knowledge graph nodes (idempotent)."""
    global _kg_indexes_created
    if _kg_indexes_created:
        return

    indexes = [
        "CREATE INDEX kg_concept_user_ns_name IF NOT EXISTS "
        "FOR (c:KnowledgeConcept) ON (c.user_id, c.namespace, c.name)",
        "CREATE INDEX kg_concept_user_ns IF NOT EXISTS "
        "FOR (c:KnowledgeConcept) ON (c.user_id, c.namespace)",
        "CREATE INDEX kg_concept_id IF NOT EXISTS "
        "FOR (c:KnowledgeConcept) ON (c.id)",
        "CREATE INDEX kg_doc_user_ns IF NOT EXISTS "
        "FOR (d:KnowledgeDocument) ON (d.user_id, d.namespace)",
        "CREATE INDEX kg_doc_id IF NOT EXISTS "
        "FOR (d:KnowledgeDocument) ON (d.id)",
    ]
    try:
        with _session() as session:
            for idx in indexes:
                try:
                    session.run(idx)
                except Exception:
                    pass  # Already exists or syntax varies
        _kg_indexes_created = True
    except Exception:
        logger.exception("Failed to create KG indexes")


# ---------------------------------------------------------------------------
# Namespace operations
# ---------------------------------------------------------------------------


def list_namespaces(user_id: str) -> list[dict]:
    """List all knowledge graph namespaces for a user with concept counts."""
    ensure_kg_indexes()
    try:
        with _session() as session:
            result = session.run(
                """
                MATCH (c:KnowledgeConcept {user_id: $uid})
                RETURN c.namespace AS namespace,
                       count(c) AS concept_count
                ORDER BY concept_count DESC
                """,
                uid=user_id,
            )
            return [dict(r) for r in result]
    except Exception:
        logger.exception("Failed to list namespaces for user %s", user_id)
        return []


def get_namespace_stats(user_id: str, namespace: str) -> dict:
    """Get stats for a namespace: concept count, relation count, documents."""
    ensure_kg_indexes()
    try:
        with _session() as session:
            result = session.run(
                """
                OPTIONAL MATCH (c:KnowledgeConcept {user_id: $uid, namespace: $ns})
                WITH count(DISTINCT c) AS concept_count
                OPTIONAL MATCH (c2:KnowledgeConcept {user_id: $uid, namespace: $ns})
                         -[r:KNOWLEDGE_RELATES]-
                         (:KnowledgeConcept {user_id: $uid, namespace: $ns})
                WITH concept_count, count(DISTINCT r) AS relation_count
                OPTIONAL MATCH (d:KnowledgeDocument {user_id: $uid, namespace: $ns})
                RETURN concept_count AS concepts,
                       relation_count AS relations,
                       count(DISTINCT d) AS documents
                """,
                uid=user_id,
                ns=namespace,
            )
            record = result.single()
            return {
                "namespace": namespace,
                "concepts": record["concepts"] if record else 0,
                "relations": record["relations"] if record else 0,
                "documents": record["documents"] if record else 0,
            }
    except Exception:
        logger.exception("Failed to get stats for namespace %s", namespace)
        return {"namespace": namespace, "concepts": 0, "relations": 0, "documents": 0}


def delete_namespace(user_id: str, namespace: str) -> int:
    """Delete all concepts, relations, and documents in a namespace.

    Returns count of nodes deleted.
    """
    ensure_kg_indexes()
    try:
        with _session() as session:
            # Delete documents
            session.run(
                """
                MATCH (d:KnowledgeDocument {user_id: $uid, namespace: $ns})
                DETACH DELETE d
                """,
                uid=user_id,
                ns=namespace,
            )
            # Delete concepts and their relations
            result = session.run(
                """
                MATCH (c:KnowledgeConcept {user_id: $uid, namespace: $ns})
                WITH count(c) AS cnt, collect(c) AS nodes
                UNWIND nodes AS c
                DETACH DELETE c
                RETURN cnt AS deleted
                """,
                uid=user_id,
                ns=namespace,
            )
            record = result.single()
            deleted = record["deleted"] if record else 0
        # Drop the namespace's concept vectors too (best-effort).
        try:
            from prax.services.memory import knowledge_vectors
            knowledge_vectors.delete_namespace_vectors(user_id, namespace)
        except Exception:
            pass
        return deleted
    except Exception:
        logger.exception("Failed to delete namespace %s for user %s", namespace, user_id)
        return 0


# ---------------------------------------------------------------------------
# Concept CRUD
# ---------------------------------------------------------------------------


def add_concept(
    user_id: str,
    namespace: str,
    name: str,
    description: str = "",
    source: str = "",
    source_type: str = "manual",
    importance: float = 0.5,
    properties: dict | None = None,
) -> str:
    """Add a single concept to the knowledge graph. Returns concept ID."""
    ensure_kg_indexes()
    canonical = name.strip().lower()
    display = name.strip()
    now = datetime.now(UTC).isoformat()
    cid = str(uuid.uuid4())
    props = properties or {}

    try:
        with _session() as session:
            result = session.run(
                """
                MERGE (c:KnowledgeConcept {
                    user_id: $uid, namespace: $ns, name: $name
                })
                ON CREATE SET
                    c.id = $cid,
                    c.display_name = $display,
                    c.description = $desc,
                    c.source = $source,
                    c.source_type = $source_type,
                    c.importance = $importance,
                    c.created_at = $now,
                    c.updated_at = $now,
                    c.properties = $props
                ON MATCH SET
                    c.description = CASE WHEN size($desc) > size(c.description)
                                         THEN $desc ELSE c.description END,
                    c.importance = CASE WHEN $importance > c.importance
                                       THEN $importance ELSE c.importance END,
                    c.updated_at = $now
                RETURN c.id AS id
                """,
                uid=user_id,
                ns=namespace,
                name=canonical,
                cid=cid,
                display=display,
                desc=description,
                source=source,
                source_type=source_type,
                importance=importance,
                now=now,
                props=str(props),
            )
            record = result.single()
            cid = record["id"] if record else cid
        # Mirror the concept into the vector index for semantic search
        # (best-effort; degrades to keyword-only if unavailable).
        try:
            from prax.services.memory import knowledge_vectors
            knowledge_vectors.upsert_concept(user_id, namespace, cid, display, description)
        except Exception:
            pass
        return cid
    except Exception:
        logger.exception(
            "Failed to add concept '%s' to namespace %s", name, namespace
        )
        return cid


def get_concept(
    user_id: str, name: str, namespace: str | None = None
) -> dict | None:
    """Get a concept with its relations."""
    ensure_kg_indexes()
    canonical = name.strip().lower()
    try:
        with _session() as session:
            if namespace:
                query = """
                    MATCH (c:KnowledgeConcept {
                        user_id: $uid, namespace: $ns, name: $name
                    })
                    OPTIONAL MATCH (c)-[r:KNOWLEDGE_RELATES]-(other:KnowledgeConcept)
                    RETURN c, collect({
                        type: r.type,
                        weight: r.weight,
                        direction: CASE WHEN startNode(r) = c
                                        THEN 'outgoing' ELSE 'incoming' END,
                        other_name: other.display_name,
                        other_namespace: other.namespace,
                        evidence: r.evidence
                    }) AS relations
                """
                result = session.run(query, uid=user_id, ns=namespace, name=canonical)
            else:
                query = """
                    MATCH (c:KnowledgeConcept {user_id: $uid, name: $name})
                    OPTIONAL MATCH (c)-[r:KNOWLEDGE_RELATES]-(other:KnowledgeConcept)
                    RETURN c, collect({
                        type: r.type,
                        weight: r.weight,
                        direction: CASE WHEN startNode(r) = c
                                        THEN 'outgoing' ELSE 'incoming' END,
                        other_name: other.display_name,
                        other_namespace: other.namespace,
                        evidence: r.evidence
                    }) AS relations
                """
                result = session.run(query, uid=user_id, name=canonical)

            record = result.single()
            if not record:
                return None

            node = record["c"]
            rels = [
                r for r in record["relations"] if r.get("type") is not None
            ]

            return {
                "id": node.get("id", ""),
                "name": node.get("name", ""),
                "display_name": node.get("display_name", ""),
                "namespace": node.get("namespace", ""),
                "description": node.get("description", ""),
                "source": node.get("source", ""),
                "source_type": node.get("source_type", ""),
                "importance": node.get("importance", 0.5),
                "created_at": node.get("created_at", ""),
                "updated_at": node.get("updated_at", ""),
                "relations": rels,
            }
    except Exception:
        logger.exception("Failed to get concept '%s'", name)
        return None


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------


def add_knowledge_relation(
    user_id: str,
    namespace: str,
    source_name: str,
    relation_type: str,
    target_name: str,
    weight: float = 1.0,
    evidence: str = "",
) -> bool:
    """Add a relation between two concepts in the knowledge graph."""
    ensure_kg_indexes()
    src = source_name.strip().lower()
    tgt = target_name.strip().lower()

    try:
        with _session() as session:
            result = session.run(
                """
                MATCH (s:KnowledgeConcept {user_id: $uid, namespace: $ns, name: $src})
                MATCH (t:KnowledgeConcept {user_id: $uid, namespace: $ns, name: $tgt})
                MERGE (s)-[r:KNOWLEDGE_RELATES {type: $rtype}]->(t)
                ON CREATE SET
                    r.weight = $weight,
                    r.evidence = $evidence,
                    r.namespace = $ns
                ON MATCH SET
                    r.weight = r.weight + $weight,
                    r.evidence = r.evidence + '; ' + $evidence
                RETURN count(r) AS cnt
                """,
                uid=user_id,
                ns=namespace,
                src=src,
                tgt=tgt,
                rtype=relation_type,
                weight=weight,
                evidence=evidence,
            )
            record = result.single()
            return bool(record and record["cnt"] > 0)
    except Exception:
        logger.exception(
            "Failed to add KG relation %s -[%s]-> %s",
            source_name,
            relation_type,
            target_name,
        )
        return False


def link_to_memory(
    user_id: str, concept_name: str, entity_name: str
) -> bool:
    """Create a cross-namespace link between a KnowledgeConcept and a memory Entity."""
    ensure_kg_indexes()
    concept_canonical = concept_name.strip().lower()
    entity_canonical = entity_name.strip().lower()

    try:
        with _session() as session:
            result = session.run(
                """
                MATCH (c:KnowledgeConcept {user_id: $uid, name: $cname})
                MATCH (e:Entity {user_id: $uid, name: $ename})
                MERGE (c)-[:REFERENCES_ENTITY]->(e)
                RETURN count(*) AS cnt
                """,
                uid=user_id,
                cname=concept_canonical,
                ename=entity_canonical,
            )
            record = result.single()
            return bool(record and record["cnt"] > 0)
    except Exception:
        logger.exception(
            "Failed to link concept '%s' to entity '%s'",
            concept_name,
            entity_name,
        )
        return False


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _knowledge_search_terms(query: str) -> list[str]:
    """Return the lowercased match terms for a knowledge search.

    With ``retrieval_query_expansion_enabled``, the query is expanded into a
    few paraphrase variants so the substring match catches alternate phrasings
    (a recall win over the bare single-term CONTAINS).  With it off, this is
    just ``[query.lower()]`` — identical to the original behaviour.
    """
    base = query.strip().lower()
    try:
        from prax.settings import settings
        if not settings.retrieval_query_expansion_enabled:
            return [base]
        from prax.services.memory.retrieval import _expand_queries
        variants = _expand_queries(query, settings.retrieval_query_expansion_n)
        terms: list[str] = []
        for v in variants:
            t = v.strip().lower()
            if t and t not in terms:
                terms.append(t)
        return terms or [base]
    except Exception:
        return [base]


_CONCEPT_RETURN = """
    RETURN c.id AS id,
           c.name AS name,
           c.display_name AS display_name,
           c.namespace AS namespace,
           c.description AS description,
           c.importance AS importance,
           c.source AS source,
           c.source_type AS source_type
"""


def _keyword_search(
    user_id: str, query: str, namespace: str | None, limit: int,
) -> list[dict]:
    """Keyword (multi-variant substring) concept search arm."""
    terms = _knowledge_search_terms(query)
    where = (
        "ANY(t IN $terms WHERE toLower(c.name) CONTAINS t "
        "OR toLower(c.description) CONTAINS t "
        "OR toLower(c.display_name) CONTAINS t)"
    )
    match_count = (
        "size([t IN $terms WHERE toLower(c.name) CONTAINS t "
        "OR toLower(c.description) CONTAINS t "
        "OR toLower(c.display_name) CONTAINS t])"
    )
    match_clause = (
        "MATCH (c:KnowledgeConcept {user_id: $uid, namespace: $ns})"
        if namespace
        else "MATCH (c:KnowledgeConcept {user_id: $uid})"
    )
    cypher = f"""
        {match_clause}
        WHERE {where}
        {_CONCEPT_RETURN},
               {match_count} AS match_count
        ORDER BY match_count DESC, c.importance DESC
        LIMIT $limit
    """
    params: dict = {"uid": user_id, "terms": terms, "limit": limit}
    if namespace:
        params["ns"] = namespace
    with _session() as session:
        rows = [dict(r) for r in session.run(cypher, **params)]
    for row in rows:
        row.pop("match_count", None)
    return rows


def _get_concepts_by_ids(user_id: str, ids: list[str]) -> dict[str, dict]:
    """Hydrate concept records for vector-only hits, keyed by id."""
    if not ids:
        return {}
    cypher = f"""
        MATCH (c:KnowledgeConcept {{user_id: $uid}})
        WHERE c.id IN $ids
        {_CONCEPT_RETURN}
    """
    try:
        with _session() as session:
            return {r["id"]: dict(r) for r in session.run(cypher, uid=user_id, ids=ids)}
    except Exception:
        logger.debug("Concept hydration failed", exc_info=True)
        return {}


def search_knowledge(
    user_id: str,
    query: str,
    namespace: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search concepts via hybrid semantic + keyword retrieval.

    The keyword arm (multi-variant substring) and the semantic arm (Qdrant
    vector search over concept embeddings) are fused with Reciprocal Rank
    Fusion.  When the vector backend is unavailable, this is exactly the
    keyword arm — so it always works, just with less recall.
    """
    ensure_kg_indexes()
    try:
        keyword = _keyword_search(user_id, query, namespace, limit)
    except Exception:
        logger.exception("KG keyword search failed for query '%s'", query)
        keyword = []

    # Semantic arm (best-effort; empty when Qdrant/embedder unavailable).
    try:
        from prax.services.memory import knowledge_vectors
        vector_hits = knowledge_vectors.search(user_id, query, namespace, top_k=limit * 2)
    except Exception:
        vector_hits = []

    if not vector_hits:
        return keyword

    # Fuse the two ranked id-orderings.
    from prax.services.memory.knowledge_vectors import _rrf
    kw_ids = [r["id"] for r in keyword]
    vec_ids = [cid for cid, _ in vector_hits]
    fused = _rrf([kw_ids, vec_ids])

    by_id = {r["id"]: r for r in keyword}
    missing = [cid for cid, _ in fused if cid not in by_id]
    by_id.update(_get_concepts_by_ids(user_id, missing))

    ordered = [by_id[cid] for cid, _ in fused if cid in by_id]
    return ordered[:limit]


def reindex_user_concepts(user_id: str, batch_limit: int = 5000) -> int:
    """Backfill the vector index for a user's existing concepts.

    New concepts are vector-indexed automatically on :func:`add_concept`; run
    this once after enabling hybrid search (or upgrading) to index the backlog.
    Returns the number of concepts (re)indexed.  No-op when the vector backend
    is unavailable.
    """
    from prax.services.memory import knowledge_vectors
    if not knowledge_vectors.available():
        return 0
    try:
        with _session() as session:
            rows = [dict(r) for r in session.run(
                """
                MATCH (c:KnowledgeConcept {user_id: $uid})
                RETURN c.id AS id, c.namespace AS namespace,
                       c.display_name AS display_name, c.description AS description
                LIMIT $limit
                """,
                uid=user_id, limit=batch_limit,
            )]
    except Exception:
        logger.exception("Concept reindex query failed for user %s", user_id)
        return 0
    count = 0
    for r in rows:
        knowledge_vectors.upsert_concept(
            user_id, r.get("namespace") or "", r["id"],
            r.get("display_name") or "", r.get("description") or "",
        )
        count += 1
    logger.info("Reindexed %d knowledge concepts for user %s", count, user_id)
    return count


# ---------------------------------------------------------------------------
# Open Knowledge Format (OKF) interchange — export/import as plain markdown
# files.  See prax/services/memory/okf_bridge.py and
# docs/research/open-knowledge-format.md.
# ---------------------------------------------------------------------------

def list_concepts(user_id: str, namespace: str) -> list[dict]:
    """Return all concepts in a namespace (for OKF export)."""
    try:
        with _session() as session:
            return [dict(r) for r in session.run(
                """
                MATCH (c:KnowledgeConcept {user_id: $uid, namespace: $ns})
                RETURN c.id AS id, c.name AS name, c.display_name AS display_name,
                       c.description AS description, c.importance AS importance,
                       c.source AS source, c.source_type AS source_type,
                       c.created_at AS created_at, c.updated_at AS updated_at
                """,
                uid=user_id, ns=namespace,
            )]
    except Exception:
        logger.exception("Failed to list concepts for namespace %s", namespace)
        return []


def list_relations(user_id: str, namespace: str) -> list[dict]:
    """Return all intra-namespace concept relations (for OKF export)."""
    try:
        with _session() as session:
            return [dict(r) for r in session.run(
                """
                MATCH (s:KnowledgeConcept {user_id: $uid, namespace: $ns})
                      -[r:KNOWLEDGE_RELATES]->
                      (t:KnowledgeConcept {user_id: $uid, namespace: $ns})
                RETURN s.id AS from_id, t.id AS to_id, r.type AS type
                """,
                uid=user_id, ns=namespace,
            )]
    except Exception:
        logger.exception("Failed to list relations for namespace %s", namespace)
        return []


def export_namespace_okf(user_id: str, namespace: str, dest_dir: str) -> dict:
    """Export a namespace's concepts + relations as an OKF bundle at *dest_dir*."""
    from prax.services.memory import okf_bridge
    concepts = list_concepts(user_id, namespace)
    relations = list_relations(user_id, namespace)
    return okf_bridge.write_bundle(concepts, relations, dest_dir, namespace)


def import_okf(user_id: str, src_dir: str, namespace: str = "imported") -> dict:
    """Import an OKF bundle at *src_dir* into the knowledge graph under *namespace*.

    Concepts are added via :func:`add_concept` (so they are also vector-indexed)
    and markdown cross-links become :func:`add_knowledge_relation` edges.
    """
    from prax.services.memory import okf_bridge
    records, edges = okf_bridge.read_bundle(src_dir)
    for rec in records:
        add_concept(
            user_id=user_id,
            namespace=namespace,
            name=rec["name"],
            description=rec.get("description", ""),
            source=rec.get("source", ""),
            source_type=rec.get("source_type", "concept"),
        )
    rel_count = 0
    for source_name, rtype, target_name in edges:
        if add_knowledge_relation(user_id, namespace, source_name, rtype, target_name):
            rel_count += 1
    logger.info(
        "Imported OKF bundle: %d concepts, %d relations into namespace %s",
        len(records), rel_count, namespace,
    )
    return {"namespace": namespace, "concepts": len(records), "relations": rel_count}


# ---------------------------------------------------------------------------
# Document ingestion with LLM extraction
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """\
Extract key concepts and their relationships from this text.
Return JSON:
{
    "concepts": [{"name": "...", "description": "...", "importance": 0.0-1.0}],
    "relations": [{"source": "...", "type": "...", "target": "...", "evidence": "..."}]
}
Relation types: defines, uses, contradicts, supports, part_of, implements, extends, depends_on
Only extract concepts that are specific and meaningful — not generic words.

Text:
"""


def _extract_concepts_and_relations(content: str) -> dict:
    """Use a cheap LLM to extract concepts and relations from text.

    Returns {"concepts": [...], "relations": [...]}.
    """
    from prax.agent.llm_factory import build_llm

    llm = build_llm(tier="low", temperature=0.0)

    # Truncate very long content to avoid token limits
    truncated = content[:12000]
    prompt = _EXTRACTION_PROMPT + truncated

    try:
        response = llm.invoke(prompt)
        text = response.content if hasattr(response, "content") else str(response)

        # Extract JSON from the response
        # Try to find JSON block in the response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
            concepts = parsed.get("concepts", [])
            relations = parsed.get("relations", [])
            return {"concepts": concepts, "relations": relations}
    except Exception:
        logger.exception("LLM extraction failed")

    return {"concepts": [], "relations": []}


def ingest_document(
    user_id: str,
    namespace: str,
    title: str,
    content: str,
    source_path: str,
    source_type: str = "markdown",
) -> dict:
    """Extract concepts and relations from a document and store in the KG.

    Returns: {"document_id": str, "concepts": int, "relations": int}
    """
    ensure_kg_indexes()
    doc_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    # Extract concepts and relations via LLM
    extracted = _extract_concepts_and_relations(content)
    concepts = extracted.get("concepts", [])
    relations = extracted.get("relations", [])

    # Store concepts
    concept_count = 0
    for c in concepts:
        c_name = c.get("name", "").strip()
        if not c_name:
            continue
        add_concept(
            user_id=user_id,
            namespace=namespace,
            name=c_name,
            description=c.get("description", ""),
            source=source_path,
            source_type=source_type,
            importance=float(c.get("importance", 0.5)),
        )
        concept_count += 1

    # Store relations
    relation_count = 0
    for r in relations:
        src = r.get("source", "").strip()
        tgt = r.get("target", "").strip()
        rtype = r.get("type", "relates_to").strip()
        if not src or not tgt:
            continue
        if add_knowledge_relation(
            user_id=user_id,
            namespace=namespace,
            source_name=src,
            relation_type=rtype,
            target_name=tgt,
            evidence=r.get("evidence", ""),
        ):
            relation_count += 1

    # Create the document node
    try:
        with _session() as session:
            session.run(
                """
                CREATE (d:KnowledgeDocument {
                    id: $doc_id,
                    user_id: $uid,
                    namespace: $ns,
                    title: $title,
                    source_path: $source_path,
                    source_type: $source_type,
                    summary: $summary,
                    extracted_at: $now,
                    concept_count: $concept_count
                })
                """,
                doc_id=doc_id,
                uid=user_id,
                ns=namespace,
                title=title,
                source_path=source_path,
                source_type=source_type,
                summary=content[:500],
                now=now,
                concept_count=concept_count,
            )

            # Link document to extracted concepts
            for c in concepts:
                c_name = c.get("name", "").strip().lower()
                if not c_name:
                    continue
                session.run(
                    """
                    MATCH (d:KnowledgeDocument {id: $doc_id})
                    MATCH (c:KnowledgeConcept {
                        user_id: $uid, namespace: $ns, name: $cname
                    })
                    MERGE (d)-[:EXTRACTED_FROM]->(c)
                    """,
                    doc_id=doc_id,
                    uid=user_id,
                    ns=namespace,
                    cname=c_name,
                )
    except Exception:
        logger.exception("Failed to create KnowledgeDocument node")

    return {
        "document_id": doc_id,
        "concepts": concept_count,
        "relations": relation_count,
    }
