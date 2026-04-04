"""Tests for prax/services/health_telemetry.py and prax/agent/health_monitor.py."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from prax.services.health_telemetry import (
    EventCategory,
    Severity,
    get_recent_events,
    get_rolling_stats,
    prune_old_events,
    record_event,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_telemetry():
    """Clear global state in health_telemetry between tests."""
    import prax.services.health_telemetry as _tel

    _tel._events.clear()
    _tel._initialized = True  # Skip disk init
    _tel._file_path = None


def _reset_monitor():
    """Clear global state in health_monitor between tests."""
    import prax.agent.health_monitor as _mon

    _mon._turn_count = 0
    _mon._last_check = None
    _mon._alert_history.clear()


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    """Reset module-level globals and redirect file I/O to tmp_path."""
    import prax.services.health_telemetry as _tel

    _reset_telemetry()
    _reset_monitor()

    # Point file storage at tmp_path so tests never touch real workspace
    _tel._file_path = tmp_path / ".health_telemetry.jsonl"
    _tel._initialized = True

    yield

    _reset_telemetry()
    _reset_monitor()


# ---------------------------------------------------------------------------
# 1. Recording events and retrieving them
# ---------------------------------------------------------------------------


class TestRecordAndRetrieve:
    def test_record_event_appends_to_store(self):
        record_event(EventCategory.TOOL_SUCCESS, Severity.INFO, component="note_list")
        events = get_recent_events(minutes=60)
        assert len(events) == 1
        assert events[0]["category"] == "tool_success"
        assert events[0]["severity"] == "info"
        assert events[0]["component"] == "note_list"

    def test_record_multiple_events(self):
        for i in range(5):
            record_event(EventCategory.TOOL_SUCCESS, component=f"tool_{i}")
        events = get_recent_events(minutes=60)
        assert len(events) == 5

    def test_record_event_with_all_fields(self):
        record_event(
            EventCategory.TOOL_ERROR,
            Severity.ERROR,
            component="browser_click",
            details="Element not found",
            latency_ms=1500.5,
            tokens=42,
            extra={"selector": "#btn"},
        )
        events = get_recent_events(minutes=60)
        assert len(events) == 1
        evt = events[0]
        assert evt["category"] == "tool_error"
        assert evt["severity"] == "error"
        assert evt["component"] == "browser_click"
        assert evt["details"] == "Element not found"
        assert evt["latency_ms"] == 1500.5
        assert evt["tokens"] == 42
        assert evt["extra"] == {"selector": "#btn"}

    def test_record_event_accepts_string_category(self):
        record_event("tool_success", "info", component="test")
        events = get_recent_events(minutes=60)
        assert events[0]["category"] == "tool_success"

    def test_record_event_truncates_long_details(self):
        long_details = "x" * 1000
        record_event(EventCategory.TOOL_ERROR, details=long_details)
        events = get_recent_events(minutes=60)
        assert len(events[0]["details"]) == 500

    def test_events_returned_newest_first(self):
        import prax.services.health_telemetry as _tel

        now = time.time()
        for comp, offset in [("first", 0), ("second", 10), ("third", 20)]:
            _tel._events.append({
                "category": EventCategory.TOOL_SUCCESS.value,
                "severity": Severity.INFO.value,
                "component": comp,
                "details": "",
                "timestamp": now + offset,
                "latency_ms": 0,
                "tokens": 0,
                "extra": {},
            })
        events = get_recent_events(minutes=60)
        assert len(events) == 3
        assert events[0]["component"] == "third"
        assert events[1]["component"] == "second"
        assert events[2]["component"] == "first"

    def test_record_event_persists_to_disk(self, tmp_path):
        import prax.services.health_telemetry as _tel

        record_event(EventCategory.TURN_COMPLETED, component="orchestrator")
        path = _tel._file_path
        assert path.exists()
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1

    def test_limit_parameter(self):
        for i in range(10):
            record_event(EventCategory.TOOL_SUCCESS, component=f"t{i}")
        events = get_recent_events(minutes=60, limit=3)
        assert len(events) == 3


# ---------------------------------------------------------------------------
# 2. Rolling stats computation
# ---------------------------------------------------------------------------


class TestRollingStats:
    def test_empty_stats(self):
        stats = get_rolling_stats(window_minutes=60)
        assert stats["total_events"] == 0
        assert stats["turns"] == 0
        assert stats["tool_calls"] == 0
        assert stats["tool_error_rate"] == 0
        assert stats["spoke_failure_rate"] == 0
        assert stats["avg_latency_ms"] == 0

    def test_tool_error_rate(self):
        for _ in range(8):
            record_event(EventCategory.TOOL_SUCCESS)
        for _ in range(2):
            record_event(EventCategory.TOOL_ERROR, Severity.ERROR)
        stats = get_rolling_stats()
        assert stats["tool_calls"] == 10
        assert stats["tool_errors"] == 2
        assert stats["tool_error_rate"] == 0.2

    def test_spoke_failure_rate(self):
        for _ in range(4):
            record_event(EventCategory.SPOKE_SUCCESS)
        for _ in range(1):
            record_event(EventCategory.SPOKE_FAILURE, Severity.ERROR)
        stats = get_rolling_stats()
        assert stats["spoke_calls"] == 5
        assert stats["spoke_failures"] == 1
        assert stats["spoke_failure_rate"] == 0.2

    def test_latency_stats(self):
        for ms in [100, 200, 300, 400, 500]:
            record_event(
                EventCategory.TURN_COMPLETED,
                latency_ms=ms,
            )
        stats = get_rolling_stats()
        assert stats["turns"] == 5
        assert stats["avg_latency_ms"] == 300.0

    def test_context_overflow_count(self):
        for _ in range(4):
            record_event(EventCategory.CONTEXT_OVERFLOW, Severity.WARNING)
        stats = get_rolling_stats()
        assert stats["context_overflows"] == 4

    def test_compaction_count(self):
        for _ in range(6):
            record_event(EventCategory.CONTEXT_COMPACTION)
        stats = get_rolling_stats()
        assert stats["compactions"] == 6

    def test_llm_error_count(self):
        for _ in range(3):
            record_event(EventCategory.LLM_ERROR, Severity.ERROR)
        stats = get_rolling_stats()
        assert stats["llm_errors"] == 3

    def test_timeout_count(self):
        for _ in range(2):
            record_event(EventCategory.TURN_TIMEOUT, Severity.ERROR)
        stats = get_rolling_stats()
        assert stats["timeouts"] == 2

    def test_retry_count(self):
        for _ in range(7):
            record_event(EventCategory.RETRY, Severity.WARNING)
        stats = get_rolling_stats()
        assert stats["retries"] == 7

    def test_budget_exhaustion_count(self):
        for _ in range(3):
            record_event(EventCategory.BUDGET_EXHAUSTED, Severity.WARNING)
        stats = get_rolling_stats()
        assert stats["budget_exhaustions"] == 3

    def test_window_excludes_old_events(self):
        """Events outside the window are excluded from stats."""
        import prax.services.health_telemetry as _tel

        now = time.time()
        # Inject an old event directly (2 hours ago)
        _tel._events.append({
            "category": EventCategory.TOOL_ERROR.value,
            "severity": Severity.ERROR.value,
            "component": "",
            "details": "",
            "timestamp": now - 7200,
            "latency_ms": 0,
            "tokens": 0,
            "extra": {},
        })
        # Add a recent event
        record_event(EventCategory.TOOL_SUCCESS)

        stats = get_rolling_stats(window_minutes=60)
        assert stats["total_events"] == 1
        assert stats["tool_errors"] == 0
        assert stats["tool_calls"] == 1


# ---------------------------------------------------------------------------
# 3. Health check — healthy system
# ---------------------------------------------------------------------------


class TestHealthCheckHealthy:
    def test_no_events_is_healthy(self):
        from prax.agent.health_monitor import run_health_check

        check = run_health_check()
        assert check.overall == "healthy"
        assert check.alerts == []
        assert "tools" in check.subsystems
        assert check.subsystems["tools"].status == "healthy"

    def test_all_successes_is_healthy(self):
        from prax.agent.health_monitor import run_health_check

        for _ in range(20):
            record_event(EventCategory.TOOL_SUCCESS)
        for _ in range(10):
            record_event(EventCategory.SPOKE_SUCCESS)
        check = run_health_check()
        assert check.overall == "healthy"
        assert check.alerts == []

    def test_healthy_check_to_dict(self):
        from prax.agent.health_monitor import run_health_check

        check = run_health_check()
        d = check.to_dict()
        assert d["overall"] == "healthy"
        assert isinstance(d["subsystems"], dict)
        assert isinstance(d["alerts"], list)


# ---------------------------------------------------------------------------
# 4. Health check — tool error rate above threshold
# ---------------------------------------------------------------------------


class TestToolErrorRateAlert:
    def test_tool_error_rate_triggers_warning(self):
        from prax.agent.health_monitor import run_health_check

        # 15% threshold: 3 errors out of 20 calls = 15%
        for _ in range(17):
            record_event(EventCategory.TOOL_SUCCESS)
        for _ in range(3):
            record_event(EventCategory.TOOL_ERROR, Severity.ERROR)
        check = run_health_check()
        assert check.subsystems["tools"].status == "warning"
        assert len(check.alerts) >= 1
        assert any("tool calls failed" in a for a in check.alerts)

    def test_tool_error_rate_below_threshold_is_healthy(self):
        from prax.agent.health_monitor import run_health_check

        # 1 error out of 20 = 5%, below 15% threshold
        for _ in range(19):
            record_event(EventCategory.TOOL_SUCCESS)
        record_event(EventCategory.TOOL_ERROR, Severity.ERROR)
        check = run_health_check()
        assert check.subsystems["tools"].status == "healthy"


# ---------------------------------------------------------------------------
# 5. Health check — spoke failures above threshold
# ---------------------------------------------------------------------------


class TestSpokeFailureAlert:
    def test_spoke_failure_rate_triggers_warning(self):
        from prax.agent.health_monitor import run_health_check

        # 20% threshold: 2 failures out of 10 = 20%
        for _ in range(8):
            record_event(EventCategory.SPOKE_SUCCESS)
        for _ in range(2):
            record_event(EventCategory.SPOKE_FAILURE, Severity.ERROR)
        check = run_health_check()
        assert check.subsystems["spokes"].status == "warning"
        assert any("spoke delegations failed" in a for a in check.alerts)

    def test_spoke_failure_rate_below_threshold_is_healthy(self):
        from prax.agent.health_monitor import run_health_check

        # 1 failure out of 10 = 10%, below 20%
        for _ in range(9):
            record_event(EventCategory.SPOKE_SUCCESS)
        record_event(EventCategory.SPOKE_FAILURE, Severity.ERROR)
        check = run_health_check()
        assert check.subsystems["spokes"].status == "healthy"


# ---------------------------------------------------------------------------
# 6. Health check — context overflows above threshold
# ---------------------------------------------------------------------------


class TestContextOverflowAlert:
    def test_context_overflow_triggers_warning(self):
        from prax.agent.health_monitor import run_health_check

        # Threshold is 3
        for _ in range(3):
            record_event(EventCategory.CONTEXT_OVERFLOW, Severity.WARNING)
        check = run_health_check()
        assert check.subsystems["context"].status == "warning"
        assert any("context overflow" in a.lower() for a in check.alerts)

    def test_context_overflow_below_threshold_is_healthy(self):
        from prax.agent.health_monitor import run_health_check

        for _ in range(2):
            record_event(EventCategory.CONTEXT_OVERFLOW, Severity.WARNING)
        check = run_health_check()
        assert check.subsystems["context"].status == "healthy"

    def test_compaction_triggers_warning(self):
        from prax.agent.health_monitor import run_health_check

        # Compaction threshold is 5
        for _ in range(5):
            record_event(EventCategory.CONTEXT_COMPACTION)
        check = run_health_check()
        assert check.subsystems["context"].status == "warning"
        assert any("compaction" in a.lower() for a in check.alerts)

    def test_overflow_takes_priority_over_compaction(self):
        """If overflows exceed threshold, compaction check is skipped."""
        from prax.agent.health_monitor import run_health_check

        for _ in range(3):
            record_event(EventCategory.CONTEXT_OVERFLOW, Severity.WARNING)
        for _ in range(10):
            record_event(EventCategory.CONTEXT_COMPACTION)
        check = run_health_check()
        assert check.subsystems["context"].status == "warning"
        # Alert should mention overflow, not compaction
        ctx_alerts = [a for a in check.alerts if "context" in a.lower() or "overflow" in a.lower()]
        assert len(ctx_alerts) >= 1
        assert "overflow" in ctx_alerts[0].lower()


# ---------------------------------------------------------------------------
# 7. Health check — LLM errors above threshold
# ---------------------------------------------------------------------------


class TestLLMErrorAlert:
    def test_llm_errors_trigger_error_status(self):
        from prax.agent.health_monitor import run_health_check

        # Threshold is 3
        for _ in range(3):
            record_event(EventCategory.LLM_ERROR, Severity.ERROR)
        check = run_health_check()
        assert check.subsystems["llm"].status == "error"
        assert any("LLM errors" in a for a in check.alerts)

    def test_llm_errors_below_threshold_is_healthy(self):
        from prax.agent.health_monitor import run_health_check

        for _ in range(2):
            record_event(EventCategory.LLM_ERROR, Severity.ERROR)
        check = run_health_check()
        assert check.subsystems["llm"].status == "healthy"


# ---------------------------------------------------------------------------
# 8. Overall status based on subsystem statuses
# ---------------------------------------------------------------------------


class TestOverallStatus:
    def test_all_healthy_means_overall_healthy(self):
        from prax.agent.health_monitor import run_health_check

        check = run_health_check()
        assert check.overall == "healthy"

    def test_warning_subsystem_means_degraded(self):
        from prax.agent.health_monitor import run_health_check

        # Trigger a tool error rate warning
        for _ in range(7):
            record_event(EventCategory.TOOL_SUCCESS)
        for _ in range(3):
            record_event(EventCategory.TOOL_ERROR, Severity.ERROR)
        check = run_health_check()
        assert check.subsystems["tools"].status == "warning"
        assert check.overall == "degraded"

    def test_error_subsystem_means_unhealthy(self):
        from prax.agent.health_monitor import run_health_check

        # LLM errors trigger "error" status (not just "warning")
        for _ in range(5):
            record_event(EventCategory.LLM_ERROR, Severity.ERROR)
        check = run_health_check()
        assert check.subsystems["llm"].status == "error"
        assert check.overall == "unhealthy"

    def test_error_takes_priority_over_warning(self):
        from prax.agent.health_monitor import run_health_check

        # Both warning and error subsystems
        for _ in range(3):
            record_event(EventCategory.CONTEXT_OVERFLOW, Severity.WARNING)
        for _ in range(5):
            record_event(EventCategory.LLM_ERROR, Severity.ERROR)
        check = run_health_check()
        assert check.overall == "unhealthy"  # error > warning

    def test_timeout_triggers_error_status(self):
        from prax.agent.health_monitor import run_health_check

        for _ in range(2):
            record_event(EventCategory.TURN_TIMEOUT, Severity.ERROR)
        check = run_health_check()
        assert check.subsystems["reliability"].status == "error"
        assert check.overall == "unhealthy"

    def test_high_retries_triggers_warning(self):
        from prax.agent.health_monitor import run_health_check

        for _ in range(6):
            record_event(EventCategory.RETRY, Severity.WARNING)
        check = run_health_check()
        assert check.subsystems["reliability"].status == "warning"
        assert check.overall == "degraded"

    def test_budget_exhaustion_triggers_warning(self):
        from prax.agent.health_monitor import run_health_check

        for _ in range(3):
            record_event(EventCategory.BUDGET_EXHAUSTED, Severity.WARNING)
        check = run_health_check()
        assert check.subsystems["budget"].status == "warning"
        assert any("budget" in a.lower() for a in check.alerts)

    def test_latency_warning(self):
        from prax.agent.health_monitor import run_health_check

        # Need >= 3 turns with avg > 60_000ms
        for _ in range(4):
            record_event(
                EventCategory.TURN_COMPLETED,
                latency_ms=70_000,
            )
        check = run_health_check()
        assert check.subsystems["latency"].status == "warning"
        assert check.overall == "degraded"


# ---------------------------------------------------------------------------
# 9. on_turn_end() returns None when healthy, advisory when degraded
# ---------------------------------------------------------------------------


class TestOnTurnEnd:
    def test_returns_none_when_healthy(self):
        import prax.agent.health_monitor as _mon
        from prax.agent.health_monitor import CHECK_EVERY_N_TURNS, on_turn_end

        # Set turn count so the next call triggers a check
        _mon._turn_count = CHECK_EVERY_N_TURNS - 1
        with patch("prax.agent.health_monitor.log_activity", create=True):
            result = on_turn_end()
        assert result is None

    @patch("prax.agent.health_monitor.log_activity", create=True)
    def test_returns_advisory_when_degraded(self, _mock_log):
        import prax.agent.health_monitor as _mon
        from prax.agent.health_monitor import CHECK_EVERY_N_TURNS, on_turn_end

        # Create a degraded state: high tool error rate
        for _ in range(7):
            record_event(EventCategory.TOOL_SUCCESS)
        for _ in range(3):
            record_event(EventCategory.TOOL_ERROR, Severity.ERROR)

        _mon._turn_count = CHECK_EVERY_N_TURNS - 1
        # Patch the TeamWork hook to avoid import errors
        with patch("prax.services.teamwork_hooks.log_activity", create=True):
            result = on_turn_end()
        assert result is not None
        assert "HEALTH MONITOR" in result
        assert "WARNING" in result

    @patch("prax.agent.health_monitor.log_activity", create=True)
    def test_returns_critical_advisory_when_unhealthy(self, _mock_log):
        import prax.agent.health_monitor as _mon
        from prax.agent.health_monitor import CHECK_EVERY_N_TURNS, on_turn_end

        # Create unhealthy state: LLM errors above threshold
        for _ in range(5):
            record_event(EventCategory.LLM_ERROR, Severity.ERROR)

        _mon._turn_count = CHECK_EVERY_N_TURNS - 1
        with patch("prax.services.teamwork_hooks.log_activity", create=True):
            result = on_turn_end()
        assert result is not None
        assert "CRITICAL" in result

    def test_advisory_includes_alert_details(self):
        import prax.agent.health_monitor as _mon
        from prax.agent.health_monitor import CHECK_EVERY_N_TURNS, on_turn_end

        for _ in range(3):
            record_event(EventCategory.CONTEXT_OVERFLOW, Severity.WARNING)

        _mon._turn_count = CHECK_EVERY_N_TURNS - 1
        with patch("prax.services.teamwork_hooks.log_activity", create=True):
            result = on_turn_end()
        assert result is not None
        assert "overflow" in result.lower()
        assert "prax_doctor" in result

    def test_on_turn_end_records_alert_history(self):
        import prax.agent.health_monitor as _mon
        from prax.agent.health_monitor import CHECK_EVERY_N_TURNS, on_turn_end

        for _ in range(5):
            record_event(EventCategory.LLM_ERROR, Severity.ERROR)

        _mon._turn_count = CHECK_EVERY_N_TURNS - 1
        with patch("prax.services.teamwork_hooks.log_activity", create=True):
            on_turn_end()
        assert len(_mon._alert_history) >= 1
        assert "message" in _mon._alert_history[0]


# ---------------------------------------------------------------------------
# 10. on_turn_end() only checks every CHECK_EVERY_N_TURNS turns
# ---------------------------------------------------------------------------


class TestTurnCountGating:
    def test_skips_check_on_non_interval_turns(self):
        import prax.agent.health_monitor as _mon
        from prax.agent.health_monitor import CHECK_EVERY_N_TURNS, on_turn_end

        # Simulate turns 1 through CHECK_EVERY_N_TURNS-1: all should return None
        _mon._turn_count = 0
        for i in range(1, CHECK_EVERY_N_TURNS):
            result = on_turn_end()
            assert result is None, f"Turn {i} should have been skipped"
            assert _mon._turn_count == i

    def test_runs_check_on_interval_turn(self):
        import prax.agent.health_monitor as _mon
        from prax.agent.health_monitor import CHECK_EVERY_N_TURNS, on_turn_end

        # Create degraded state so we can observe the check running
        for _ in range(3):
            record_event(EventCategory.CONTEXT_OVERFLOW, Severity.WARNING)

        _mon._turn_count = CHECK_EVERY_N_TURNS - 1
        with patch("prax.services.teamwork_hooks.log_activity", create=True):
            result = on_turn_end()
        # Should have actually run the check and found issues
        assert result is not None

    def test_runs_check_on_multiples_of_interval(self):
        import prax.agent.health_monitor as _mon
        from prax.agent.health_monitor import CHECK_EVERY_N_TURNS, on_turn_end

        _mon._turn_count = 2 * CHECK_EVERY_N_TURNS - 1
        result = on_turn_end()
        # Even though healthy (returns None), the check should have run
        assert _mon._last_check is not None
        assert result is None  # healthy system

    def test_last_check_is_none_when_no_check_run(self):
        import prax.agent.health_monitor as _mon
        from prax.agent.health_monitor import on_turn_end

        _mon._turn_count = 0
        on_turn_end()  # turn 1, not at interval
        assert _mon._last_check is None


# ---------------------------------------------------------------------------
# 11. get_health_status() returns fresh check
# ---------------------------------------------------------------------------


class TestGetHealthStatus:
    def test_returns_status_dict(self):
        from prax.agent.health_monitor import get_health_status

        status = get_health_status()
        assert "check" in status
        assert "stats" in status
        assert "alert_history" in status
        assert "check_interval_turns" in status
        assert "window_minutes" in status

    def test_creates_fresh_check_when_none_exists(self):
        import prax.agent.health_monitor as _mon
        from prax.agent.health_monitor import get_health_status

        assert _mon._last_check is None
        status = get_health_status()
        assert _mon._last_check is not None
        assert status["check"]["overall"] == "healthy"

    def test_creates_fresh_check_when_stale(self):
        import prax.agent.health_monitor as _mon
        from prax.agent.health_monitor import HealthCheck, get_health_status

        # Set a stale check (> 5 minutes old)
        stale_check = HealthCheck(timestamp=time.time() - 400)
        _mon._last_check = stale_check

        get_health_status()
        # Should have created a new check
        assert _mon._last_check is not stale_check
        assert _mon._last_check.timestamp > stale_check.timestamp

    def test_reuses_recent_check(self):
        import prax.agent.health_monitor as _mon
        from prax.agent.health_monitor import HealthCheck, get_health_status

        # Set a fresh check (< 5 minutes old)
        fresh_check = HealthCheck(timestamp=time.time() - 60)
        _mon._last_check = fresh_check

        status = get_health_status()
        # Should reuse the existing check
        assert _mon._last_check is fresh_check
        assert status["check"]["timestamp"] == fresh_check.timestamp

    def test_includes_rolling_stats(self):
        from prax.agent.health_monitor import get_health_status

        for _ in range(5):
            record_event(EventCategory.TOOL_SUCCESS)
        record_event(EventCategory.TOOL_ERROR)

        status = get_health_status()
        assert status["stats"]["tool_calls"] == 6
        assert status["stats"]["tool_errors"] == 1


# ---------------------------------------------------------------------------
# 12. Event pruning removes old events
# ---------------------------------------------------------------------------


class TestEventPruning:
    def test_prune_removes_old_events(self):
        import prax.services.health_telemetry as _tel

        # Inject events with old timestamps directly
        old_ts = time.time() - (25 * 3600)  # 25 hours ago
        _tel._events.append({"category": "tool_success", "timestamp": old_ts, "severity": "info"})
        _tel._events.append({"category": "tool_success", "timestamp": old_ts - 100, "severity": "info"})
        # Add a recent event
        record_event(EventCategory.TOOL_SUCCESS)

        assert len(_tel._events) == 3
        removed = prune_old_events()
        assert removed == 2
        assert len(_tel._events) == 1

    def test_prune_keeps_recent_events(self):
        import prax.services.health_telemetry as _tel

        for _ in range(5):
            record_event(EventCategory.TOOL_SUCCESS)
        removed = prune_old_events()
        assert removed == 0
        assert len(_tel._events) == 5

    def test_prune_rewrites_file(self, tmp_path):
        import prax.services.health_telemetry as _tel

        # Add old + new events
        old_ts = time.time() - (25 * 3600)
        _tel._events.append({"category": "tool_error", "timestamp": old_ts, "severity": "error"})
        record_event(EventCategory.TOOL_SUCCESS)

        # File should have the new event
        assert _tel._file_path.exists()

        removed = prune_old_events()
        assert removed == 1

        # File should have been rewritten with only the recent event
        lines = _tel._file_path.read_text().strip().split("\n")
        assert len(lines) == 1

    def test_prune_returns_zero_when_empty(self):
        removed = prune_old_events()
        assert removed == 0


# ---------------------------------------------------------------------------
# 13. get_recent_events filtering by category and severity
# ---------------------------------------------------------------------------


class TestEventFiltering:
    def test_filter_by_category(self):
        record_event(EventCategory.TOOL_SUCCESS)
        record_event(EventCategory.TOOL_ERROR, Severity.ERROR)
        record_event(EventCategory.SPOKE_SUCCESS)
        record_event(EventCategory.TOOL_SUCCESS)

        events = get_recent_events(minutes=60, category="tool_success")
        assert len(events) == 2
        assert all(e["category"] == "tool_success" for e in events)

    def test_filter_by_severity(self):
        record_event(EventCategory.TOOL_SUCCESS, Severity.INFO)
        record_event(EventCategory.TOOL_ERROR, Severity.ERROR)
        record_event(EventCategory.LLM_ERROR, Severity.ERROR)
        record_event(EventCategory.RETRY, Severity.WARNING)

        events = get_recent_events(minutes=60, severity="error")
        assert len(events) == 2
        assert all(e["severity"] == "error" for e in events)

    def test_filter_by_category_and_severity(self):
        record_event(EventCategory.TOOL_ERROR, Severity.ERROR)
        record_event(EventCategory.TOOL_ERROR, Severity.WARNING)
        record_event(EventCategory.LLM_ERROR, Severity.ERROR)
        record_event(EventCategory.TOOL_SUCCESS, Severity.INFO)

        events = get_recent_events(
            minutes=60, category="tool_error", severity="error"
        )
        assert len(events) == 1
        assert events[0]["category"] == "tool_error"
        assert events[0]["severity"] == "error"

    def test_filter_by_time_window(self):
        import prax.services.health_telemetry as _tel

        now = time.time()
        # Old event: 1 hour ago
        _tel._events.append({
            "category": EventCategory.TOOL_SUCCESS.value,
            "severity": Severity.INFO.value,
            "component": "old",
            "details": "",
            "timestamp": now - 3600,
            "latency_ms": 0,
            "tokens": 0,
            "extra": {},
        })
        # Recent event: 1 minute ago
        _tel._events.append({
            "category": EventCategory.TOOL_SUCCESS.value,
            "severity": Severity.INFO.value,
            "component": "recent",
            "details": "",
            "timestamp": now - 60,
            "latency_ms": 0,
            "tokens": 0,
            "extra": {},
        })
        # Query with a 30-minute window
        events = get_recent_events(minutes=30)
        assert len(events) == 1
        assert events[0]["component"] == "recent"

    def test_filter_returns_empty_when_no_match(self):
        record_event(EventCategory.TOOL_SUCCESS, Severity.INFO)
        events = get_recent_events(minutes=60, category="llm_error")
        assert events == []

    def test_limit_applied_after_filtering(self):
        for _ in range(10):
            record_event(EventCategory.TOOL_ERROR, Severity.ERROR)
        for _ in range(10):
            record_event(EventCategory.TOOL_SUCCESS, Severity.INFO)

        events = get_recent_events(minutes=60, category="tool_error", limit=5)
        assert len(events) == 5
        assert all(e["category"] == "tool_error" for e in events)


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------


class TestSubsystemStatusDataclass:
    def test_subsystem_status_to_dict(self):
        from prax.agent.health_monitor import SubsystemStatus

        status = SubsystemStatus(
            name="Test Subsystem",
            status="warning",
            message="Something happened",
            metric_value=0.25,
            threshold=0.15,
        )
        d = status.to_dict()
        assert d["name"] == "Test Subsystem"
        assert d["status"] == "warning"
        assert d["message"] == "Something happened"
        assert d["metric_value"] == 0.25
        assert d["threshold"] == 0.15


class TestHealthCheckDataclass:
    def test_health_check_defaults(self):
        from prax.agent.health_monitor import HealthCheck

        check = HealthCheck()
        assert check.overall == "healthy"
        assert check.subsystems == {}
        assert check.alerts == []
        assert check.timestamp > 0


class TestMemoryBounds:
    def test_events_capped_at_max(self):
        import prax.services.health_telemetry as _tel

        for i in range(2100):
            record_event(EventCategory.TOOL_SUCCESS, component=str(i))
        assert len(_tel._events) <= 2000


class TestGetLastCheck:
    def test_get_last_check_returns_none_initially(self):
        from prax.agent.health_monitor import get_last_check

        assert get_last_check() is None

    def test_get_last_check_returns_check_after_on_turn_end(self):
        import prax.agent.health_monitor as _mon
        from prax.agent.health_monitor import CHECK_EVERY_N_TURNS, get_last_check, on_turn_end

        _mon._turn_count = CHECK_EVERY_N_TURNS - 1
        on_turn_end()
        check = get_last_check()
        assert check is not None
        assert check.overall == "healthy"


class TestAlertHistory:
    def test_alert_history_trimmed(self):
        import prax.agent.health_monitor as _mon
        from prax.agent.health_monitor import CHECK_EVERY_N_TURNS, on_turn_end

        # Fill alert history beyond max
        for i in range(60):
            _mon._alert_history.append({"message": f"alert {i}", "timestamp": time.time()})

        # Create degraded state and trigger a check
        for _ in range(5):
            record_event(EventCategory.LLM_ERROR, Severity.ERROR)

        _mon._turn_count = CHECK_EVERY_N_TURNS - 1
        with patch("prax.services.teamwork_hooks.log_activity", create=True):
            on_turn_end()
        # History should be trimmed to _MAX_ALERT_HISTORY
        assert len(_mon._alert_history) <= 50

    def test_get_alert_history_returns_copy(self):
        import prax.agent.health_monitor as _mon
        from prax.agent.health_monitor import get_alert_history

        _mon._alert_history.append({"message": "test"})
        history = get_alert_history()
        assert len(history) == 1
        # Modifying the returned list should not affect the original
        history.clear()
        assert len(_mon._alert_history) == 1


class TestOnTurnEndExceptionHandling:
    def test_on_turn_end_returns_none_on_exception(self):
        import prax.agent.health_monitor as _mon
        from prax.agent.health_monitor import CHECK_EVERY_N_TURNS, on_turn_end

        _mon._turn_count = CHECK_EVERY_N_TURNS - 1
        with patch(
            "prax.agent.health_monitor.run_health_check",
            side_effect=RuntimeError("boom"),
        ):
            result = on_turn_end()
        assert result is None
