"""Self-rate-limiting + retry for eval LLM calls.

Heavy benchmark runs — and several eval processes hitting ONE prepaid endpoint at
once — draw transient failures from the provider: connect timeouts, 429s, or just
an empty/blank answer. Untreated, those score as *wrong* and silently deflate a
benchmark number (an empty answer is a guaranteed miss). Worse, some providers
return the error *as the answer text* ("Connect timeout, please try again
later."), so the harness can't even tell it was infra.

This wraps a per-case executor call with:
  - a client-side **throttle** (a minimum interval between calls, process-global),
    so a run paces itself instead of bursting; and
  - **retry with exponential backoff + jitter** on a transient failure, where a
    failure is an exception, an empty answer, OR a known transient-error response
    string.

Env-configured with safe defaults — retries ON (they only turn a flake into a
real result), throttle OFF (0s, so normal runs aren't slowed). A genuine *wrong*
answer is NOT retried (only empty / transient-error / exception), so cost impact
is bounded to actually-broken calls. Set ``PRAX_EVAL_LLM_MAX_RETRIES=0`` to
disable entirely. Keyless-CI safe (a fake replay_fn never trips the retry path).

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

# Phrases a provider/harness emits *as the answer* when the call actually failed
# transiently. Matched case-insensitively against a short response — deliberately
# specific so a real answer that happens to discuss "rate limits" isn't caught
# (we only test SHORT responses; see _looks_transient).
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


def _looks_transient(resp: object) -> bool:
    """True if *resp* is an empty answer or a short transient-error string.

    Only SHORT responses (<= 400 chars) are scanned for markers, so a long,
    genuine answer that merely mentions 'rate limit' in its prose is never
    mistaken for an infra failure.
    """
    if resp is None:
        return True
    text = resp if isinstance(resp, str) else str(resp)
    if not text.strip():
        return True
    if len(text) <= 400:
        low = text.lower()
        return any(m in low for m in _TRANSIENT_MARKERS)
    return False


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
    """Run ``fn(prompt)`` with self-throttling + retry-on-transient-failure.

    Returns ``fn``'s result. On a transient failure (exception, empty answer, or a
    short transient-error response) it retries up to ``max_retries()`` times with
    backoff; if every attempt is transient it returns the last result (or re-raises
    the last exception) so the case still scores — just honestly as a failure.
    """
    retries = max_retries()
    last_exc: Exception | None = None
    last_resp: str | None = None
    for attempt in range(retries + 1):
        _throttle()
        try:
            resp = fn(prompt)
        except ExecutorError as exc:
            # A structurally-broken call (bad key, forbidden, quota) is pointless to
            # retry — surface it now so it's recorded as an error, not a wrong answer.
            if not exc.transient:
                raise
            last_exc = exc
            if attempt < retries:
                logger.warning("eval call %s transient executor error (%s), retry %d/%d",
                               label, exc, attempt + 1, retries)
                _sleep_backoff(attempt)
                continue
            raise
        except Exception as exc:  # noqa: BLE001 — any provider error is retryable here
            last_exc = exc
            if attempt < retries:
                logger.warning("eval call %s raised (%s), retry %d/%d",
                               label, exc, attempt + 1, retries)
                _sleep_backoff(attempt)
                continue
            raise
        last_resp = resp
        if not _looks_transient(resp):
            return resp
        if attempt < retries:
            logger.warning("eval call %s returned a transient failure, retry %d/%d",
                           label, attempt + 1, retries)
            _sleep_backoff(attempt)
    # Exhausted retries: prefer the last response; if we only ever saw exceptions,
    # re-raise the last one.
    if last_resp is not None:
        return last_resp
    if last_exc is not None:
        raise last_exc
    return ""
