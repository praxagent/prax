"""LangChain tool wrappers for observability queries.

Thin adapters over ``prax.services.obs_service``. Registered in the
orchestrator's tool list only when ``observability_enabled`` is true
— in lite deployment the tools aren't loaded at all, so the agent
never sees them as options.

All three tools share a contract: they return compact text (never
the raw JSON firehose) with results capped at the service layer so
a broad query can't flood context.
"""
from __future__ import annotations

from langchain_core.tools import tool

from prax.services import obs_service


def _render_unavailable(result: dict) -> str:
    return (
        f"⚠️ {result.get('message', 'Observability not available.')}\n"
        "Ask the user whether this deployment is lite or full mode before retrying."
    )


def _render_error(result: dict) -> str:
    return f"❌ Query failed: {result.get('error', 'unknown error')}"


@tool
def obs_query_logs(logql: str, limit: int = 100, since_minutes: int = 60) -> str:
    """Query application logs via LogQL (Loki).

    Use this for "what did Prax log about X", "why did the request fail",
    or "show me recent errors from component Y". Much better than
    `read_logs` because it queries the real log store, not just the
    on-disk tail.

    Args:
        logql: A LogQL query, e.g. '{job="prax"} |= "ERROR"' or
               '{job="prax"} |~ "agent.*timeout"'.
        limit: Max log lines to return (capped at 200).
        since_minutes: How far back to search (default 60m).

    In lite deployment (no LGTM stack), returns a graceful
    not-available message — not a crash.
    """
    result = obs_service.query_logs(logql, limit=limit, since_minutes=since_minutes)
    status = result.get("status")
    if status == "not_available":
        return _render_unavailable(result)
    if status == "error":
        return _render_error(result)
    entries = result.get("entries", [])
    if not entries:
        return f"No log entries match `{logql}` in the last {since_minutes}m."
    lines = [f"Matched {len(entries)} log entries for `{logql}`:\n"]
    for e in entries:
        labels = e.get("labels", {})
        label_str = " ".join(f"{k}={v}" for k, v in labels.items() if k in ("job", "level", "service"))
        lines.append(f"  [{label_str}] {e.get('line', '').rstrip()}")
    return "\n".join(lines)


@tool
def obs_query_metrics(promql: str, range_minutes: int = 15, step: str = "30s") -> str:
    """Query time-series metrics via PromQL (Prometheus).

    Use this for "what's the p95 latency", "how many requests in the last
    hour", "error rate trend". Returns a compact summary (not raw
    points) — first/last values and counts per series.

    Args:
        promql: PromQL expression, e.g. 'rate(http_requests_total[5m])'.
        range_minutes: Look-back window (default 15).
        step: Resolution (default "30s").

    In lite deployment, returns a graceful not-available message.
    """
    result = obs_service.query_metrics(promql, range_minutes=range_minutes, step=step)
    status = result.get("status")
    if status == "not_available":
        return _render_unavailable(result)
    if status == "error":
        return _render_error(result)
    series = result.get("series", [])
    if not series:
        return f"No series returned for `{promql}` over last {range_minutes}m."
    lines = [f"PromQL `{promql}` — {len(series)} series, last {range_minutes}m:\n"]
    for s in series[:10]:
        pts = s.get("points", [])
        metric = s.get("metric", {})
        name = metric.get("__name__", "")
        labels = " ".join(
            f"{k}={v}" for k, v in metric.items() if k != "__name__"
        )
        if pts:
            first_val = pts[0][1]
            last_val = pts[-1][1]
            lines.append(
                f"  {name}{{{labels}}}: first={first_val} last={last_val} points={len(pts)}"
            )
        else:
            lines.append(f"  {name}{{{labels}}}: (empty)")
    if len(series) > 10:
        lines.append(f"  ... +{len(series) - 10} more series (refine your query)")
    return "\n".join(lines)


@tool
def obs_query_traces(traceql: str, limit: int = 20) -> str:
    """Query distributed traces via TraceQL (Tempo).

    Use this for "show me the spans for request X", "which operation
    was slow", or "trace a specific ID". Accepts either a TraceQL
    search expression or a raw 32-hex-char trace ID for direct fetch.

    Args:
        traceql: TraceQL expression (e.g. '{resource.service.name="prax" && duration > 1s}')
                 or a 32-char hex trace ID.
        limit: Max traces to return (capped at 20).

    In lite deployment, returns a graceful not-available message.
    """
    result = obs_service.query_traces(traceql, limit=limit)
    status = result.get("status")
    if status == "not_available":
        return _render_unavailable(result)
    if status == "error":
        return _render_error(result)
    traces = result.get("traces", [])
    if not traces:
        return f"No traces matched `{traceql}`."
    lines = [f"Found {len(traces)} traces for `{traceql}`:\n"]
    for t in traces[:limit]:
        trace_id = t.get("traceID") or t.get("traceId") or "?"
        name = t.get("rootServiceName") or t.get("name") or "?"
        dur = t.get("durationMs") or t.get("duration") or "?"
        lines.append(f"  {trace_id[:16]}...  {name}  {dur}ms")
    return "\n".join(lines)


def build_obs_tools() -> list:
    """Return observability tools only when the stack is configured.

    In lite deployment (no LGTM), returns an empty list so the tools
    are not registered at all — the agent never sees them as options.
    """
    from prax.settings import settings
    if not settings.observability_enabled:
        return []
    return [obs_query_logs, obs_query_metrics, obs_query_traces]
