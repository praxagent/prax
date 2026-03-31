"""Active Inference prediction tracking — Phases 1 & 2.

Implements extrinsic uncertainty measurement for the agent harness:

Phase 1 — **Prediction Error**: Every tool call can include an
``expected_observation`` string.  After execution the harness computes
the delta between prediction and reality.  High cumulative error
triggers an automatic warning injection into the system prompt.

Phase 2 — **Epistemic Ledger**: Tracks which files/resources the agent
has actually read in the current session.  Write-tools targeting an
unread resource are gated with a warning, enforcing a read-before-write
invariant regardless of the model's self-reported confidence.

See Research §17 in README — "Active Inference, Extrinsic Uncertainty
Measurement, and the Harness as Markov Blanket".
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase 1 — Prediction Error
# ---------------------------------------------------------------------------

_SUCCESS_PATTERNS = re.compile(
    r"\b(success|saved|created|completed|done|ok|passed|found|wrote|"
    r"updated|archived|restored|compiled|scheduled|deleted|sent)\b",
    re.IGNORECASE,
)
_FAILURE_PATTERNS = re.compile(
    r"\b(error|fail|exception|traceback|not found|denied|timeout|"
    r"refused|invalid|missing|cannot|unable|permission|corrupt|"
    r"exhausted|blocked|abort)\b",
    re.IGNORECASE,
)


@dataclass
class PredictionRecord:
    """One prediction-error measurement from a single tool call."""

    tool_name: str
    expected: str
    actual_snippet: str  # First N chars of actual result.
    error_score: float   # 0.0 = perfect match, 1.0 = total mismatch.
    timestamp: float = field(default_factory=time.time)


def compute_prediction_error(expected: str, actual: str) -> float:
    """Heuristic prediction error between an expectation and an outcome.

    The score is in [0, 1]:

    * 0.0 — outcome aligns with prediction
    * 0.5 — mild mismatch (predicted failure, got success)
    * 0.9 — strong mismatch (predicted success, got failure)

    This is deliberately simple: a cheap, deterministic signal that
    requires no LLM call.  Richer analysis belongs to the metacognitive
    layer, not here.
    """
    if not expected or not actual:
        return 0.0

    expected_lower = expected.lower()
    actual_lower = str(actual).lower()[:2000]

    expected_success = bool(_SUCCESS_PATTERNS.search(expected_lower))
    expected_failure = bool(_FAILURE_PATTERNS.search(expected_lower))
    actual_success = bool(_SUCCESS_PATTERNS.search(actual_lower))
    actual_failure = bool(_FAILURE_PATTERNS.search(actual_lower))

    # Strong category mismatch.
    if expected_success and actual_failure:
        return 0.9
    if expected_failure and actual_success:
        return 0.5  # Pleasantly surprised — still counts as mismatch.

    # Keyword overlap (rough semantic similarity proxy).
    expected_words = set(re.findall(r"[a-z0-9_./]+", expected_lower))
    actual_words = set(re.findall(r"[a-z0-9_./]+", actual_lower))
    if expected_words:
        overlap = len(expected_words & actual_words) / len(expected_words)
        return round(max(0.0, 1.0 - overlap * 2), 3)

    return 0.0


# ---------------------------------------------------------------------------
# Phase 2 — Epistemic Ledger (read-before-write invariant)
# ---------------------------------------------------------------------------

# Tools that mutate workspace or external state.
WRITE_TOOLS: frozenset[str] = frozenset({
    "workspace_save", "workspace_patch", "workspace_archive",
    "user_notes_update", "note_create", "note_update",
    "plugin_write", "self_improve_deploy",
})

# Tools that observe workspace or external state.
READ_TOOLS: frozenset[str] = frozenset({
    "workspace_read", "workspace_list", "workspace_search",
    "workspace_restore",
    "user_notes_read", "read_logs",
    "conversation_history", "conversation_search",
    "note_search", "note_read",
    "reread_instructions", "system_status",
    "agent_plan_status", "links_history", "todo_list",
})


def extract_resource_key(tool_name: str, kwargs: dict) -> str | None:
    """Extract the canonical resource identifier from tool arguments.

    Returns None when no meaningful resource path can be derived.
    """
    if tool_name in (
        "workspace_save", "workspace_patch", "workspace_read",
        "workspace_archive", "workspace_restore",
    ):
        return kwargs.get("filename")
    if tool_name in ("note_create", "note_update", "note_read"):
        key = kwargs.get("title") or kwargs.get("note_id")
        return f"note:{key}" if key else None
    return None


# ---------------------------------------------------------------------------
# Tracker (singleton per process)
# ---------------------------------------------------------------------------

_HIGH_ERROR_THRESHOLD = 0.6
_CONSECUTIVE_ERROR_WARN = 2   # warn after 2 consecutive high errors
_CONSECUTIVE_ERROR_BLOCK = 4  # hard block after 4


class PredictionTracker:
    """Session-level prediction error and epistemic state tracking.

    Thread-safe.  Reset at the start of each orchestrator turn via
    ``reset()``.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()  # Reentrant — prompt_injection() calls mean_error
        self._predictions: list[PredictionRecord] = []
        self._reads: set[str] = set()
        self._cumulative_error: float = 0.0
        self._prediction_count: int = 0
        self._consecutive_high: int = 0

    # ── lifecycle ─────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear all state for a new turn."""
        with self._lock:
            self._predictions.clear()
            self._reads.clear()
            self._cumulative_error = 0.0
            self._prediction_count = 0
            self._consecutive_high = 0

    # ── Phase 1: prediction error ─────────────────────────────────────

    def record_prediction(
        self,
        tool_name: str,
        expected: str,
        actual: str,
    ) -> PredictionRecord:
        """Compute prediction error and store the record."""
        error = compute_prediction_error(expected, actual)
        record = PredictionRecord(
            tool_name=tool_name,
            expected=expected,
            actual_snippet=str(actual)[:500],
            error_score=error,
        )
        with self._lock:
            self._predictions.append(record)
            self._cumulative_error += error
            self._prediction_count += 1
            if error >= _HIGH_ERROR_THRESHOLD:
                self._consecutive_high += 1
            else:
                self._consecutive_high = 0

        logger.info(
            "Prediction error: tool=%s score=%.2f cumulative=%.2f "
            "consecutive_high=%d",
            tool_name, error, self._cumulative_error,
            self._consecutive_high,
        )
        return record

    @property
    def mean_error(self) -> float:
        with self._lock:
            if self._prediction_count == 0:
                return 0.0
            return self._cumulative_error / self._prediction_count

    @property
    def is_high_uncertainty(self) -> bool:
        """True when consecutive high-error predictions suggest guessing."""
        with self._lock:
            return self._consecutive_high >= _CONSECUTIVE_ERROR_WARN

    # ── Phase 2: epistemic ledger ────────────────────────────────────

    def record_read(self, resource: str) -> None:
        """Mark *resource* as observed in this session."""
        with self._lock:
            self._reads.add(resource)
            logger.debug("Epistemic ledger: read '%s'", resource)

    def has_read(self, resource: str) -> bool:
        with self._lock:
            return resource in self._reads

    def check_epistemic_gate(
        self,
        tool_name: str,
        kwargs: dict,
    ) -> str | None:
        """Return a warning if a write-tool targets an unread resource.

        Returns ``None`` when the action is allowed.
        """
        if tool_name not in WRITE_TOOLS:
            return None

        resource = extract_resource_key(tool_name, kwargs)
        if not resource:
            return None  # Can't determine target — allow.

        if self.has_read(resource):
            return None

        return (
            f"⚠️ Epistemic gate: '{resource}' has not been read in this "
            f"session. Use workspace_read or the appropriate read tool to "
            f"verify its current state before modifying it. This prevents "
            f"edits based on stale assumptions."
        )

    # ── prompt injection ──────────────────────────────────────────────

    def prompt_injection(self) -> str:
        """System prompt warning when uncertainty is high."""
        with self._lock:
            if self._consecutive_high < _CONSECUTIVE_ERROR_WARN:
                return ""
            return (
                f"\n\n[ACTIVE INFERENCE WARNING: Your last "
                f"{self._consecutive_high} tool calls produced unexpected "
                f"results (mean prediction error: {self.mean_error:.2f}). "
                f"STOP making changes. Switch to read-only tools "
                f"(workspace_read, workspace_list, system_status) to "
                f"verify your assumptions before continuing.]"
            )

    # ── trace integration ────────────────────────────────────────────

    def drain_records(self) -> list[dict]:
        """Return prediction records for trace logging and clear buffer."""
        with self._lock:
            records = [
                {
                    "tool": r.tool_name,
                    "expected": r.expected[:200],
                    "actual": r.actual_snippet[:200],
                    "error": round(r.error_score, 3),
                    "ts": r.timestamp,
                }
                for r in self._predictions
            ]
            self._predictions.clear()
            return records


# ---------------------------------------------------------------------------
# Singleton access
# ---------------------------------------------------------------------------

_tracker: PredictionTracker | None = None
_tracker_lock = threading.Lock()


def get_prediction_tracker() -> PredictionTracker:
    """Return the process-wide prediction tracker (created on first call)."""
    global _tracker
    with _tracker_lock:
        if _tracker is None:
            _tracker = PredictionTracker()
    return _tracker
