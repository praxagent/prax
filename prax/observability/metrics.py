"""Prometheus metric definitions for Prax.

Metrics are exposed via ``/metrics`` endpoint (standard Prometheus scrape target).
All metrics degrade gracefully — if ``prometheus_client`` is not installed,
the module exposes no-op stubs so callers don't need to guard imports.

Metric families:

- **LLM**: call counts, token usage, latency (by model)
- **Spoke**: delegation counts, duration (by spoke/category)
- **Tool**: invocation counts (by tool name)
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

    LLM_CALLS = Counter(
        "prax_llm_calls_total",
        "Total LLM API calls",
        ["model", "status"],
    )

    LLM_TOKENS = Counter(
        "prax_llm_tokens_total",
        "Total LLM tokens consumed",
        ["model", "type"],  # type: input | output
    )

    LLM_DURATION = Histogram(
        "prax_llm_duration_seconds",
        "LLM call latency in seconds",
        ["model"],
        buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120),
    )

    SPOKE_CALLS = Counter(
        "prax_spoke_calls_total",
        "Total spoke/sub-agent delegations",
        ["spoke", "status"],
    )

    SPOKE_DURATION = Histogram(
        "prax_spoke_duration_seconds",
        "Spoke agent execution duration",
        ["spoke"],
        buckets=(1, 5, 10, 30, 60, 120, 300),
    )

    TOOL_CALLS = Counter(
        "prax_tool_calls_total",
        "Total tool invocations",
        ["tool"],
    )

    # Hallucination/grounding guard firings (claim_audit, semantic-entropy) so
    # the inline per-turn guards become a trended, alertable production signal.
    HALLUCINATION_GUARD = Counter(
        "prax_hallucination_guard_total",
        "Times a grounding/hallucination guard fired",
        ["type"],  # e.g. claim_audit, narrative, scheduled_briefing, semantic_entropy
    )

    # Live-traffic eval quality (reference-free), updated by the nightly job.
    EVAL_QUALITY = Gauge(
        "prax_eval_quality",
        "Reference-free live-traffic eval quality (0-1), by axis",
        ["axis"],  # overall | grounding | relevancy | correctness
    )

    METRICS_AVAILABLE = True

except ImportError:
    logger.info("prometheus_client not installed — metrics disabled")

    class _NoOp:
        """Stub that accepts any method call and does nothing."""
        def labels(self, **kwargs): return self
        def inc(self, amount=1): pass
        def observe(self, amount): pass
        def set(self, amount): pass

    LLM_CALLS = _NoOp()
    LLM_TOKENS = _NoOp()
    LLM_DURATION = _NoOp()
    SPOKE_CALLS = _NoOp()
    SPOKE_DURATION = _NoOp()
    TOOL_CALLS = _NoOp()
    HALLUCINATION_GUARD = _NoOp()
    EVAL_QUALITY = _NoOp()
    CONTENT_TYPE_LATEST = "text/plain"
    METRICS_AVAILABLE = False

    def generate_latest():
        return b"# prometheus_client not installed\n"
