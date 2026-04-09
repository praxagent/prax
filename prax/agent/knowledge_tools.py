"""Knowledge graph tools — agent-facing tools for document ingestion and search.

These tools are used by the memory spoke agent to manage the knowledge graph,
which is SEPARATE from conversational memory.  The knowledge graph stores
structured concepts extracted from documents, papers, and code, organized
by namespace.
"""
from __future__ import annotations

import os

from langchain_core.tools import tool

from prax.agent.user_context import current_user_id


def _uid() -> str:
    uid = current_user_id.get()
    if not uid:
        return "anonymous"
    return uid


@tool
def knowledge_ingest(
    source_path: str, namespace: str = "general", title: str = ""
) -> str:
    """Ingest a document into the knowledge graph, extracting concepts and relations.

    Args:
        source_path: Path to the file in the workspace (markdown, PDF, or text).
        namespace: Knowledge namespace (e.g., "papers", "docs", "codebase").
                   Keeps knowledge organized and separate from conversational memory.
        title: Optional title. Auto-detected from filename if not provided.
    """
    from prax.services.memory.knowledge_graph import ingest_document

    # Read the file content
    try:
        from prax.services.workspace_service import get_workspace_service

        ws = get_workspace_service()
        content = ws.read_file(_uid(), source_path)
        if content is None:
            return f"File not found: {source_path}"
    except Exception:
        return f"Could not read file: {source_path}"

    # Auto-detect title from filename if not provided
    if not title:
        title = os.path.splitext(os.path.basename(source_path))[0].replace(
            "_", " "
        ).replace("-", " ").title()

    # Detect source type from extension
    ext = os.path.splitext(source_path)[1].lower()
    source_type_map = {
        ".md": "markdown",
        ".markdown": "markdown",
        ".pdf": "pdf",
        ".py": "code",
        ".js": "code",
        ".ts": "code",
        ".go": "code",
        ".rs": "code",
        ".java": "code",
        ".txt": "text",
        ".html": "html",
        ".htm": "html",
    }
    source_type = source_type_map.get(ext, "text")

    result = ingest_document(
        user_id=_uid(),
        namespace=namespace,
        title=title,
        content=content,
        source_path=source_path,
        source_type=source_type,
    )

    return (
        f"Ingested '{title}' into namespace '{namespace}': "
        f"{result['concepts']} concepts, {result['relations']} relations extracted. "
        f"Document ID: {result['document_id'][:8]}..."
    )


@tool
def knowledge_search(query: str, namespace: str = "") -> str:
    """Search the knowledge graph for concepts and their relationships.

    Unlike memory recall (which searches conversational history), this searches
    structured knowledge extracted from documents, papers, and code.

    Args:
        query: What to search for.
        namespace: Limit search to a specific namespace, or empty for all.
    """
    from prax.services.memory.knowledge_graph import search_knowledge

    results = search_knowledge(
        user_id=_uid(),
        query=query,
        namespace=namespace if namespace else None,
        limit=20,
    )

    if not results:
        ns_note = f" in namespace '{namespace}'" if namespace else ""
        return f"No knowledge graph concepts found matching '{query}'{ns_note}."

    lines = []
    for r in results:
        desc = r.get("description", "")
        desc_preview = (desc[:100] + "...") if len(desc) > 100 else desc
        lines.append(
            f"- **{r.get('display_name', r.get('name', '?'))}** "
            f"[{r.get('namespace', '?')}] "
            f"(importance={r.get('importance', 0):.1f}, "
            f"source={r.get('source_type', '?')})\n"
            f"  {desc_preview}"
        )
    return "\n".join(lines)


@tool
def knowledge_namespaces() -> str:
    """List all knowledge graph namespaces and their concept counts.

    Namespaces organize knowledge by source/topic:
    - "papers" -- academic papers and research
    - "docs" -- documentation and guides
    - "codebase" -- code structure and architecture
    - "uploads" -- user-uploaded files
    - Custom namespaces created by the user
    """
    from prax.services.memory.knowledge_graph import list_namespaces

    namespaces = list_namespaces(_uid())

    if not namespaces:
        return "No knowledge graph namespaces found. Ingest a document to create one."

    lines = ["Knowledge graph namespaces:"]
    for ns in namespaces:
        lines.append(
            f"  - **{ns['namespace']}**: {ns['concept_count']} concepts"
        )
    return "\n".join(lines)


@tool
def knowledge_connect(concept: str, memory_entity: str) -> str:
    """Link a knowledge graph concept to a memory entity.

    Creates a cross-namespace connection so Prax can relate what he knows
    about a topic (from documents) to what he knows about a person/project
    (from conversations).

    Args:
        concept: Name of the knowledge concept.
        memory_entity: Name of the memory entity to link to.
    """
    from prax.services.memory.knowledge_graph import link_to_memory

    success = link_to_memory(_uid(), concept, memory_entity)
    if success:
        return (
            f"Linked knowledge concept '{concept}' to memory entity "
            f"'{memory_entity}'."
        )
    return (
        f"Could not link '{concept}' to '{memory_entity}'. "
        f"Make sure both exist in their respective graphs."
    )


def build_knowledge_tools() -> list:
    """Return knowledge graph tools."""
    return [knowledge_ingest, knowledge_search, knowledge_namespaces, knowledge_connect]
