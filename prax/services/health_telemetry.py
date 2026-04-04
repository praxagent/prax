"""Append-only health telemetry store.

Records events from the orchestrator, spokes, governed tools, and context
manager.  The health monitor queries this store to detect anomalies.

Storage: JSONL file in the workspace directory (one line per event).
Events older than ``MAX_AGE_HOURS`` are pruned on read.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------


class EventCategory(StrEnum):
    CONTEXT_OVERFLOW = "context_overflow"
    CONTEXT_COMPACTION = "context_compaction"
    TOOL_ERROR = "tool_error"
    TOOL_SUCCESS = "tool_success"
    SPOKE_FAILURE = "spoke_failure"
    SPOKE_SUCCESS = "spoke_success"
    LLM_ERROR = "llm_error"
    TURN_COMPLETED = "turn_completed"
    TURN_TIMEOUT = "turn_timeout"
    RETRY = "retry"
    BUDGET_EXHAUSTED = "budget_exhausted"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class HealthEvent:
    category: str
    severity: str
    component: str = ""
    details: str = ""
    timestamp: float = field(default_factory=time.time)
    latency_ms: float = 0
    tokens: int = 0
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

MAX_AGE_HOURS = 24
_MAX_EVENTS_IN_MEMORY = 2000

_lock = threading.Lock()
_events: list[dict] = []
_file_path: Path | None = None
_initialized = False


def _get_file_path() -> Path:
    global _file_path
    if _file_path is None:
        from prax.settings import settings
        workspace = Path(settings.workspace_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        _file_path = workspace / ".health_telemetry.jsonl"
    return _file_path


def _init() -> None:
    """Load existing events from disk on first access."""
    global _initialized
    if _initialized:
        return
    _initialized = True
    path = _get_file_path()
    if not path.exists():
        return
    cutoff = time.time() - MAX_AGE_HOURS * 3600
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                    if evt.get("timestamp", 0) >= cutoff:
                        _events.append(evt)
                except json.JSONDecodeError:
                    continue
        # Keep memory bounded
        if len(_events) > _MAX_EVENTS_IN_MEMORY:
            _events[:] = _events[-_MAX_EVENTS_IN_MEMORY:]
    except Exception:
        logger.debug("Failed to load health telemetry", exc_info=True)


def record_event(
    category: str | EventCategory,
    severity: str | Severity = Severity.INFO,
    *,
    component: str = "",
    details: str = "",
    latency_ms: float = 0,
    tokens: int = 0,
    extra: dict | None = None,
) -> None:
    """Append a health event to the store.

    No-op when ``HEALTH_MONITOR_ENABLED=false`` so minimal deployments
    don't pay for telemetry I/O.
    """
    try:
        from prax.settings import settings
        if not settings.health_monitor_enabled:
            return
    except Exception:
        pass
    evt = HealthEvent(
        category=category if isinstance(category, str) else category.value,
        severity=severity if isinstance(severity, str) else severity.value,
        component=component,
        details=details[:500],
        latency_ms=latency_ms,
        tokens=tokens,
        extra=extra or {},
    )
    row = asdict(evt)
    with _lock:
        _init()
        _events.append(row)
        # Trim in memory
        if len(_events) > _MAX_EVENTS_IN_MEMORY:
            _events[:] = _events[-_MAX_EVENTS_IN_MEMORY:]
        # Append to disk (fire-and-forget)
        try:
            with open(_get_file_path(), "a") as f:
                f.write(json.dumps(row, default=str) + "\n")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def get_recent_events(
    minutes: int = 60,
    category: str | None = None,
    severity: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Return recent events, newest first."""
    with _lock:
        _init()
        cutoff = time.time() - minutes * 60
        filtered = []
        for evt in reversed(_events):
            if evt.get("timestamp", 0) < cutoff:
                break
            if category and evt.get("category") != category:
                continue
            if severity and evt.get("severity") != severity:
                continue
            filtered.append(evt)
            if len(filtered) >= limit:
                break
        return filtered


def get_rolling_stats(window_minutes: int = 60) -> dict:
    """Compute rolling statistics over the given window.

    Returns a dict suitable for the health status API.
    """
    with _lock:
        _init()
        cutoff = time.time() - window_minutes * 60
        window = [e for e in _events if e.get("timestamp", 0) >= cutoff]

    total_turns = sum(1 for e in window if e["category"] == EventCategory.TURN_COMPLETED.value)
    total_tool_calls = sum(
        1 for e in window
        if e["category"] in (EventCategory.TOOL_SUCCESS.value, EventCategory.TOOL_ERROR.value)
    )
    tool_errors = sum(1 for e in window if e["category"] == EventCategory.TOOL_ERROR.value)
    spoke_calls = sum(
        1 for e in window
        if e["category"] in (EventCategory.SPOKE_SUCCESS.value, EventCategory.SPOKE_FAILURE.value)
    )
    spoke_failures = sum(1 for e in window if e["category"] == EventCategory.SPOKE_FAILURE.value)
    context_overflows = sum(1 for e in window if e["category"] == EventCategory.CONTEXT_OVERFLOW.value)
    compactions = sum(1 for e in window if e["category"] == EventCategory.CONTEXT_COMPACTION.value)
    retries = sum(1 for e in window if e["category"] == EventCategory.RETRY.value)
    llm_errors = sum(1 for e in window if e["category"] == EventCategory.LLM_ERROR.value)
    timeouts = sum(1 for e in window if e["category"] == EventCategory.TURN_TIMEOUT.value)
    budget_exhaustions = sum(1 for e in window if e["category"] == EventCategory.BUDGET_EXHAUSTED.value)

    # Latency stats from completed turns
    turn_latencies = [
        e["latency_ms"]
        for e in window
        if e["category"] == EventCategory.TURN_COMPLETED.value and e.get("latency_ms", 0) > 0
    ]
    avg_latency = sum(turn_latencies) / len(turn_latencies) if turn_latencies else 0
    p95_latency = sorted(turn_latencies)[int(len(turn_latencies) * 0.95)] if len(turn_latencies) >= 2 else avg_latency

    return {
        "window_minutes": window_minutes,
        "total_events": len(window),
        "turns": total_turns,
        "tool_calls": total_tool_calls,
        "tool_errors": tool_errors,
        "tool_error_rate": round(tool_errors / total_tool_calls, 4) if total_tool_calls else 0,
        "spoke_calls": spoke_calls,
        "spoke_failures": spoke_failures,
        "spoke_failure_rate": round(spoke_failures / spoke_calls, 4) if spoke_calls else 0,
        "context_overflows": context_overflows,
        "compactions": compactions,
        "retries": retries,
        "llm_errors": llm_errors,
        "timeouts": timeouts,
        "budget_exhaustions": budget_exhaustions,
        "avg_latency_ms": round(avg_latency, 1),
        "p95_latency_ms": round(p95_latency, 1),
    }


def prune_old_events() -> int:
    """Remove events older than MAX_AGE_HOURS. Returns count removed."""
    cutoff = time.time() - MAX_AGE_HOURS * 3600
    with _lock:
        _init()
        before = len(_events)
        _events[:] = [e for e in _events if e.get("timestamp", 0) >= cutoff]
        removed = before - len(_events)
        # Rewrite file
        if removed > 0:
            try:
                with open(_get_file_path(), "w") as f:
                    for evt in _events:
                        f.write(json.dumps(evt, default=str) + "\n")
            except Exception:
                pass
        return removed
