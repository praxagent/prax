"""Feedback capture — thumbs up/down on agent messages.

Stores feedback in workspace-local JSONL and, for negative feedback,
automatically creates failure journal entries for the agent improvement loop.

The feedback → failure journal → eval pipeline is Prax's trace-centered
improvement loop, inspired by the observe → enrich → fix → validate cycle
described in modern agent engineering practice.

Usage::

    from prax.services.feedback_service import submit_feedback, get_feedback

    # User gives thumbs-down on an agent response
    submit_feedback(
        user_id="abc123",
        rating="negative",
        trace_id="a1b2c3d4e5f6",
        message_content="The agent said X but should have done Y",
        comment="It used the wrong tool",
    )

    # Retrieve recent feedback
    entries = get_feedback(user_id="abc123", rating_filter="negative")
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class FeedbackEntry:
    """A single feedback signal on an agent message."""

    id: str = ""
    user_id: str = ""
    trace_id: str = ""
    message_content: str = ""
    rating: str = ""  # "positive" or "negative"
    comment: str = ""  # optional user annotation / correction
    created_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Storage — workspace-local JSONL
# ---------------------------------------------------------------------------

def _feedback_dir() -> Path:
    """Return the directory for feedback JSONL files."""
    try:
        from prax.settings import settings
        base = Path(settings.workspace_dir).resolve()
    except Exception:
        base = Path(".")
    d = base / ".prax" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _feedback_file() -> Path:
    """Return the feedback JSONL file path."""
    return _feedback_dir() / "feedback.jsonl"


def _append_feedback(entry: FeedbackEntry) -> None:
    """Append a feedback entry to the JSONL file."""
    try:
        filepath = _feedback_file()
        line = json.dumps(asdict(entry), default=str)
        with open(filepath, "a") as f:
            f.write(line + "\n")
    except Exception:
        logger.warning("Failed to persist feedback %s", entry.id, exc_info=True)


def _load_feedback() -> list[FeedbackEntry]:
    """Load all feedback entries from disk."""
    filepath = _feedback_file()
    if not filepath.exists():
        return []
    entries = []
    try:
        for line in filepath.read_text().strip().splitlines():
            if line.strip():
                data = json.loads(line)
                entries.append(FeedbackEntry(**data))
    except Exception:
        logger.warning("Failed to load feedback file", exc_info=True)
    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def submit_feedback(
    user_id: str,
    rating: str,
    trace_id: str = "",
    message_content: str = "",
    comment: str = "",
) -> FeedbackEntry:
    """Record feedback on an agent message.

    For negative feedback, automatically creates a failure journal entry
    so the trace enters the agent improvement loop.

    Args:
        user_id: The user providing feedback.
        rating: ``"positive"`` or ``"negative"``.
        trace_id: Execution graph trace_id (if available).
        message_content: The agent message being rated.
        comment: Optional user annotation or correction.

    Returns:
        The persisted FeedbackEntry.
    """
    if rating not in ("positive", "negative"):
        raise ValueError(f"rating must be 'positive' or 'negative', got '{rating}'")

    entry = FeedbackEntry(
        user_id=user_id,
        trace_id=trace_id,
        message_content=message_content,
        rating=rating,
        comment=comment,
    )
    _append_feedback(entry)

    logger.info(
        "Feedback recorded: %s (trace=%s, rating=%s)",
        entry.id, trace_id[:8] if trace_id else "none", rating,
    )

    # Negative feedback → failure journal (async-safe, never blocks)
    if rating == "negative":
        try:
            _create_failure_case(entry)
        except Exception:
            logger.warning(
                "Failed to create failure case from feedback %s", entry.id,
                exc_info=True,
            )

    return entry


def get_feedback(
    user_id: str | None = None,
    rating_filter: str | None = None,
    limit: int = 50,
) -> list[FeedbackEntry]:
    """Retrieve feedback entries, optionally filtered.

    Args:
        user_id: Filter by user. None returns all.
        rating_filter: ``"positive"``, ``"negative"``, or None for all.
        limit: Max entries to return (most recent first).
    """
    entries = _load_feedback()
    if user_id:
        entries = [e for e in entries if e.user_id == user_id]
    if rating_filter:
        entries = [e for e in entries if e.rating == rating_filter]
    # Most recent first
    entries.sort(key=lambda e: e.created_at, reverse=True)
    return entries[:limit]


def get_feedback_stats(user_id: str | None = None) -> dict:
    """Return feedback counts and recent trends."""
    entries = _load_feedback()
    if user_id:
        entries = [e for e in entries if e.user_id == user_id]
    positive = sum(1 for e in entries if e.rating == "positive")
    negative = sum(1 for e in entries if e.rating == "negative")
    return {
        "total": len(entries),
        "positive": positive,
        "negative": negative,
        "positive_rate": round(positive / len(entries), 3) if entries else 0.0,
    }


# ---------------------------------------------------------------------------
# Failure journal bridge
# ---------------------------------------------------------------------------

def _create_failure_case(feedback: FeedbackEntry) -> None:
    """Create a failure journal entry from negative feedback.

    Extracts the execution graph (if available) and stores the failure
    case in Neo4j + Qdrant for the improvement loop.
    """
    from prax.services.memory.failure_journal import record_failure

    # Try to pull the execution graph for this trace
    graph_snapshot = {}
    if feedback.trace_id:
        try:
            from prax.agent.trace import _active_graphs
            graph = _active_graphs.get(feedback.trace_id)
            if graph:
                graph_snapshot = graph.to_dict()
        except Exception:
            pass

    # Extract the user input from the graph trigger or feedback context
    user_input = graph_snapshot.get("trigger", "")
    if not user_input and feedback.message_content:
        user_input = f"(agent output rated negative: {feedback.message_content[:200]})"

    record_failure(
        user_id=feedback.user_id,
        user_input=user_input,
        agent_output=feedback.message_content,
        trace_id=feedback.trace_id,
        graph_snapshot=graph_snapshot,
        feedback_comment=feedback.comment,
    )
