"""Health monitoring watchdog.

Runs every N turns (default 10) to analyze health telemetry and detect
anomalies.  When problems are found, injects an advisory into the
orchestrator's next system context so Prax can decide to self-repair
or alert the human.

The monitor does NOT run as a background thread — it's called by the
orchestrator at the end of each turn (cheap counter check).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CHECK_EVERY_N_TURNS = 10  # Run a full health check every N turns
WINDOW_MINUTES = 60  # Look-back window for anomaly detection

# Thresholds for anomaly detection
TOOL_ERROR_RATE_WARN = 0.15  # 15% tool error rate
SPOKE_FAILURE_RATE_WARN = 0.20  # 20% spoke failure rate
CONTEXT_OVERFLOW_WARN = 3  # 3+ overflows in window
COMPACTION_WARN = 5  # 5+ compactions in window
LLM_ERROR_WARN = 3  # 3+ LLM errors in window
LATENCY_WARN_MS = 60_000  # 60s average response time
TIMEOUT_WARN = 2  # 2+ timeouts in window


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------


@dataclass
class HealthCheck:
    """Result of a single health check run."""
    timestamp: float = field(default_factory=time.time)
    subsystems: dict[str, SubsystemStatus] = field(default_factory=dict)
    overall: str = "healthy"  # healthy | degraded | unhealthy
    alerts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "overall": self.overall,
            "subsystems": {k: v.to_dict() for k, v in self.subsystems.items()},
            "alerts": self.alerts,
        }


@dataclass
class SubsystemStatus:
    """Health status of a single subsystem."""
    name: str
    status: str = "healthy"  # healthy | warning | error
    message: str = ""
    metric_value: float = 0
    threshold: float = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "metric_value": self.metric_value,
            "threshold": self.threshold,
        }


def run_health_check(window_minutes: int = WINDOW_MINUTES) -> HealthCheck:
    """Analyze recent telemetry and return a structured health report."""
    from prax.services.health_telemetry import get_rolling_stats

    stats = get_rolling_stats(window_minutes)
    check = HealthCheck()

    # --- Tool execution ---
    tool_status = SubsystemStatus(
        name="Tool Execution",
        metric_value=stats["tool_error_rate"],
        threshold=TOOL_ERROR_RATE_WARN,
    )
    if stats["tool_calls"] > 0 and stats["tool_error_rate"] >= TOOL_ERROR_RATE_WARN:
        tool_status.status = "warning"
        tool_status.message = (
            f"{stats['tool_errors']}/{stats['tool_calls']} tool calls failed "
            f"({stats['tool_error_rate']:.0%} error rate)"
        )
        check.alerts.append(tool_status.message)
    else:
        tool_status.message = f"{stats['tool_calls']} calls, {stats['tool_errors']} errors"
    check.subsystems["tools"] = tool_status

    # --- Spoke delegation ---
    spoke_status = SubsystemStatus(
        name="Spoke Delegation",
        metric_value=stats["spoke_failure_rate"],
        threshold=SPOKE_FAILURE_RATE_WARN,
    )
    if stats["spoke_calls"] > 0 and stats["spoke_failure_rate"] >= SPOKE_FAILURE_RATE_WARN:
        spoke_status.status = "warning"
        spoke_status.message = (
            f"{stats['spoke_failures']}/{stats['spoke_calls']} spoke delegations failed "
            f"({stats['spoke_failure_rate']:.0%} failure rate)"
        )
        check.alerts.append(spoke_status.message)
    else:
        spoke_status.message = f"{stats['spoke_calls']} delegations, {stats['spoke_failures']} failures"
    check.subsystems["spokes"] = spoke_status

    # --- Context management ---
    ctx_status = SubsystemStatus(
        name="Context Management",
        metric_value=stats["context_overflows"],
        threshold=CONTEXT_OVERFLOW_WARN,
    )
    if stats["context_overflows"] >= CONTEXT_OVERFLOW_WARN:
        ctx_status.status = "warning"
        ctx_status.message = (
            f"{stats['context_overflows']} context overflows in last {window_minutes}min "
            f"(threshold: {CONTEXT_OVERFLOW_WARN})"
        )
        check.alerts.append(ctx_status.message)
    elif stats["compactions"] >= COMPACTION_WARN:
        ctx_status.status = "warning"
        ctx_status.message = (
            f"{stats['compactions']} compactions in last {window_minutes}min "
            f"(threshold: {COMPACTION_WARN})"
        )
        check.alerts.append(ctx_status.message)
    else:
        ctx_status.message = (
            f"{stats['context_overflows']} overflows, {stats['compactions']} compactions"
        )
    check.subsystems["context"] = ctx_status

    # --- LLM provider ---
    llm_status = SubsystemStatus(
        name="LLM Provider",
        metric_value=stats["llm_errors"],
        threshold=LLM_ERROR_WARN,
    )
    if stats["llm_errors"] >= LLM_ERROR_WARN:
        llm_status.status = "error"
        llm_status.message = (
            f"{stats['llm_errors']} LLM errors in last {window_minutes}min"
        )
        check.alerts.append(llm_status.message)
    else:
        llm_status.message = f"{stats['llm_errors']} errors"
    check.subsystems["llm"] = llm_status

    # --- Response latency ---
    latency_status = SubsystemStatus(
        name="Response Latency",
        metric_value=stats["avg_latency_ms"],
        threshold=LATENCY_WARN_MS,
    )
    if stats["avg_latency_ms"] > LATENCY_WARN_MS and stats["turns"] >= 3:
        latency_status.status = "warning"
        latency_status.message = (
            f"Average response time {stats['avg_latency_ms']/1000:.1f}s "
            f"(p95: {stats['p95_latency_ms']/1000:.1f}s)"
        )
        check.alerts.append(latency_status.message)
    elif stats["turns"] > 0:
        latency_status.message = (
            f"Avg {stats['avg_latency_ms']/1000:.1f}s, "
            f"P95 {stats['p95_latency_ms']/1000:.1f}s "
            f"({stats['turns']} turns)"
        )
    else:
        latency_status.message = "No turns recorded"
    check.subsystems["latency"] = latency_status

    # --- Reliability (timeouts + retries) ---
    reliability_status = SubsystemStatus(
        name="Reliability",
        metric_value=stats["timeouts"],
        threshold=TIMEOUT_WARN,
    )
    if stats["timeouts"] >= TIMEOUT_WARN:
        reliability_status.status = "error"
        reliability_status.message = (
            f"{stats['timeouts']} timeouts, {stats['retries']} retries "
            f"in last {window_minutes}min"
        )
        check.alerts.append(reliability_status.message)
    elif stats["retries"] > 5:
        reliability_status.status = "warning"
        reliability_status.message = f"{stats['retries']} retries in last {window_minutes}min"
        check.alerts.append(reliability_status.message)
    else:
        reliability_status.message = (
            f"{stats['timeouts']} timeouts, {stats['retries']} retries"
        )
    check.subsystems["reliability"] = reliability_status

    # --- Budget ---
    budget_status = SubsystemStatus(
        name="Tool Budget",
        metric_value=stats["budget_exhaustions"],
        threshold=3,
    )
    if stats["budget_exhaustions"] >= 3:
        budget_status.status = "warning"
        budget_status.message = (
            f"Budget exhausted {stats['budget_exhaustions']} times — "
            f"consider increasing agent_max_tool_calls"
        )
        check.alerts.append(budget_status.message)
    else:
        budget_status.message = f"{stats['budget_exhaustions']} exhaustions"
    check.subsystems["budget"] = budget_status

    # --- Recent code changes (rollback awareness) ---
    recent_change = _detect_recent_code_change()
    if recent_change and any(s.status != "healthy" for s in check.subsystems.values()):
        change_status = SubsystemStatus(
            name="Recent Changes",
            status="warning",
            message=(
                f"Code changed {recent_change['minutes_ago']}min ago "
                f"({recent_change['summary']}). "
                f"Health degradation may be related — consider rollback."
            ),
        )
        check.subsystems["changes"] = change_status
        check.alerts.append(change_status.message)

    # --- Overall status ---
    statuses = [s.status for s in check.subsystems.values()]
    if "error" in statuses:
        check.overall = "unhealthy"
    elif "warning" in statuses:
        check.overall = "degraded"
    else:
        check.overall = "healthy"

    return check


# ---------------------------------------------------------------------------
# Rollback awareness — detect recent code changes
# ---------------------------------------------------------------------------

_last_known_commit: str | None = None


def _detect_recent_code_change() -> dict | None:
    """Check if Prax's code was modified recently (e.g. by a coding agent).

    Returns a dict with 'commit', 'summary', 'minutes_ago' if a change
    happened in the last 30 minutes, or None otherwise.
    """
    global _last_known_commit
    try:
        import subprocess
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H|%s|%ct"],
            capture_output=True, text=True, timeout=5,
            cwd="/app" if _is_docker() else None,
        )
        if result.returncode != 0:
            return None
        parts = result.stdout.strip().split("|", 2)
        if len(parts) < 3:
            return None
        commit_hash, summary, timestamp_str = parts
        commit_time = int(timestamp_str)
        minutes_ago = (time.time() - commit_time) / 60

        # Only flag if this is a NEW commit we haven't seen, and it's recent
        if _last_known_commit is None:
            _last_known_commit = commit_hash
            return None  # First run — establish baseline, don't alarm
        if commit_hash == _last_known_commit:
            return None  # No change
        _last_known_commit = commit_hash

        if minutes_ago > 30:
            return None  # Change is old enough to be unrelated

        return {
            "commit": commit_hash[:8],
            "summary": summary[:80],
            "minutes_ago": round(minutes_ago, 1),
        }
    except Exception:
        return None


def _is_docker() -> bool:
    """Check if we're running inside Docker."""
    try:
        from prax.settings import settings
        return settings.running_in_docker
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Turn counter and orchestrator integration
# ---------------------------------------------------------------------------

