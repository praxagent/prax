"""Append-only health telemetry store.

Records events from the orchestrator, spokes, governed tools, and context
manager.  The health monitor queries this store to detect anomalies.

Storage: JSONL file in the workspace directory (one line per event).
Events older than ``MAX_AGE_HOURS`` are pruned on read, and the file is
rewritten from memory whenever it holds more rows than memory keeps, so it
stays bounded.

Successful tool calls are the exception: they are *counted* in memory
(``count_tool_success``) and never stored as events.  They arrive at tool-call
rate, and as events they evicted the rare alerts (LLM_ERROR, TURN_TIMEOUT,
SPOKE_FAILURE) from the in-memory cap, flooded TeamWork's recent-events feed,
and grew the file at tool-call rate.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections import deque
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
    TOOL_SUCCESS = "tool_success"  # counted by count_tool_success, never stored
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
# record_event rewrites the file from memory once it holds more rows than this.
_MAX_ROWS_ON_DISK = 2 * _MAX_EVENTS_IN_MEMORY

# Successful tool calls: one timestamp each, pruned to the stats window.  The
# maxlen is a backstop far above any real hub call rate (it is ~14 calls/s
# sustained for an hour), so it never decides a count in practice.
SUCCESS_WINDOW_MINUTES = 60  # health_monitor.WINDOW_MINUTES
_MAX_SUCCESS_TIMESTAMPS = 50_000

_lock = threading.Lock()
_events: list[dict] = []
_tool_successes: deque[float] = deque(maxlen=_MAX_SUCCESS_TIMESTAMPS)
_file_path: Path | None = None
_initialized = False
_disk_rows = 0  # rows in the JSONL file, as far as this process knows

# When this process started counting.  Successes live only in memory, so the
# TOOL_ERROR events reloaded from the file after a restart have no successes
# beside them, and rated together they would read as a 100% error rate.  The
# rate therefore uses only events since this moment.  Import time is early
# enough: nothing can count a success before importing this module.
_PROCESS_START = time.time()


def _enabled() -> bool:
    """False when ``HEALTH_MONITOR_ENABLED=false``."""
    try:
        from prax.settings import settings
        return bool(settings.health_monitor_enabled)
    except Exception:
        return True


def _get_file_path() -> Path:
    global _file_path
    if _file_path is None:
        from prax.settings import settings
        workspace = Path(settings.workspace_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        _file_path = workspace / ".health_telemetry.jsonl"
    return _file_path


def _init() -> None:
    """Load existing events from disk on first access.

    When the file held more rows than were kept (expired, past the memory cap,
    unreadable, or success rows an earlier build stored), it is rewritten from
    the kept ones, so every restart also bounds the file.
    """
    global _initialized, _disk_rows
    if _initialized:
        return
    _initialized = True
    path = _get_file_path()
    if not path.exists():
        return
    cutoff = time.time() - MAX_AGE_HOURS * 3600
    rows_read = 0
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rows_read += 1
                try:
                    evt = json.loads(line)
                    if evt.get("category") == EventCategory.TOOL_SUCCESS.value:
                        continue  # counted in memory now, never stored
                    if evt.get("timestamp", 0) >= cutoff:
                        _events.append(evt)
                except (json.JSONDecodeError, AttributeError, TypeError):
                    continue
        # Keep memory bounded
        if len(_events) > _MAX_EVENTS_IN_MEMORY:
            _events[:] = _events[-_MAX_EVENTS_IN_MEMORY:]
    except Exception:
        logger.debug("Failed to load health telemetry", exc_info=True)
        return
    _disk_rows = rows_read
    if rows_read > len(_events):
        _rewrite_file()


def _rewrite_file() -> None:
    """Replace the file with the in-memory events, atomically.  Hold ``_lock``.

    Written to a temporary file beside it and then ``os.replace``d, so a crash
    mid-write leaves the old file, never a truncated one.  Rows that another
    process appended to the same file since this one loaded it are lost: one
    writer per workspace is the supported shape.
    """
    global _disk_rows
    path = _get_file_path()
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=path.name + ".", suffix=".tmp",
        )
        with os.fdopen(fd, "w") as f:
            for evt in _events:
                f.write(json.dumps(evt, default=str) + "\n")
        os.replace(tmp_name, path)
        tmp_name = None
        _disk_rows = len(_events)
    except Exception:
        logger.debug("Failed to rewrite health telemetry", exc_info=True)
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _prune_successes(now: float) -> None:
    """Drop success timestamps older than the stats window.  Hold ``_lock``."""
    cutoff = now - SUCCESS_WINDOW_MINUTES * 60
    while _tool_successes and _tool_successes[0] < cutoff:
        _tool_successes.popleft()


def count_tool_success() -> None:
    """Count one successful hub tool call, in memory only.

    The tool error rate's denominator; not an event (see the module
    docstring).  No-op when ``HEALTH_MONITOR_ENABLED=false``.
    """
    if not _enabled():
        return
    now = time.time()
    with _lock:
        _tool_successes.append(now)
        _prune_successes(now)


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
    don't pay for telemetry I/O.  A ``TOOL_SUCCESS`` goes to
    ``count_tool_success`` and is never stored, whoever records it.
    """
    global _disk_rows
    category_value = category if isinstance(category, str) else category.value
    if category_value == EventCategory.TOOL_SUCCESS.value:
        count_tool_success()
        return
    if not _enabled():
        return
    evt = HealthEvent(
        category=category_value,
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
            _disk_rows += 1
        except Exception:
            pass
        # Memory keeps only the newest _MAX_EVENTS_IN_MEMORY, so older rows on
        # disk are ones no reader sees again; drop them in one rewrite.
        if _disk_rows > _MAX_ROWS_ON_DISK:
            _rewrite_file()


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
        now = time.time()
        cutoff = now - window_minutes * 60
        window = [e for e in _events if e.get("timestamp", 0) >= cutoff]
        _prune_successes(now)
        success_times = list(_tool_successes)

    # tool_calls counts every TOOL_ERROR in the window, including any reloaded
    # from before a restart.  The error rate does not: successes are never
    # persisted, so those errors have no denominator (see _PROCESS_START).  The
    # rate also stops where success timestamps stop, so a window longer than
    # SUCCESS_WINDOW_MINUTES cannot inflate it.
    rate_since = max(cutoff, _PROCESS_START, now - SUCCESS_WINDOW_MINUTES * 60)
    tool_error_times = [
        e.get("timestamp", 0) for e in window
        if e["category"] == EventCategory.TOOL_ERROR.value
    ]
    tool_errors = len(tool_error_times)
    total_tool_calls = sum(1 for t in success_times if t >= cutoff) + tool_errors
    rate_errors = sum(1 for t in tool_error_times if t >= rate_since)
    rate_calls = sum(1 for t in success_times if t >= rate_since) + rate_errors

    total_turns = sum(1 for e in window if e["category"] == EventCategory.TURN_COMPLETED.value)
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
        "tool_error_rate": round(rate_errors / rate_calls, 4) if rate_calls else 0,
        # The rate's own basis.  Equal to tool_calls / tool_errors except after
        # a restart, while errors reloaded from the file are still in the window.
        "tool_error_rate_calls": rate_calls,
        "tool_error_rate_errors": rate_errors,
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
        if removed > 0:
            _rewrite_file()
        return removed
