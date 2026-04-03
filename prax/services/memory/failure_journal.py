"""Failure journal — persistent record of agent failures for the improvement loop.

Stores failure cases in Neo4j (graph) and Qdrant (vector) so they can be:
1. Queried by category, tool, or time range
2. Searched semantically (find similar past failures)
3. Replayed by the eval runner to verify fixes
4. Analyzed for patterns by the self-improve agent

Graph model:
  (:FailureCase {id, user_id, trace_id, user_input, agent_output,
                 trajectory, feedback_comment, failure_category,
                 resolved, resolution, created_at, importance})

  (:FailureCase)-[:INVOLVED_TOOL]->(:Entity)   — tools used in the failing trace
  (:FailureCase)-[:SIMILAR_TO]->(:FailureCase)  — from vector similarity

The failure journal is the bridge between feedback capture and the eval runner.
Each entry represents a concrete, observed failure that becomes a regression
test case. The journal grows monotonically — resolved failures stay as
regression guards.

References:
  - LangChain, "The Agent Improvement Loop Starts with a Trace" (2025)
  - Park et al., "Generative Agents" (2023): observation → reflection → action
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FailureCase:
    """A single observed agent failure."""

    id: str = ""
    user_id: str = ""
    trace_id: str = ""
    user_input: str = ""
    agent_output: str = ""
    trajectory: str = ""  # JSON string of execution graph snapshot
    feedback_comment: str = ""
    failure_category: str = ""  # auto-classified or manual
    tools_involved: list[str] = field(default_factory=list)
    resolved: bool = False
    resolution: str = ""
    created_at: str = ""
    importance: float = 0.8  # failures start important

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:16]
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Local persistence — workspace JSONL (always available, even without Neo4j)
# ---------------------------------------------------------------------------

def _journal_dir() -> Path:
    try:
        from prax.settings import settings
        base = Path(settings.workspace_dir).resolve()
    except Exception:
        base = Path(".")
    d = base / ".prax" / "failure_journal"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _journal_file() -> Path:
    return _journal_dir() / "failures.jsonl"


def _append_local(case: FailureCase) -> None:
    """Append to local JSONL (never fails — primary persistence)."""
    try:
        line = json.dumps(asdict(case), default=str)
        with open(_journal_file(), "a") as f:
            f.write(line + "\n")
    except Exception:
        logger.warning("Failed to persist failure case %s locally", case.id, exc_info=True)


def _load_local() -> list[FailureCase]:
    """Load all failure cases from local JSONL."""
    filepath = _journal_file()
    if not filepath.exists():
        return []
    cases = []
    try:
        for line in filepath.read_text().strip().splitlines():
            if line.strip():
                data = json.loads(line)
                cases.append(FailureCase(**data))
    except Exception:
        logger.warning("Failed to load failure journal", exc_info=True)
    return cases


def _update_local(case_id: str, updates: dict) -> bool:
    """Update a failure case in the local JSONL (rewrite)."""
    filepath = _journal_file()
    if not filepath.exists():
        return False
    try:
        lines = filepath.read_text().strip().splitlines()
        new_lines = []
        found = False
        for line in lines:
            data = json.loads(line)
            if data.get("id") == case_id:
                data.update(updates)
                found = True
            new_lines.append(json.dumps(data, default=str))
        if found:
            filepath.write_text("\n".join(new_lines) + "\n")
        return found
    except Exception:
        logger.warning("Failed to update failure case %s", case_id, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Neo4j persistence (best-effort — degrades gracefully)
# ---------------------------------------------------------------------------

def _store_neo4j(case: FailureCase) -> None:
    """Store failure case as a Neo4j node with tool relationships."""
    try:
        from prax.services.memory.graph_store import _session

        with _session() as session:
            # Ensure FailureCase index exists
            try:
                session.run(
                    "CREATE INDEX failure_user IF NOT EXISTS "
                    "FOR (f:FailureCase) ON (f.user_id)"
                )
                session.run(
                    "CREATE INDEX failure_id IF NOT EXISTS "
                    "FOR (f:FailureCase) ON (f.id)"
                )
            except Exception:
                pass

            session.run(
                """
                CREATE (f:FailureCase {
                    id: $id,
                    user_id: $uid,
                    trace_id: $trace_id,
                    user_input: $user_input,
                    agent_output: $agent_output,
                    trajectory: $trajectory,
                    feedback_comment: $feedback_comment,
                    failure_category: $category,
                    resolved: $resolved,
                    resolution: $resolution,
                    created_at: $created_at,
                    importance: $importance
                })
                """,
                id=case.id,
                uid=case.user_id,
                trace_id=case.trace_id,
                user_input=case.user_input[:2000],
                agent_output=case.agent_output[:2000],
                trajectory=case.trajectory[:5000],
                feedback_comment=case.feedback_comment[:1000],
                category=case.failure_category,
                resolved=case.resolved,
                resolution=case.resolution,
                created_at=case.created_at,
                importance=case.importance,
            )

            # Link to tool entities
            for tool_name in case.tools_involved:
                canonical = tool_name.strip().lower()
                session.run(
                    """
                    MATCH (f:FailureCase {id: $fid})
                    MATCH (e:Entity {user_id: $uid, name: $tool})
                    MERGE (f)-[:INVOLVED_TOOL]->(e)
                    """,
                    fid=case.id,
                    uid=case.user_id,
                    tool=canonical,
                )

        logger.debug("Failure case %s stored in Neo4j", case.id)
    except Exception:
        logger.debug("Neo4j storage failed for failure case %s (degrading gracefully)", case.id)


def _store_qdrant(case: FailureCase) -> None:
    """Embed failure case in Qdrant for semantic similarity search."""
    try:
        from prax.services.memory.vector_store import upsert_memory

        # Build a searchable text from the failure details
        content = (
            f"FAILURE: {case.user_input}\n"
            f"OUTPUT: {case.agent_output[:500]}\n"
            f"FEEDBACK: {case.feedback_comment}"
        )

        upsert_memory(
            user_id=case.user_id,
            content=content,
            source="failure_journal",
            importance=case.importance,
            tags=["failure", case.failure_category] if case.failure_category else ["failure"],
            memory_id=f"failure-{case.id}",
        )
        logger.debug("Failure case %s embedded in Qdrant", case.id)
    except Exception:
        logger.debug("Qdrant storage failed for failure case %s (degrading gracefully)", case.id)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_failure(
    user_id: str,
    user_input: str,
    agent_output: str,
    trace_id: str = "",
    graph_snapshot: dict | None = None,
    feedback_comment: str = "",
    failure_category: str = "",
) -> FailureCase:
    """Record an observed agent failure.

    Persists to local JSONL (always), Neo4j (best-effort), and Qdrant
    (best-effort). The local file is the source of truth.

    Args:
        user_id: User who observed the failure.
        user_input: What the user asked.
        agent_output: What the agent said/did.
        trace_id: Execution graph trace_id.
        graph_snapshot: Serialized execution graph dict.
        feedback_comment: User's correction or complaint.
        failure_category: Optional classification (e.g., "wrong_tool",
            "hallucination", "incomplete", "wrong_format").

    Returns:
        The persisted FailureCase.
    """
    # Extract tools from the graph snapshot
    tools: list[str] = []
    if graph_snapshot:
        for node in graph_snapshot.get("nodes", []):
            if node.get("spoke_or_category") == "tool":
                tools.append(node["name"])

    # Auto-classify if no category provided
    if not failure_category:
        failure_category = _auto_classify(user_input, agent_output, feedback_comment)

    case = FailureCase(
        user_id=user_id,
        trace_id=trace_id,
        user_input=user_input,
        agent_output=agent_output,
        trajectory=json.dumps(graph_snapshot, default=str) if graph_snapshot else "",
        feedback_comment=feedback_comment,
        failure_category=failure_category,
        tools_involved=tools,
    )

    # Primary persistence — always works
    _append_local(case)

    # Best-effort persistence to graph + vector stores
    _store_neo4j(case)
    _store_qdrant(case)

    logger.info(
        "Failure case recorded: %s (category=%s, tools=%s, trace=%s)",
        case.id, failure_category or "unclassified",
        tools[:3], trace_id[:8] if trace_id else "none",
    )
    return case


def resolve_failure(case_id: str, resolution: str) -> bool:
    """Mark a failure case as resolved.

    Called after a fix has been deployed and verified. The failure
    remains in the journal as a permanent regression guard.
    """
    updates = {"resolved": True, "resolution": resolution}
    updated = _update_local(case_id, updates)

    # Best-effort Neo4j update
    try:
        from prax.services.memory.graph_store import _session
        with _session() as session:
            session.run(
                """
                MATCH (f:FailureCase {id: $id})
                SET f.resolved = true, f.resolution = $resolution
                """,
                id=case_id,
                resolution=resolution,
            )
    except Exception:
        pass

    return updated


def get_failures(
    user_id: str | None = None,
    resolved: bool | None = None,
    category: str | None = None,
    limit: int = 50,
) -> list[FailureCase]:
    """Retrieve failure cases from the local journal.

    Args:
        user_id: Filter by user. None returns all.
        resolved: True for resolved, False for unresolved, None for all.
        category: Filter by failure_category.
        limit: Max entries (most recent first).
    """
    cases = _load_local()
    if user_id:
        cases = [c for c in cases if c.user_id == user_id]
    if resolved is not None:
        cases = [c for c in cases if c.resolved == resolved]
    if category:
        cases = [c for c in cases if c.failure_category == category]
    cases.sort(key=lambda c: c.created_at, reverse=True)
    return cases[:limit]


def search_similar_failures(
    user_id: str,
    query: str,
    top_k: int = 5,
) -> list[FailureCase]:
    """Find semantically similar past failures using Qdrant.

    Useful for detecting recurring failure patterns — if a new failure
    is similar to existing ones, it's a pattern worth addressing.
    """
    try:
        from prax.services.memory.vector_store import search_dense

        results = search_dense(user_id, query, top_k=top_k * 2)
        # Filter to failure journal entries
        failure_ids = []
        for r in results:
            mid = r.payload.get("memory_id", "") if hasattr(r, "payload") else ""
            if isinstance(mid, str) and mid.startswith("failure-"):
                failure_ids.append(mid.replace("failure-", ""))

        if not failure_ids:
            return []

        # Look up full failure cases
        all_cases = _load_local()
        id_set = set(failure_ids)
        matches = [c for c in all_cases if c.id in id_set]
        return matches[:top_k]
    except Exception:
        logger.debug("Semantic failure search failed (degrading gracefully)")
        return []


def get_failure_stats(user_id: str | None = None) -> dict:
    """Return failure journal statistics."""
    cases = _load_local()
    if user_id:
        cases = [c for c in cases if c.user_id == user_id]

    total = len(cases)
    resolved = sum(1 for c in cases if c.resolved)
    unresolved = total - resolved

    # Category breakdown
    categories: dict[str, int] = {}
    for c in cases:
        cat = c.failure_category or "unclassified"
        categories[cat] = categories.get(cat, 0) + 1

    # Tool frequency in failures
    tool_counts: dict[str, int] = {}
    for c in cases:
        for t in c.tools_involved:
            tool_counts[t] = tool_counts.get(t, 0) + 1

    return {
        "total": total,
        "resolved": resolved,
        "unresolved": unresolved,
        "resolution_rate": round(resolved / total, 3) if total else 0.0,
        "categories": dict(sorted(categories.items(), key=lambda x: -x[1])),
        "top_failing_tools": dict(sorted(tool_counts.items(), key=lambda x: -x[1])[:10]),
    }


# ---------------------------------------------------------------------------
# Auto-classification heuristics
# ---------------------------------------------------------------------------

_CATEGORY_SIGNALS = {
    "wrong_tool": [
        "wrong tool", "shouldn't have used", "don't use", "used the wrong",
        "not the right tool",
    ],
    "hallucination": [
        "made up", "hallucin", "not true", "incorrect", "false", "doesn't exist",
        "fabricat",
    ],
    "incomplete": [
        "didn't finish", "incomplete", "missed", "forgot", "left out", "only part",
    ],
    "wrong_format": [
        "format", "formatting", "should have been", "wrong output",
    ],
    "too_slow": [
        "too long", "took forever", "slow", "timeout", "timed out",
    ],
    "asked_instead_of_acting": [
        "just do it", "stop asking", "don't ask", "act don't ask", "why are you asking",
    ],
    "permission_error": [
        "permission", "not allowed", "blocked", "denied", "unauthorized",
    ],
}


def _auto_classify(
    user_input: str, agent_output: str, feedback_comment: str
) -> str:
    """Best-effort heuristic classification of a failure.

    Scans the feedback comment and agent output for signal words.
    Returns empty string if no confident match.
    """
    text = f"{feedback_comment} {agent_output}".lower()
    scores: dict[str, int] = {}
    for category, signals in _CATEGORY_SIGNALS.items():
        score = sum(1 for s in signals if s in text)
        if score:
            scores[category] = score
    if scores:
        return max(scores, key=scores.get)  # type: ignore[arg-type]
    return ""
