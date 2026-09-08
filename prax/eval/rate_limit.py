"""Self-rate-limiting + retry for eval LLM calls.

Heavy benchmark runs — and several eval processes hitting ONE prepaid endpoint at
once — draw transient failures from the provider: connect timeouts, 429s, 5xx.

This wraps a per-case executor call with:
  - a client-side **throttle** (a minimum interval between calls, process-global),
    so a run paces itself instead of bursting; and
  - **retry with exponential backoff + jitter** on a TRANSPORT failure — an
    exception raised by the executor (a transient :class:`ExecutorError`, or any
    other exception). A non-transient ``ExecutorError`` (bad key, forbidden,
    quota) is re-raised at once.

**A returned answer is never retried, whatever it looks like.** This module used
to re-run a case up to four times when the answer came back empty or short and
"transient-looking" ("Connect timeout, please try again later."). That is a
second, third, fourth attempt at the SAME case, selected on the content of the
first one — pass@4-on-bad-luck reported under a ``pass@1`` protocol label. The
executors are the source of truth for "did the call fail": they raise
``ExecutorError`` (or surface ``run.error``) when it did, and the aggregators
classify that error. An empty answer the executor returned is an attempt the
agent made, and it scores as one.

Env-configured with safe defaults — retries ON (they only turn a transport flake
into a real attempt), throttle OFF (0s, so normal runs aren't slowed). Set
``PRAX_EVAL_LLM_MAX_RETRIES=0`` to disable entirely. Keyless-CI safe (a fake
replay_fn never trips the retry path).

Note: ``time``/``sleep`` live here deliberately — this is eval infra, not a
workflow script.
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Phrases in an executor FAILURE REASON (an exception message / ``run.error``)
# that mark it as a retryable blip. Consulted by ``classify_transient`` only —
# never matched against an answer the executor returned.
_TRANSIENT_MARKERS = (
    "connect timeout",
    "please try again later",
    "try again later",
    "rate limit",
    "rate-limit",
    "temporarily unavailable",
    "service unavailable",
    "too many requests",
    "429",
    "502 bad gateway",
    "503",
    "upstream error",
    "overloaded",
)

class ExecutorError(Exception):
    """A case's executor failed (auth/config/timeout/provider error) — the run
    produced no gradable answer. Raised by the eval executors so an infra failure
    is recorded as an ``error`` (excluded from the score) instead of being parsed
    as a wrong answer. ``transient=True`` failures (timeouts, 429s, 5xx) are
    retried; ``transient=False`` (401/403/auth/quota/config) are re-raised at once
    — retrying a bad key just wastes calls."""

    def __init__(self, reason: str, *, transient: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.transient = transient


# Markers in a failure reason that mean "don't bother retrying" — the call is
# structurally broken (bad/missing key, forbidden, out of quota), not flaky.
_PERMANENT_MARKERS = (
    "401", "403", "unauthorized", "authentication", "missing authentication",
    "invalid api key", "invalid_api_key", "no auth", "forbidden",
    "quota", "insufficient", "permission", "invalid key",
)


def classify_transient(reason: str) -> bool:
    """True if *reason* looks like a retryable blip; False for permanent failures."""
    low = (reason or "").lower()
    if any(m in low for m in _PERMANENT_MARKERS):
        return False
    return any(m in low for m in _TRANSIENT_MARKERS) or "timeout" in low or "timed out" in low


_lock = threading.Lock()
_last_call_ts = 0.0


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def max_retries() -> int:
    """Retry attempts after the first try (PRAX_EVAL_LLM_MAX_RETRIES, default 4)."""
    return max(0, _env_int("PRAX_EVAL_LLM_MAX_RETRIES", 4))


def _backoff_base() -> float:
    return max(0.0, _env_float("PRAX_EVAL_LLM_BACKOFF_BASE_S", 2.0))


def _backoff_cap() -> float:
    return max(0.0, _env_float("PRAX_EVAL_LLM_BACKOFF_CAP_S", 30.0))


def min_interval() -> float:
    """Minimum seconds between call *starts* (PRAX_EVAL_LLM_MIN_INTERVAL_S, default 0)."""
    return max(0.0, _env_float("PRAX_EVAL_LLM_MIN_INTERVAL_S", 0.0))


def _throttle() -> None:
    """Block until at least ``min_interval`` has elapsed since the last call start."""
    gap = min_interval()
    if gap <= 0:
        return
    global _last_call_ts
    with _lock:
        now = time.monotonic()
        wait = _last_call_ts + gap - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _last_call_ts = now


def _sleep_backoff(attempt: int) -> None:
    """Exponential backoff with full jitter for *attempt* (0-indexed)."""
    delay = min(_backoff_cap(), _backoff_base() * (2 ** attempt))
    if delay > 0:
        time.sleep(random.uniform(0, delay))  # full jitter spreads concurrent retries


def call_with_rate_limit(fn: Callable[[str], str], prompt: str, *,
                         label: str = "eval") -> str:
    """Run ``fn(prompt)`` with self-throttling + retry on TRANSPORT failure only.

    Returns the first answer ``fn`` returns — empty, short, or otherwise. Only an
    exception is retried (up to ``max_retries()`` times with backoff): a transient
    :class:`ExecutorError` or any other exception. A non-transient
    ``ExecutorError`` is re-raised immediately, and when every attempt raised the
    last exception propagates so the batch records the case as an error.

    INVARIANT (pass@1): the content of an answer never triggers a retry. See the
    module docstring; pinned by ``tests/test_eval_rate_limit.py``.
    """
    retries = max_retries()
    for attempt in range(retries + 1):
        _throttle()
        try:
            resp = fn(prompt)
        except ExecutorError as exc:
            # A structurally-broken call (bad key, forbidden, quota) is pointless to
            # retry — surface it now so it's recorded as an error, not a wrong answer.
            if not exc.transient:
                raise
            if attempt < retries:
                logger.warning("eval call %s transient executor error (%s), retry %d/%d",
                               label, exc, attempt + 1, retries)
                _sleep_backoff(attempt)
                continue
            raise
        except Exception as exc:  # noqa: BLE001 — any provider error is retryable here
            if attempt < retries:
                logger.warning("eval call %s raised (%s), retry %d/%d",
                               label, exc, attempt + 1, retries)
                _sleep_backoff(attempt)
                continue
            raise
        if resp is None or not str(resp).strip():
            # Logged, NOT retried: an empty answer is the agent's attempt and
            # scores as a miss under the pass@1 protocol the summary reports.
            logger.warning("eval call %s returned an empty answer — scored as-is", label)
        return resp
    raise AssertionError("unreachable: the loop returns or raises on every path")