_turn_count = 0
_last_check: HealthCheck | None = None
_alert_history: list[dict] = []
_MAX_ALERT_HISTORY = 50


def get_last_check() -> HealthCheck | None:
    """Return the most recent health check result."""
    return _last_check


def get_alert_history() -> list[dict]:
    """Return recent alert history."""
    return list(_alert_history)


def on_turn_end() -> str | None:
    """Called by the orchestrator at the end of each turn.

    Returns an advisory string to inject into the next turn's system
    context if anomalies are detected, or None if everything is healthy.

    Respects ``HEALTH_MONITOR_ENABLED`` — returns None immediately if
    disabled (for minimal/lightweight deployments).
    """
    global _turn_count, _last_check

    try:
        from prax.settings import settings
        if not settings.health_monitor_enabled:
            return None
    except Exception:
        pass

    _turn_count += 1

    if _turn_count % CHECK_EVERY_N_TURNS != 0:
        return None

    try:
        check = run_health_check()
        _last_check = check

        if check.overall == "healthy":
            logger.debug("Health check: all systems healthy")
            return None

        # Record alerts
        for alert in check.alerts:
            _alert_history.append({
                "timestamp": check.timestamp,
                "message": alert,
                "overall": check.overall,
            })
        # Trim history
        if len(_alert_history) > _MAX_ALERT_HISTORY:
            _alert_history[:] = _alert_history[-_MAX_ALERT_HISTORY:]

        # Build advisory for Prax
        severity = "WARNING" if check.overall == "degraded" else "CRITICAL"
        lines = [f"[HEALTH MONITOR — {severity}]"]
        for alert in check.alerts:
            lines.append(f"  - {alert}")
        lines.append(
            "Consider investigating with prax_doctor or informing the user "
            "if the issue persists."
        )
        advisory = "\n".join(lines)
        logger.warning("Health monitor advisory:\n%s", advisory)

        # Push to TeamWork
        try:
            from prax.services.teamwork_hooks import log_activity
            log_activity(
                "Health Monitor", "health_alert",
                f"[{check.overall.upper()}] {'; '.join(check.alerts[:3])}",
            )
        except Exception:
            pass

        return advisory

    except Exception:
        logger.debug("Health check failed", exc_info=True)
        return None


