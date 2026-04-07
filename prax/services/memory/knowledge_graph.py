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
            return record["deleted"] if record else 0
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
            return record["id"] if record else cid
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


def search_knowledge(
    user_id: str,
    query: str,
    namespace: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search concepts across knowledge graph namespaces."""
    ensure_kg_indexes()
    search_term = query.strip().lower()
    try:
        with _session() as session:
            if namespace:
                result = session.run(
                    """
                    MATCH (c:KnowledgeConcept {user_id: $uid, namespace: $ns})
                    WHERE toLower(c.name) CONTAINS $q
                       OR toLower(c.description) CONTAINS $q
                       OR toLower(c.display_name) CONTAINS $q
                    RETURN c.id AS id,
                           c.name AS name,
                           c.display_name AS display_name,
                           c.namespace AS namespace,
                           c.description AS description,
                           c.importance AS importance,
                           c.source AS source,
                           c.source_type AS source_type
                    ORDER BY c.importance DESC
                    LIMIT $limit
                    """,
                    uid=user_id,
                    ns=namespace,
                    q=search_term,
                    limit=limit,
                )
            else:
                result = session.run(
                    """
                    MATCH (c:KnowledgeConcept {user_id: $uid})
                    WHERE toLower(c.name) CONTAINS $q
                       OR toLower(c.description) CONTAINS $q
                       OR toLower(c.display_name) CONTAINS $q
                    RETURN c.id AS id,
                           c.name AS name,
                           c.display_name AS display_name,
                           c.namespace AS namespace,
                           c.description AS description,
                           c.importance AS importance,
                           c.source AS source,
                           c.source_type AS source_type
                    ORDER BY c.importance DESC
                    LIMIT $limit
                    """,
                    uid=user_id,
                    q=search_term,
                    limit=limit,
                )
            return [dict(r) for r in result]
    except Exception:
        logger.exception("KG search failed for query '%s'", query)
        return []


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
