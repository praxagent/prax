"""Circuit breaker for external dependencies.

Prevents cascade failures when an external service (LLM provider, browser,
sandbox) starts returning errors.  Each dependency gets its own breaker.

State machine::

    CLOSED  ─(failure threshold met)─>  OPEN
    OPEN    ─(recovery timeout elapsed)─>  HALF_OPEN
    HALF_OPEN ─(success)─>  CLOSED
    HALF_OPEN ─(failure)─>  OPEN

When OPEN, calls fail immediately with a descriptive error instead of
hitting the dead service and wasting time/tokens.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_FAILURE_THRESHOLD = 5      # Consecutive failures to trip
DEFAULT_RECOVERY_TIMEOUT = 60.0    # Seconds before trying again (half-open)
DEFAULT_SUCCESS_THRESHOLD = 2      # Successes in half-open to close


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Circuit breaker for a single dependency."""
    name: str
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD
    recovery_timeout: float = DEFAULT_RECOVERY_TIMEOUT
    success_threshold: int = DEFAULT_SUCCESS_THRESHOLD

    state: BreakerState = field(default=BreakerState.CLOSED)
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0
    last_state_change: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_success(self) -> None:
        """Record a successful call to the dependency."""
        with self._lock:
            if self.state == BreakerState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.success_threshold:
                    self._transition(BreakerState.CLOSED)
                    logger.info("Circuit breaker [%s] closed (recovered)", self.name)
            elif self.state == BreakerState.CLOSED:
                # Reset failure count on success
                self.failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call to the dependency."""
        with self._lock:
            self.last_failure_time = time.time()
            if self.state == BreakerState.HALF_OPEN:
                # Failed during probe — reopen
                self._transition(BreakerState.OPEN)
                logger.warning("Circuit breaker [%s] reopened (probe failed)", self.name)
            elif self.state == BreakerState.CLOSED:
                self.failure_count += 1
                if self.failure_count >= self.failure_threshold:
                    self._transition(BreakerState.OPEN)
                    logger.warning(
                        "Circuit breaker [%s] tripped after %d consecutive failures",
                        self.name, self.failure_count,
                    )
                    try:
                        from prax.services.health_telemetry import EventCategory, Severity, record_event
                        record_event(
                            EventCategory.LLM_ERROR, Severity.ERROR,
                            component=f"circuit_breaker:{self.name}",
                            details=f"Tripped after {self.failure_count} failures",
                        )
                    except Exception:
                        pass

    def is_allowed(self) -> bool:
        """Check whether a call is allowed through the breaker.

        Returns True if the call should proceed, False if it should be
        blocked (circuit is open).
        """
        with self._lock:
            if self.state == BreakerState.CLOSED:
                return True
            if self.state == BreakerState.OPEN:
                # Check if recovery timeout has elapsed
                if time.time() - self.last_failure_time >= self.recovery_timeout:
                    self._transition(BreakerState.HALF_OPEN)
                    logger.info("Circuit breaker [%s] entering half-open (probing)", self.name)
                    return True
                return False
            # HALF_OPEN — allow one probe call
            return True

    def _transition(self, new_state: BreakerState) -> None:
        """Transition to a new state (must be called with lock held)."""
        self.state = new_state
        self.last_state_change = time.time()
        if new_state == BreakerState.CLOSED:
            self.failure_count = 0
            self.success_count = 0
        elif new_state == BreakerState.HALF_OPEN:
            self.success_count = 0
        elif new_state == BreakerState.OPEN:
            self.success_count = 0

    def to_dict(self) -> dict:
        """Serialize for API/UI."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "last_failure_time": self.last_failure_time,
            "last_state_change": self.last_state_change,
        }

    def reset(self) -> None:
        """Force-reset the breaker to closed state."""
        with self._lock:
            self._transition(BreakerState.CLOSED)
            logger.info("Circuit breaker [%s] manually reset", self.name)


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------

_breakers: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def get_breaker(
    name: str,
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
    recovery_timeout: float = DEFAULT_RECOVERY_TIMEOUT,
) -> CircuitBreaker:
    """Get or create a circuit breaker for the named dependency."""
    with _registry_lock:
        if name not in _breakers:
            _breakers[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
            )
        return _breakers[name]


def get_all_breakers() -> dict[str, dict]:
    """Return all breaker states for the health API."""
    with _registry_lock:
        return {name: b.to_dict() for name, b in _breakers.items()}


def reset_all() -> None:
    """Reset all breakers to closed (e.g. after config change)."""
    with _registry_lock:
        for b in _breakers.values():
            b.reset()
