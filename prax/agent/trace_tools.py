"""LangChain tool wrappers for trace semantic search + detail fetch.

These turn Prax's ``.prax/graphs/*.jsonl`` trace archive into an
introspectable "have I solved this before?" surface.  Complements
``review_my_traces`` — which digests traces into LLM advice — by
giving the agent direct, structured read access.
"""
from __future__ import annotations

import json as _json

from langchain_core.tools import tool

from prax.services import trace_search_service


def _render_unavailable(result: dict) -> str:
    msg = result.get("message") or "Trace search not available."
    return f"⚠️ {msg}"


def _render_error(result: dict) -> str:
    return f"❌ Trace search failed: {result.get('error', 'unknown error')}"


@tool
def trace_search(query: str, top_k: int = 5) -> str:
    """Find past execution traces similar to a task description.

    Use this BEFORE starting a complex or unfamiliar task — if you've
    solved something similar before, you can look up what you did
    instead of re-deriving it.

    Semantic search over every completed trace's trigger (user intent)
    + top span summaries.  Returns the top-k most similar traces with
    their trace_id, trigger, status, tool-call count, and similarity
    score.  Follow up with ``trace_detail(trace_id)`` to see the full
    tool sequence of any match.

    Args:
        query: Natural-language description of the task at hand.
               E.g., "plan a trip to Tokyo" or "find arxiv papers
               about RAG and summarise them".
        top_k: How many candidate traces to return (default 5, max 20).

    In lite deployment (no Qdrant / no embedder) returns a graceful
    not-available message; fall back to ``conversation_search`` for
    keyword lookup.
    """
    result = trace_search_service.search_traces(query, top_k=top_k)
    status = result.get("status")
    if status == "not_available":
        return _render_unavailable(result)
    if status == "error":
        return _render_error(result)
    matches = result.get("matches", [])
    if not matches:
        return f"No prior traces match `{query}`. (This is fine — you may not have done it before.)"
    lines = [f"Top {len(matches)} traces similar to `{query}`:\n"]
    for m in matches:
        score = m.get("score")
        score_str = f"{score:.2f}" if isinstance(score, float) else "?"
        tid = m.get("trace_id", "?")
        trigger = (m.get("trigger") or "").strip().replace("\n", " ")
        if len(trigger) > 140:
            trigger = trigger[:137] + "..."
        st = m.get("status", "?")
        tools_n = m.get("tool_calls", 0)
        started = m.get("started_at") or ""
        lines.append(
            f"  {tid}  score={score_str}  status={st}  tools={tools_n}  {started[:19]}\n"
            f"    trigger: {trigger or '(no trigger)'}"
        )
    lines.append(
        "\nCall trace_detail(trace_id) to see the full tool sequence "
        "of any match."
    )
    return "\n".join(lines)


@tool
def trace_detail(trace_id: str) -> str:
    """Return the full structured record of a specific past trace.

    Use this after ``trace_search`` surfaces a promising match, or
    when you already have a trace_id (from the trace log, from a
    conversation, etc.) and want to see exactly what happened.

    Returns span-by-span summaries with tool-call counts, durations,
    and status so you can reconstruct what was tried and what worked.

    Args:
        trace_id: The UUID of the trace to fetch. Copy it from the
                  output of ``trace_search``.
    """
    result = trace_search_service.get_trace_detail(trace_id)
    status = result.get("status")
    if status == "not_found":
        return f"⚠️ {result.get('message', 'trace not found')}"
    if status == "error":
        return _render_error(result)
    trace = result.get("trace", {})
    nodes = trace.get("nodes") or []
    lines = [
        f"Trace {trace.get('trace_id', '?')}  status={trace.get('status', '?')}",
        f"  trigger: {(trace.get('trigger') or '(none)').strip()[:300]}",
        f"  nodes: {len(nodes)}",
    ]
    if trace.get("session_id"):
        lines.append(f"  session: {trace.get('session_id')}")
    lines.append("")
    for i, node in enumerate(nodes, 1):
        name = node.get("name", "?")
        st = node.get("status", "?")
        dur = node.get("duration_s")
        dur_str = f"{dur:.1f}s" if isinstance(dur, int | float) else "?"
        tools_n = node.get("tool_calls", 0)
        summary = (node.get("summary") or "").strip().replace("\n", " ")
        if len(summary) > 280:
            summary = summary[:277] + "..."
        lines.append(
            f"  #{i} [{st}] {name}  tools={tools_n}  {dur_str}\n"
            f"    {summary or '(no summary)'}"
        )
    if len(lines) > 40:
        # Keep the dump bounded — 40 lines of span detail is plenty.
        lines = lines[:40] + [f"  ... +{len(nodes) - 30} more nodes truncated"]
    # Attach the raw JSON tail for programmatic use (truncated).
    try:
        raw = _json.dumps(trace, default=str)
        if len(raw) > 2000:
            raw = raw[:2000] + "..."
        lines.append(f"\nRaw: {raw}")
    except Exception:
        pass
    return "\n".join(lines)
