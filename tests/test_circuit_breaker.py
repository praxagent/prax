"""Tests for the circuit breaker."""
from __future__ import annotations

import time

import pytest

from prax.agent.circuit_breaker import (
    BreakerState,
    CircuitBreaker,
    get_all_breakers,
    get_breaker,
    reset_all,
)


@pytest.fixture(autouse=True)
def _clean():
    """Reset the global registry between tests."""
    import prax.agent.circuit_breaker as _cb
    _cb._breakers.clear()
    yield
    _cb._breakers.clear()


class TestCircuitBreakerStates:
    def test_starts_closed(self):
        cb = CircuitBreaker(name="test")
        assert cb.state == BreakerState.CLOSED
        assert cb.is_allowed()

    def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == BreakerState.CLOSED
        assert cb.is_allowed()

    def test_trips_at_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == BreakerState.OPEN
        assert not cb.is_allowed()

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        # Failure count reset, need 3 more to trip
        cb.record_failure()
        assert cb.state == BreakerState.CLOSED

    def test_open_blocks_calls(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=100)
        cb.record_failure()
        assert cb.state == BreakerState.OPEN
        assert not cb.is_allowed()

    def test_half_open_after_timeout(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        assert cb.state == BreakerState.OPEN
        time.sleep(0.02)
        assert cb.is_allowed()
        assert cb.state == BreakerState.HALF_OPEN

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.01, success_threshold=1)
        cb.record_failure()
        time.sleep(0.02)
        cb.is_allowed()  # transitions to HALF_OPEN
        cb.record_success()
        assert cb.state == BreakerState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        time.sleep(0.02)
        cb.is_allowed()  # transitions to HALF_OPEN
        cb.record_failure()
        assert cb.state == BreakerState.OPEN

    def test_success_threshold_requires_multiple(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.01, success_threshold=3)
        cb.record_failure()
        time.sleep(0.02)
        cb.is_allowed()  # HALF_OPEN
        cb.record_success()
        assert cb.state == BreakerState.HALF_OPEN  # Still half-open
        cb.record_success()
        assert cb.state == BreakerState.HALF_OPEN
        cb.record_success()
        assert cb.state == BreakerState.CLOSED  # Now closed


class TestManualReset:
    def test_reset_closes_breaker(self):
        cb = CircuitBreaker(name="test", failure_threshold=1)
        cb.record_failure()
        assert cb.state == BreakerState.OPEN
        cb.reset()
        assert cb.state == BreakerState.CLOSED
        assert cb.failure_count == 0

    def test_reset_clears_counts(self):
        cb = CircuitBreaker(name="test", failure_threshold=5)
        cb.record_failure()
        cb.record_failure()
        cb.reset()
        assert cb.failure_count == 0
        assert cb.success_count == 0


class TestToDict:
    def test_serialization(self):
        cb = CircuitBreaker(name="llm:openai", failure_threshold=5)
        d = cb.to_dict()
        assert d["name"] == "llm:openai"
        assert d["state"] == "closed"
        assert d["failure_threshold"] == 5
        assert "failure_count" in d
        assert "last_failure_time" in d


class TestGlobalRegistry:
    def test_get_breaker_creates(self):
        b = get_breaker("test_service")
        assert b.name == "test_service"
        assert b.state == BreakerState.CLOSED

    def test_get_breaker_returns_same(self):
        b1 = get_breaker("test_service")
        b2 = get_breaker("test_service")
        assert b1 is b2

    def test_get_all_breakers(self):
        get_breaker("service_a")
        get_breaker("service_b")
        all_b = get_all_breakers()
        assert "service_a" in all_b
        assert "service_b" in all_b
        assert all_b["service_a"]["state"] == "closed"

    def test_reset_all(self):
        b = get_breaker("test", failure_threshold=1)
        b.record_failure()
        assert b.state == BreakerState.OPEN
        reset_all()
        assert b.state == BreakerState.CLOSED

    def test_custom_thresholds(self):
        b = get_breaker("custom", failure_threshold=10, recovery_timeout=120.0)
        assert b.failure_threshold == 10
        assert b.recovery_timeout == 120.0
