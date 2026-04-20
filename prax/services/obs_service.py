"""Observability datasource query service.

Thin HTTP wrappers over Loki (LogQL), Prometheus (PromQL), and Tempo
(TraceQL).  Degrades gracefully when the LGTM stack isn't running:
every function returns a structured result with a ``not_available``
status instead of raising, so the agent tools never crash in lite
deployment mode.

Result caps follow the SWE-agent ACI pattern — keep any single call
bounded so a broad query can't firehose the agent's context. LogQL
log entries are hard-capped at 200; TraceQL traces at 20; PromQL
range queries at a fixed 15-minute window with 30-second step.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from prax.settings import settings

logger = logging.getLogger(__name__)

MAX_LOG_ENTRIES = 200
MAX_TRACES = 20
DEFAULT_RANGE_MINUTES = 15
DEFAULT_STEP = "30s"
HTTP_TIMEOUT = 10.0


def _not_available(datasource: str) -> dict:
    return {
        "status": "not_available",
        "message": (
            f"{datasource} is not configured in this deployment. "
            "Observability requires the full-compose stack (docker-compose.yml). "
            "Lite deployments don't ship the LGTM stack."
        ),
    }


def is_available() -> bool:
    """True when at least one LGTM datasource URL is configured."""
    return bool(
        settings.observability_enabled
        and (settings.loki_url or settings.prometheus_url or settings.tempo_url)
    )


def query_logs(
    logql: str,
    limit: int = MAX_LOG_ENTRIES,
    since_minutes: int = 60,
) -> dict[str, Any]:
    """Run a LogQL query against Loki.

    Returns ``{"status": "ok", "entries": [...], "matched": N}`` on
    success. Each entry is ``{"ts": iso8601, "labels": {...}, "line": "..."}``.
    On failure returns ``{"status": "error", "error": "..."}``.
    On missing configuration returns ``{"status": "not_available", ...}``.
    """
    if not settings.loki_url:
        return _not_available("Loki")
    limit = max(1, min(limit, MAX_LOG_ENTRIES))
    url = f"{settings.loki_url.rstrip('/')}/loki/api/v1/query_range"
    params = {
        "query": logql,
        "limit": str(limit),
        "since": f"{since_minutes}m",
    }
    try:
        resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as e:
        logger.warning("Loki query failed: %s", e)
        return {"status": "error", "error": str(e)}
    except ValueError as e:
        return {"status": "error", "error": f"Loki returned invalid JSON: {e}"}
    entries = []
    for stream in payload.get("data", {}).get("result", []):
        labels = stream.get("stream", {})
        for ts_ns, line in stream.get("values", []):
            entries.append({"ts_ns": ts_ns, "labels": labels, "line": line})
    entries = entries[:limit]
    return {"status": "ok", "entries": entries, "matched": len(entries)}


def query_metrics(
    promql: str,
    range_minutes: int = DEFAULT_RANGE_MINUTES,
    step: str = DEFAULT_STEP,
) -> dict[str, Any]:
    """Run a PromQL range query against Prometheus.

    Returns ``{"status": "ok", "series": [...]}`` where each series is
    ``{"metric": {...labels...}, "points": [(ts, val), ...]}``. Range
    is always the last ``range_minutes`` relative to now.
    """
    if not settings.prometheus_url:
        return _not_available("Prometheus")
    import time
    end = int(time.time())
    start = end - range_minutes * 60
    url = f"{settings.prometheus_url.rstrip('/')}/api/v1/query_range"
    params = {
        "query": promql,
        "start": str(start),
        "end": str(end),
        "step": step,
    }
    try:
        resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as e:
        logger.warning("Prometheus query failed: %s", e)
        return {"status": "error", "error": str(e)}
    except ValueError as e:
        return {"status": "error", "error": f"Prometheus returned invalid JSON: {e}"}
    series = []
    for s in payload.get("data", {}).get("result", []):
        series.append({
            "metric": s.get("metric", {}),
            "points": [(float(ts), v) for ts, v in s.get("values", [])],
        })
    return {"status": "ok", "series": series}


def query_traces(
    traceql: str,
    limit: int = MAX_TRACES,
) -> dict[str, Any]:
    """Search traces via Tempo's TraceQL API.

    If ``traceql`` looks like a raw trace ID (32 hex chars) we fetch
    the trace directly; otherwise we use the search endpoint.
    Returns ``{"status": "ok", "traces": [...]}``.
    """
    if not settings.tempo_url:
        return _not_available("Tempo")
    limit = max(1, min(limit, MAX_TRACES))
    base = settings.tempo_url.rstrip("/")
    # Hex trace ID shortcut — single trace fetch.
    stripped = traceql.strip()
    if len(stripped) == 32 and all(c in "0123456789abcdefABCDEF" for c in stripped):
        url = f"{base}/api/traces/{stripped}"
        try:
            resp = requests.get(url, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            return {"status": "ok", "traces": [resp.json()]}
        except requests.RequestException as e:
            return {"status": "error", "error": str(e)}
    # TraceQL search.
    url = f"{base}/api/search"
    params = {"q": traceql, "limit": str(limit)}
    try:
        resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as e:
        logger.warning("Tempo query failed: %s", e)
        return {"status": "error", "error": str(e)}
    except ValueError as e:
        return {"status": "error", "error": f"Tempo returned invalid JSON: {e}"}
    traces = payload.get("traces", [])[:limit]
    return {"status": "ok", "traces": traces}