def get_health_status() -> dict:
    """Return the full health status for the API.

    Runs a fresh check if none exists or the last one is stale (>5 min).
    Returns ``{"enabled": false}`` when health monitoring is disabled.
    """
    global _last_check

    try:
        from prax.settings import settings
        if not settings.health_monitor_enabled:
            return {"enabled": False}
    except Exception:
        pass

    if _last_check is None or (time.time() - _last_check.timestamp > 300):
        _last_check = run_health_check()

    from prax.services.health_telemetry import get_rolling_stats

    # Include circuit breaker states
    circuit_breakers = {}
    try:
        from prax.agent.circuit_breaker import get_all_breakers
        circuit_breakers = get_all_breakers()
    except Exception:
        pass

    # Include loop detector stats
    loop_stats = {}
    try:
        from prax.agent.loop_detector import get_loop_stats
        loop_stats = get_loop_stats()
    except Exception:
        pass

    return {
        "enabled": True,
        "check": _last_check.to_dict(),
        "stats": get_rolling_stats(WINDOW_MINUTES),
        "alert_history": _alert_history[-20:],
        "check_interval_turns": CHECK_EVERY_N_TURNS,
        "window_minutes": WINDOW_MINUTES,
        "circuit_breakers": circuit_breakers,
        "loop_detector": loop_stats,
    }
