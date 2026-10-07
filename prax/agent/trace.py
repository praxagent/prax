"""Execution tracing -- chain UUIDs, named spans, and execution graphs.

Every delegation chain gets a ``trace_id`` (chain UUID).  Individual agent
invocations get a ``span_id``.  The :class:`ExecutionGraph` tracks the tree
of all invocations, giving governing agents a big-picture view.

Usage::

    from prax.agent.trace import start_span

    span = start_span("browser", "browser")
    try:
        result = run_spoke(...)
        span.end(status="completed", summary=result[:200], tool_calls=5)
    except Exception as e:
        span.end(status="failed", summary=str(e))

    # Or as a context manager:
    with start_span("browser", "browser") as span:
        ...
"""
from __future__ import annotations

import contextvars
import functools
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import ToolMessage

from prax.agent.message_text import args_preview_for_tool, error_preview_for_tool, preview_for_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

def _usage_cost(usage_by_model: dict) -> float | None:
    """Estimated USD across models, or None when any model's rate is unknown.

    Partial pricing is treated as no pricing: summing the models we know and
    silently dropping the ones we don't would produce a number that LOOKS
    complete and is quietly smaller than the truth — the scorecard-laundering
    mistake in miniature.
    """
    if not usage_by_model:
        return None
    try:
        from prax.eval.pricing import estimate_cost
    except Exception:  # noqa: BLE001
        return None
    total = 0.0
    for model, u in usage_by_model.items():
        c = estimate_cost(model, u.get("in", 0), u.get("out", 0))
        if c is None:
            return None
        total += c
    return round(total, 4)


@dataclass
class SpanNode:
    """A single node in the execution graph -- one agent invocation."""

    span_id: str
    name: str
    parent_id: str | None
    trace_id: str
    spoke_or_category: str
    status: str = "running"
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    tool_calls: int = 0
    summary: str = ""
    tier_choices: list[dict] = field(default_factory=list)
    # Real token usage, accumulated by the LLM callback as calls complete under
    # this span. Usage used to flow to Prometheus and OTel and STOP there — the
    # trace, the one artifact a person actually opens, never carried what the
    # turn cost.
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    # Of `tokens_in`, how many the provider served from a cached prefix vs
    # wrote into the cache. A turn re-sends the system prompt, the tool
    # definitions and the whole history on EVERY tool-calling round, so this is
    # the difference between paying once for that prefix and paying N times —
    # and `tokens_in` alone cannot distinguish the two.
    cached_tokens_in: int = 0
    cache_write_tokens: int = 0
    usage_by_model: dict = field(default_factory=dict)
    # Tool spans. `requested_args_sha256` is the hash of the arguments the model
    # asked for in its response (matched by tool_call_id), in the same canonical
    # form as the secrets proxy's wire record, so scripts/check_wire_record.py
    # can tell a dropped call from one the trace misreports. Hashes only — the
    # arguments themselves are never stored.
    #
    # `args_sha256` hashes the inputs of the innermost tool layer: arguments
    # AFTER LangChain validation (schema defaults filled in, values coerced,
    # governance's expected_observation removed). It is NOT comparable with
    # `requested_args_sha256` — they differ on ordinary calls — and says only
    # "this is what the tool body received".
    #
    # `args_changed` is the comparison that is meaningful: True when some layer
    # of the call ran without a requested argument, or with a different value
    # for one (see arguments_differ). Persisted only when True.
    args_sha256: str = ""
    requested_args_sha256: str = ""
    args_changed: bool = False


class ExecutionGraph:
    """Thread-safe tree of all agent invocations in a delegation chain."""

    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self._nodes: dict[str, SpanNode] = {}
        self._lock = threading.Lock()
        self.trigger: str = ""  # User message or cron/event that started this trace
        self.session_id: str = ""  # Groups related traces into a session
        self.source: str = ""  # Origin channel: discord | sms | voice | teamwork | scheduler | task_runner

    def add_llm_usage(self, span_id: str, model: str,
                      tokens_in: int, tokens_out: int,
                      cached_in: int = 0, cache_write: int = 0) -> None:
        """Attribute one completed LLM call's usage to a span.

        `cached_in` is the subset of `tokens_in` served from a cached prefix
        (already counted in `tokens_in`, never added to it) — keeping it a
        subset rather than a separate total means existing totals and any cost
        arithmetic built on them stay correct whether or not a provider
        reports cache details.
        """
        with self._lock:
            node = self._nodes.get(span_id)
            if node is None:
                return
            node.llm_calls += 1
            node.tokens_in += int(tokens_in or 0)
            node.tokens_out += int(tokens_out or 0)
            node.cached_tokens_in += int(cached_in or 0)
            node.cache_write_tokens += int(cache_write or 0)
            per = node.usage_by_model.setdefault(model or "unknown",
                                                 {"in": 0, "out": 0})
            per["in"] += int(tokens_in or 0)
            per["out"] += int(tokens_out or 0)
            if cached_in:
                per["cached_in"] = per.get("cached_in", 0) + int(cached_in)

    def cost_so_far(self) -> tuple[float, bool]:
        """(USD priced so far, whether every model had a known rate).

        For budgets: the priced part is a LOWER bound when a model's rate is
        unknown, so a budget checked against it can stop late, never early.
        """
        try:
            from prax.eval.pricing import estimate_cost
        except Exception:  # noqa: BLE001
            return 0.0, False
        with self._lock:
            merged: dict[str, list[int]] = {}
            for n in self._nodes.values():
                for m, u in n.usage_by_model.items():
                    agg = merged.setdefault(m, [0, 0])
                    agg[0] += u["in"]
                    agg[1] += u["out"]
        total, complete = 0.0, True
        for model, (tin, tout) in merged.items():
            c = estimate_cost(model, tin, tout)
            if c is None:
                complete = False
            else:
                total += c
        return total, complete

    def add_node(self, node: SpanNode) -> None:
        with self._lock:
            self._nodes[node.span_id] = node

    def complete_node(
        self,
        span_id: str,
        *,
        status: str = "completed",
        summary: str = "",
        tool_calls: int = 0,
        tier_choices: list[dict] | None = None,
    ) -> None:
        with self._lock:
            node = self._nodes.get(span_id)
            if node:
                node.status = status
                node.finished_at = datetime.now(UTC)
                node.summary = summary[:2000]
                if tool_calls:
                    node.tool_calls = tool_calls
                if tier_choices:
                    node.tier_choices = tier_choices

    def get_summary(self) -> str:
        """Human-readable tree summary for governing agents."""
        with self._lock:
            if not self._nodes:
                return "No execution history."

            lines = [f"Execution trace [{self.trace_id[:8]}]:"]
            roots = [
                n
                for n in self._nodes.values()
                if n.parent_id is None or n.parent_id not in self._nodes
            ]
            for root in sorted(roots, key=lambda n: n.started_at):
                self._format_node(root, lines, indent=1)
            return "\n".join(lines)

    def to_dict(self) -> dict:
        """Return a JSON-serializable representation of the graph."""
        with self._lock:
            nodes = []
            statuses: list[str] = []
            root_statuses: list[str] = []
            for n in self._nodes.values():
                statuses.append(n.status)
                if n.parent_id is None or n.parent_id not in self._nodes:
                    root_statuses.append(n.status)
                models_used: list[dict] = []
                seen_keys: set[tuple[str, str, str | None]] = set()
                for tc in n.tier_choices:
                    key = (
                        str(tc.get("provider", "")),
                        str(tc.get("model", "")),
                        tc.get("tier_resolved") or tc.get("tier_requested"),
                    )
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    models_used.append({
                        "provider": tc.get("provider"),
                        "model": tc.get("model"),
                        "tier": tc.get("tier_resolved") or tc.get("tier_requested"),
                    })
                node_cost = _usage_cost(n.usage_by_model)
                nodes.append({
                    "tokens_in": n.tokens_in,
                    "tokens_out": n.tokens_out,
                    # Subset of tokens_in served from a cached prefix. Emitted
                    # only when non-zero so a provider that reports nothing
                    # doesn't litter every trace with zeros that read like a
                    # measured "no caching happened".
                    **({"cached_tokens_in": n.cached_tokens_in}
                       if n.cached_tokens_in else {}),
                    **({"cache_write_tokens": n.cache_write_tokens}
                       if n.cache_write_tokens else {}),
                    "llm_calls": n.llm_calls,
                    # None means "no rate known", which is reported as unknown
                    # rather than as $0.00 — unknown and free are different claims.
                    "cost_estimate_usd": node_cost,
                    "span_id": n.span_id,
                    "name": n.name,
                    "parent_id": n.parent_id,
                    "status": n.status,
                    "spoke_or_category": n.spoke_or_category,
                    "started_at": n.started_at.isoformat(),
                    "finished_at": n.finished_at.isoformat() if n.finished_at else None,
                    "tool_calls": n.tool_calls,
                    "summary": n.summary,
                    **({"args_sha256": n.args_sha256} if n.args_sha256 else {}),
                    **({"requested_args_sha256": n.requested_args_sha256}
                       if n.requested_args_sha256 else {}),
                    **({"args_changed": True} if n.args_changed else {}),
                    "models_used": models_used,
                    "duration_s": round(
                        (n.finished_at - n.started_at).total_seconds(), 1
                    ) if n.finished_at else round(
                        (datetime.now(UTC) - n.started_at).total_seconds(), 1
                    ),
                })
            # Sort nodes by started_at so roots come first
            nodes.sort(key=lambda n: n["started_at"])
            if "running" in root_statuses:
                overall_status = "running"
            elif "timed_out" in root_statuses or "timed_out" in statuses:
                overall_status = "timed_out"
            elif "failed" in root_statuses or "failed" in statuses:
                overall_status = "failed"
            elif "aborted" in root_statuses or "aborted" in statuses:
                overall_status = "aborted"
            elif "running" in statuses:
                overall_status = "running"
            else:
                overall_status = "completed"
            total_in = sum(n.tokens_in for n in self._nodes.values())
            total_out = sum(n.tokens_out for n in self._nodes.values())
            total_cached = sum(n.cached_tokens_in for n in self._nodes.values())
            total_cache_write = sum(n.cache_write_tokens for n in self._nodes.values())
            merged: dict[str, dict] = {}
            for n in self._nodes.values():
                for m, u in n.usage_by_model.items():
                    agg = merged.setdefault(m, {"in": 0, "out": 0})
                    agg["in"] += u["in"]
                    agg["out"] += u["out"]
            result: dict = {
                "trace_id": self.trace_id,
                "status": overall_status,
                "node_count": len(nodes),
                "tokens_in": total_in,
                "tokens_out": total_out,
                # The prefix-reuse ratio is the whole point of collecting this:
                # it says what fraction of a turn's input was paid for once
                # rather than once per tool-calling round.
                **({"cached_tokens_in": total_cached} if total_cached else {}),
                **({"cache_write_tokens": total_cache_write}
                   if total_cache_write else {}),
                # None = "no rate known", reported as unknown rather than $0.00.
                "cost_estimate_usd": _usage_cost(merged),
                "nodes": nodes,
            }
            if self.trigger:
                result["trigger"] = self.trigger
            if self.session_id:
                result["session_id"] = self.session_id
            if self.source:
                result["source"] = self.source
            return result

    def _format_node(
        self, node: SpanNode, lines: list[str], indent: int
    ) -> None:
        prefix = "  " * indent
        elapsed = ""
        if node.finished_at:
            secs = (node.finished_at - node.started_at).total_seconds()
            elapsed = f" ({secs:.1f}s)"

        status_tag = {
            "running": "[RUNNING]",
            "completed": "[OK]",
            "failed": "[FAIL]",
            "timed_out": "[TIMEOUT]",
            "aborted": "[ABORT]",
        }.get(node.status, f"[{node.status.upper()}]")

        line = (
            f"{prefix}{status_tag} {node.name} [{node.spoke_or_category}]"
            f"{elapsed}"
        )
        if node.tool_calls:
            line += f" -- {node.tool_calls} tool calls"
        lines.append(line)

        if node.tier_choices:
            # Compact tier summary: "low→gpt-5.4-nano x2, medium→gpt-5.4-mini x1"
            tier_counts: dict[str, int] = {}
            for tc in node.tier_choices:
                key = f"{tc.get('tier_requested', '?')}→{tc.get('model', '?')}"
                tier_counts[key] = tier_counts.get(key, 0) + 1
            tier_str = ", ".join(
                f"{k} x{v}" if v > 1 else k for k, v in tier_counts.items()
            )
            lines.append(f"{prefix}  tiers: {tier_str}")

        if node.summary:
            summary_text = node.summary[:120].replace("\n", " ")
            lines.append(f"{prefix}  > {summary_text}")

        children = sorted(
            [n for n in self._nodes.values() if n.parent_id == node.span_id],
            key=lambda n: n.started_at,
        )
        for child in children:
            self._format_node(child, lines, indent + 1)


@dataclass
class TraceHeartbeat:
    """Thread-safe liveness marker for a running execution trace."""

    trace_id: str
    started_at: float = field(default_factory=time.monotonic)
    last_activity_at: float = field(default_factory=time.monotonic)
    last_source: str = "trace"
    last_message: str = "trace started"
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def touch(self, source: str, message: str = "") -> None:
        with self._lock:
            self.last_activity_at = time.monotonic()
            self.last_source = source[:80] if source else "trace"
            if message:
                self.last_message = message[:240]

    def snapshot(self) -> dict:
        with self._lock:
            now = time.monotonic()
            return {
                "trace_id": self.trace_id,
                "started_at": self.started_at,
                "elapsed_s": now - self.started_at,
                "last_activity_at": self.last_activity_at,
                "idle_s": now - self.last_activity_at,
                "last_source": self.last_source,
                "last_message": self.last_message,
            }


_trace_heartbeats: dict[str, TraceHeartbeat] = {}
_trace_heartbeats_lock = threading.Lock()


def get_trace_heartbeat(trace_id: str) -> TraceHeartbeat:
    """Return the heartbeat object for a trace, creating it if needed."""
    with _trace_heartbeats_lock:
        heartbeat = _trace_heartbeats.get(trace_id)
        if heartbeat is None:
            heartbeat = TraceHeartbeat(trace_id=trace_id)
            _trace_heartbeats[trace_id] = heartbeat
        return heartbeat


def remove_trace_heartbeat(trace_id: str) -> None:
    """Drop liveness state for a completed trace."""
    with _trace_heartbeats_lock:
        _trace_heartbeats.pop(trace_id, None)


def touch_current_trace(source: str, message: str = "") -> None:
    """Update liveness for the currently active trace, if any."""
    ctx = _current_trace.get()
    if not ctx:
        return
    try:
        get_trace_heartbeat(ctx.trace_id).touch(source, message)
    except Exception:
        logger.debug("Failed to touch trace heartbeat", exc_info=True)


@dataclass
class TraceContext:
    """Immutable context that flows via contextvars."""

    trace_id: str
    span_id: str
    parent_id: str | None
    origin: str
    depth: int
    graph: ExecutionGraph


# ---------------------------------------------------------------------------
# Context variable
# ---------------------------------------------------------------------------

_current_trace: contextvars.ContextVar[TraceContext | None] = (
    contextvars.ContextVar("_current_trace", default=None)
)


@dataclass
class _PendingDelegationContext:
    """Trace parent registered by callback dispatch for a delegate_* tool.

    LangGraph may execute a tool body in a different context than the callback
    that observed ``on_tool_start``.  The callback still knows the exact
    ``delegate_*`` span it created, so it records that span here.  The spoke
    runner can claim it before starting the child spoke span.
    """

    tool_name: str
    ctx: TraceContext
    input_preview: str = ""
    created_at: float = field(default_factory=time.monotonic)


_pending_delegations: list[_PendingDelegationContext] = []
_pending_delegations_lock = threading.Lock()
_PENDING_DELEGATION_TTL_SECONDS = 120

# Stores the trace_id of the most recent root span in the current context.
# Read by callers (e.g. teamwork_routes) after an agent run to attach
# trace_id to the response message.
last_root_trace_id: contextvars.ContextVar[str | None] = (
    contextvars.ContextVar("last_root_trace_id", default=None)
)

# Preserves the execution graph from the most recent completed root span.
# Without this, the graph is lost when root_span.end() resets the contextvar.
# Read by integration tests and diagnostics after agent.run() returns.
_last_completed_graph: ExecutionGraph | None = None

# ---------------------------------------------------------------------------
# Global registry of active + recently completed execution graphs.
# Keyed by trace_id.  Active graphs are added when a root span starts;
# completed graphs are kept for up to _COMPLETED_TTL seconds.
# ---------------------------------------------------------------------------

_active_graphs: dict[str, ExecutionGraph] = {}
_active_graphs_lock = threading.Lock()
_COMPLETED_MAX = 100  # keep at most this many completed graphs in memory
_GRAPH_RETENTION_DAYS = 7  # the old fixed window; see _retention_days()


def _retention_days() -> int:
    """Days of persisted graphs to keep (TRACE_RETENTION_DAYS; 0 = forever).

    Seven days was fixed here, and the deletion ran right after loading the
    files at startup: an instance idle for a week woke up, loaded its history
    and deleted it in the same breath — invisible until the next restart
    emptied memory too. A graph line is a few KB; a long window costs little.
    """
    try:
        from prax.settings import settings
        return max(0, int(settings.trace_retention_days))
    except Exception:
        return _GRAPH_RETENTION_DAYS
_graphs_loaded = False


# ---------------------------------------------------------------------------
# Persistence — save completed graphs to disk, load on startup
# ---------------------------------------------------------------------------


def _graphs_dir() -> Path:
    """Return the directory for persisted graph JSONL files: in the records
    directory, outside the workspace the sandbox mounts (they used to be at
    ``workspace_dir/.prax/graphs`` and move on first use).
    """
    from prax.services import records
    return records.graphs_dir()


def _persist_graph(graph: ExecutionGraph) -> None:
    """Append a completed graph as one JSON line to today's file (journaled:
    ``prax/services/record_chain.py``)."""
    from prax.services import record_chain
    try:
        d = _graphs_dir()
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        filepath = d / f"graphs-{today}.jsonl"
        line = json.dumps(graph.to_dict(), default=str)
        record_chain.append(filepath, (line + "\n").encode("utf-8"))
    except Exception:
        logger.warning("Failed to persist execution graph %s", graph.trace_id, exc_info=True)


def _rotate_graph_files() -> None:
    """Keep the newest graph files inside both retention limits.

    Two limits, so the default is safe either way a deployment is used: an
    age window (TRACE_RETENTION_DAYS, 0 = no age limit) keeps a quiet
    instance's history, and a size cap (TRACE_RETENTION_MAX_MB, 0 = no cap)
    keeps a busy one from filling the disk. Oldest files go first; today's
    file, still being written, is never deleted. Each deletion is journaled
    with what the file held (``prax/services/record_chain.py``).
    """
    from prax.services import record_chain
    try:
        from prax.settings import settings
        max_mb = max(0, int(getattr(settings, "trace_retention_max_mb", 0) or 0))
    except Exception:
        max_mb = 0
    days = _retention_days()
    try:
        d = _graphs_dir()
        from datetime import timedelta
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        files = sorted(d.glob("graphs-*.jsonl"))  # oldest first (ISO dates)
        if days > 0:
            cutoff_str = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
            for f in [f for f in files if f.stem.replace("graphs-", "") < cutoff_str]:
                record_chain.delete(f, reason=f"retention: older than {days} days")
                logger.info("Rotated graph file %s (older than %d days)", f.name, days)
            files = [f for f in files if f.exists()]
        if max_mb > 0:
            budget = max_mb * 1024 * 1024
            total = sum(f.stat().st_size for f in files)
            for f in files:
                if total <= budget:
                    break
                if f.stem.replace("graphs-", "") == today:
                    continue
                total -= f.stat().st_size
                record_chain.delete(f, reason=f"retention: traces over {max_mb} MB")
                logger.info("Rotated graph file %s (traces over %d MB)", f.name, max_mb)
    except Exception:
        logger.debug("Graph file rotation failed", exc_info=True)


def _load_persisted_graphs() -> None:
    """Load recently persisted graphs into _active_graphs on startup."""
    global _graphs_loaded
    if _graphs_loaded:
        return
    _graphs_loaded = True

    try:
        d = _graphs_dir()
        if not d.exists():
            return

        # Load files from most recent first, up to _COMPLETED_MAX total.
        # Within each file, lines are in chronological order — read in
        # reverse so that the most recent graphs are loaded first.
        files = sorted(d.glob("graphs-*.jsonl"), reverse=True)
        loaded = 0
        for filepath in files:
            if loaded >= _COMPLETED_MAX:
                break
            try:
                lines = filepath.read_text().strip().splitlines()
                for line in reversed(lines):
                    if loaded >= _COMPLETED_MAX:
                        break
                    data = json.loads(line)
                    graph = _graph_from_dict(data)
                    if graph and graph.trace_id not in _active_graphs:
                        _active_graphs[graph.trace_id] = graph
                        loaded += 1
            except Exception:
                logger.warning("Failed to load graph file %s", filepath.name, exc_info=True)

        if loaded:
            logger.info("Loaded %d persisted execution graphs", loaded)

        # Rotate old files in the background
        _rotate_graph_files()
    except Exception:
        logger.warning("Failed to load persisted graphs", exc_info=True)


def _graph_from_dict(data: dict) -> ExecutionGraph | None:
    """Reconstruct an ExecutionGraph from its serialized dict."""
    trace_id = data.get("trace_id")
    nodes = data.get("nodes", [])
    if not trace_id or not nodes:
        return None

    graph = ExecutionGraph(trace_id)
    graph.trigger = data.get("trigger", "")
    graph.session_id = data.get("session_id", "")
    for nd in nodes:
        started_at = datetime.fromisoformat(nd["started_at"])
        finished_at = (
            datetime.fromisoformat(nd["finished_at"]) if nd.get("finished_at") else None
        )
        node = SpanNode(
            span_id=nd["span_id"],
            name=nd["name"],
            parent_id=nd.get("parent_id"),
            trace_id=trace_id,
            spoke_or_category=nd.get("spoke_or_category", ""),
            status=nd.get("status", "completed"),
            started_at=started_at,
            finished_at=finished_at,
            tool_calls=nd.get("tool_calls", 0),
            summary=nd.get("summary", ""),
            args_sha256=nd.get("args_sha256", ""),
            requested_args_sha256=nd.get("requested_args_sha256", ""),
            args_changed=bool(nd.get("args_changed", False)),
        )
        graph._nodes[node.span_id] = node
    return graph


# ---------------------------------------------------------------------------
# Span handle
# ---------------------------------------------------------------------------


class SpanHandle:
    """Returned by :func:`start_span`.  Call ``.end()`` when done."""

    def __init__(self, ctx: TraceContext, token: contextvars.Token, *, otel_span=None):
        self.ctx = ctx
        self.span_id = ctx.span_id
        self.trace_id = ctx.trace_id
        self._token = token
        self._otel_span = otel_span
        self._ended = False

    def end(
        self,
        *,
        status: str = "completed",
        summary: str = "",
        tool_calls: int = 0,
        tier_choices: list[dict] | None = None,
    ) -> None:
        if self._ended:
            return
        self._ended = True

        # Auto-collect tier choices made during this span's lifetime
        if tier_choices is None:
            try:
                from prax.agent.llm_factory import drain_tier_choices
                all_choices = drain_tier_choices()
                # Keep only choices that belong to this span
                mine = [c for c in all_choices if c.get("span_id") == self.span_id]

                # A choice with no span_id was made outside any span — which is
                # the NORMAL case for the agent's own model. ConversationAgent
                # resolves its LLM in __init__ and is then reused across turns,
                # so build_llm runs when no span is open and the choice is filed
                # under span_id=None. Every span then filtered it out, and
                # `models_used` came back empty on every node of every trace:
                # the trace knew which model answered and threw it away.
                #
                # The root span adopts them. It is the turn's own span, it ends
                # last, and attributing an unowned choice to a random concurrent
                # child would be a guess; attributing it to the turn is not.
                is_root = self.ctx.parent_id is None
                unowned = [c for c in all_choices if not c.get("span_id")]
                if is_root:
                    mine = mine + unowned
                    unowned = []

                tier_choices = mine
                leftover = [
                    c for c in all_choices
                    if c.get("span_id") and c.get("span_id") != self.span_id
                ] + unowned
                if leftover:
                    from prax.agent.llm_factory import _tier_choice_log, _tier_lock
                    with _tier_lock:
                        _tier_choice_log.extend(leftover)
            except Exception:
                tier_choices = None

        self.ctx.graph.complete_node(
            self.span_id,
            status=status,
            summary=summary,
            tool_calls=tool_calls,
            tier_choices=tier_choices,
        )

        # Close the OTel span with status and attributes
        if self._otel_span:
            try:
                self._otel_span.set_attribute("prax.status", status)
                self._otel_span.set_attribute("prax.tool_calls", tool_calls)
                if summary:
                    self._otel_span.set_attribute("prax.summary", summary[:200])
                if status == "failed":
                    self._otel_span.set_attribute("error", True)
                self._otel_span.end()
            except Exception:
                pass

        # Preserve the graph when a root span ends so callers (integration
        # tests, diagnostics) can still access it after the contextvar resets.
        if self.ctx.parent_id is None:
            global _last_completed_graph
            _last_completed_graph = self.ctx.graph
            remove_trace_heartbeat(self.trace_id)
            # Persist to disk so graphs survive restarts.
            _persist_graph(self.ctx.graph)
            # Prune old completed graphs from the in-memory registry.
            with _active_graphs_lock:
                completed = [
                    tid for tid, g in _active_graphs.items()
                    if tid != self.trace_id and not any(
                        n.status == "running" for n in g._nodes.values()
                    )
                ]
                while len(completed) > _COMPLETED_MAX:
                    _active_graphs.pop(completed.pop(0), None)

        try:
            _current_trace.reset(self._token)
        except ValueError:
            pass  # Token from a different context (thread pool)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._ended:
            status = "failed" if exc_type else "completed"
            summary = str(exc_val)[:200] if exc_val else ""
            self.end(status=status, summary=summary)
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _start_otel_span(name: str, spoke_or_category: str, trace_id: str):
    """Create an OTel span if the tracer is initialized.  Returns span or None."""
    try:
        from prax.observability.setup import get_tracer
        tracer = get_tracer()
        if not tracer:
            return None
        return tracer.start_span(
            name=f"prax.{spoke_or_category}.{name}",
            attributes={
                "prax.trace_id": trace_id,
                "prax.span_name": name,
                "prax.spoke_or_category": spoke_or_category,
            },
        )
    except Exception:
        return None


class DelegationDepthExceeded(RuntimeError):
    """Raised when delegation nesting exceeds the configured limit."""


def start_span(
    name: str,
    spoke_or_category: str,
    *,
    parent_context: TraceContext | None = None,
) -> SpanHandle:
    """Create a span -- child of current trace, or new trace if none exists.

    Returns a :class:`SpanHandle`.  Call ``handle.end(...)`` to close,
    or use as a context manager.

    If OpenTelemetry is initialized, a corresponding OTel span is also created
    and linked to the Prax execution graph for distributed trace export.

    Raises :class:`DelegationDepthExceeded` if the delegation chain exceeds
    the configured ``AGENT_MAX_DELEGATION_DEPTH``.
    """
    parent = parent_context or _current_trace.get()

    if parent:
        trace_id = parent.trace_id
        parent_id = parent.span_id
        graph = parent.graph
        depth = parent.depth + 1

        # Enforce delegation depth limit to prevent infinite recursive delegation.
        try:
            from prax.settings import settings
            max_depth = settings.agent_max_delegation_depth
        except Exception:
            max_depth = 4  # safe default
        if depth > max_depth:
            logger.error(
                "Delegation depth %d exceeds limit %d — aborting span '%s' [%s]",
                depth, max_depth, name, spoke_or_category,
            )
            raise DelegationDepthExceeded(
                f"Delegation depth {depth} exceeds maximum of {max_depth}. "
                f"Refusing to start span '{name}'. This usually means the agent "
                f"is in a recursive delegation loop."
            )
    else:
        trace_id = uuid.uuid4().hex[:16]
        parent_id = None
        graph = ExecutionGraph(trace_id)
        depth = 0
        # Record the root trace_id so callers can attach it to responses.
        last_root_trace_id.set(trace_id)
        # Register in global registry for the graphs API.
        with _active_graphs_lock:
            _active_graphs[trace_id] = graph

    span_id = uuid.uuid4().hex[:12]

    node = SpanNode(
        span_id=span_id,
        name=name,
        parent_id=parent_id,
        trace_id=trace_id,
        spoke_or_category=spoke_or_category,
    )
    graph.add_node(node)
    try:
        get_trace_heartbeat(trace_id).touch(
            f"{spoke_or_category}:{name}",
            f"started span {name}",
        )
    except Exception:
        logger.debug("Failed to update heartbeat for span start", exc_info=True)

    ctx = TraceContext(
        trace_id=trace_id,
        span_id=span_id,
        parent_id=parent_id,
        origin=name,
        depth=depth,
        graph=graph,
    )
    token = _current_trace.set(ctx)

    # Bridge to OpenTelemetry
    otel_span = _start_otel_span(name, spoke_or_category, trace_id)

    logger.debug(
        "Span [%s/%s] %s started (depth=%d, parent=%s)",
        trace_id[:8],
        span_id[:8],
        name,
        depth,
        parent_id[:8] if parent_id else "root",
    )
    return SpanHandle(ctx, token, otel_span=otel_span)


def get_current_trace() -> TraceContext | None:
    """Return the active trace context, or ``None``."""
    return _current_trace.get()


def _prune_pending_delegations(now: float | None = None) -> None:
    now = now or time.monotonic()
    _pending_delegations[:] = [
        pending for pending in _pending_delegations
        if now - pending.created_at <= _PENDING_DELEGATION_TTL_SECONDS
    ]


def _matches_pending_input(pending: _PendingDelegationContext, task: str) -> bool:
    if not task:
        return False
    task_norm = " ".join(task.lower().split())
    input_norm = " ".join((pending.input_preview or "").lower().split())
    if not task_norm or not input_norm:
        return False
    return task_norm[:160] in input_norm or input_norm[:160] in task_norm


def register_pending_delegation_context(
    tool_name: str,
    ctx: TraceContext,
    input_preview: str = "",
) -> None:
    """Record the trace context for a delegate_* tool span.

    This is intentionally process-local and short-lived.  It bridges the gap
    between LangChain callback dispatch and actual tool execution when they do
    not share a contextvar context.
    """
    if not tool_name.startswith("delegate_"):
        return
    with _pending_delegations_lock:
        _prune_pending_delegations()
        _pending_delegations.append(
            _PendingDelegationContext(
                tool_name=tool_name,
                ctx=ctx,
                input_preview=str(input_preview)[:1000],
            )
        )


def claim_pending_delegation_context(
    tool_name: str | None = None,
    task: str = "",
) -> TraceContext | None:
    """Claim a pending delegate_* context for a spoke starting out-of-band."""
    with _pending_delegations_lock:
        _prune_pending_delegations()
        if not _pending_delegations:
            return None

        candidates = list(enumerate(_pending_delegations))
        if tool_name:
            exact = [
                (idx, pending) for idx, pending in candidates
                if pending.tool_name == tool_name
            ]
            if exact:
                task_matches = [
                    (idx, pending) for idx, pending in exact
                    if _matches_pending_input(pending, task)
                ]
                if task_matches or len(exact) == 1:
                    idx, pending = (task_matches or exact)[0]
                    _pending_delegations.pop(idx)
                    return pending.ctx

        if task:
            task_matches = [
                (idx, pending) for idx, pending in candidates
                if _matches_pending_input(pending, task)
            ]
            if task_matches:
                idx, pending = task_matches[0]
                _pending_delegations.pop(idx)
                return pending.ctx

        if len(_pending_delegations) == 1:
            return _pending_delegations.pop(0).ctx

    return None


def discard_pending_delegation_context(span_id: str) -> None:
    """Drop an unclaimed pending delegation when the delegate tool ends."""
    if not span_id:
        return
    with _pending_delegations_lock:
        _pending_delegations[:] = [
            pending for pending in _pending_delegations
            if pending.ctx.span_id != span_id
        ]


def get_graph_summary() -> str:
    """Return a human-readable summary of the current execution graph."""
    ctx = _current_trace.get()
    if ctx:
        return ctx.graph.get_summary()
    # Fall back to the last completed root graph (e.g. after agent.run() returns)
    if _last_completed_graph:
        return _last_completed_graph.get_summary()
    return "No active trace."


def get_last_completed_graph() -> ExecutionGraph | None:
    """Return the execution graph from the most recent completed root span.

    Useful for integration tests and diagnostics that need to inspect the
    graph after ``agent.run()`` has returned (by which time the contextvar
    has been reset).
    """
    return _last_completed_graph


def get_all_tier_choices(graph: ExecutionGraph | None = None) -> list[dict]:
    """Collect tier choices from all nodes in the graph.

    If no graph is provided, uses the last completed root graph.
    Returns a flat list of tier choice dicts, ordered by timestamp.
    """
    g = graph or _last_completed_graph
    if not g:
        return []
    with g._lock:
        choices = []
        for node in g._nodes.values():
            for tc in node.tier_choices:
                tc_copy = dict(tc)
                tc_copy["span_name"] = tc_copy.get("span_name") or node.name
                choices.append(tc_copy)
    choices.sort(key=lambda c: c.get("ts", 0))
    return choices


def get_active_graphs_json() -> list[dict]:
    """Return all active and recently completed execution graphs as dicts.

    Used by the ``/execution/graphs`` API endpoint to feed the TeamWork
    graph visualization panel.  On first call, loads persisted graphs from
    disk so they survive Prax restarts.
    """
    with _active_graphs_lock:
        _load_persisted_graphs()
        graphs = list(_active_graphs.values())
    # Sort: running first, then by most recent
    result = [g.to_dict() for g in graphs]
    result.sort(key=lambda g: (0 if g["status"] == "running" else 1, g["nodes"][0]["started_at"] if g["nodes"] else ""), reverse=False)
    # Running first
    running = [g for g in result if g["status"] == "running"]
    done = [g for g in result if g["status"] != "running"]
    done.sort(key=lambda g: g["nodes"][0]["started_at"] if g["nodes"] else "", reverse=True)
    return running + done


def delete_graph(trace_id: str) -> bool:
    """Remove a graph from memory and scrub it from persisted JSONL files."""
    with _active_graphs_lock:
        removed = _active_graphs.pop(trace_id, None)

    # Also remove from persisted files so it doesn't reload on restart. The
    # rewrite is journaled, naming the trace (prax/services/record_chain.py).
    from prax.services import record_chain

    def scrub(data: bytes) -> bytes | None:
        lines = data.decode("utf-8").strip().splitlines()
        kept = [ln for ln in lines if f'"trace_id": "{trace_id}"' not in ln]
        if len(kept) == len(lines):
            return None
        return ("\n".join(kept) + "\n" if kept else "").encode("utf-8")

    try:
        d = _graphs_dir()
        for filepath in d.glob("graphs-*.jsonl"):
            record_chain.rewrite(filepath, scrub, note=f"delete trace {trace_id}")
    except Exception:
        logger.warning("Failed to scrub graph %s from disk", trace_id, exc_info=True)

    return removed is not None


def update_graph_session(trace_id: str, new_session_id: str) -> bool:
    """Move a graph to a different session. Updates in memory and on disk."""
    with _active_graphs_lock:
        graph = _active_graphs.get(trace_id)
        if not graph:
            return False
        graph.session_id = new_session_id

    # Update on disk — rewrite the line with the new session_id (journaled).
    from prax.services import record_chain

    def move(data: bytes) -> bytes | None:
        updated = False
        new_lines = []
        for ln in data.decode("utf-8").strip().splitlines():
            if f'"trace_id": "{trace_id}"' in ln:
                entry = json.loads(ln)
                entry["session_id"] = new_session_id
                new_lines.append(json.dumps(entry))
                updated = True
            else:
                new_lines.append(ln)
        return ("\n".join(new_lines) + "\n").encode("utf-8") if updated else None

    try:
        d = _graphs_dir()
        for filepath in d.glob("graphs-*.jsonl"):
            record_chain.rewrite(filepath, move,
                                 note=f"move trace {trace_id} to session {new_session_id}")
    except Exception:
        logger.warning("Failed to update session for graph %s on disk", trace_id, exc_info=True)

    return True


def build_identity_context(name: str) -> str:
    """Build a context string for injection into agent system prompts.

    Tells the agent who it is, where it sits in the delegation chain,
    and what sibling agents are doing (for parallel awareness).
    """
    ctx = _current_trace.get()
    if not ctx:
        return f"You are '{name}'."

    parts = [
        f"You are '{name}' (trace: {ctx.trace_id[:8]}, depth: {ctx.depth})."
    ]

    if ctx.parent_id:
        with ctx.graph._lock:
            parent_node = ctx.graph._nodes.get(ctx.parent_id)
        if parent_node:
            parts.append(f"Delegated by: {parent_node.name}.")

    # Include sibling status for parallel awareness
    with ctx.graph._lock:
        siblings = [
            n
            for n in ctx.graph._nodes.values()
            if n.parent_id == ctx.parent_id and n.span_id != ctx.span_id
        ]
    if siblings:
        sibling_parts = ", ".join(
            f"{s.name} ({s.status})" for s in siblings
        )
        parts.append(f"Parallel peers: {sibling_parts}.")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# LangChain callback handler — real-time tool call nodes in the graph
# ---------------------------------------------------------------------------


def args_sha256(args) -> str:
    """Canonical hash of tool-call arguments — identical to the secrets proxy's
    wire record (prax-secrets-proxy ``wire_record._args_hash``): a JSON string is
    parsed first, then dumped with sorted keys and compact separators."""
    import hashlib

    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError:
            return hashlib.sha256(args.encode("utf-8")).hexdigest()
    return hashlib.sha256(
        json.dumps(args, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


# Keys a governance layer adds to a tool's schema and removes before the tool
# runs (governed_tool.py pops expected_observation): the model may send them and
# the tool never receives them, by design — not a change.
_GOVERNANCE_ONLY_ARGS = frozenset({"expected_observation"})


def _jsonable(value):
    """*value* as plain JSON data — a validated model as a dict, an enum as its
    value, a date as ISO text — i.e. the shape the model sent it in."""
    try:
        from pydantic_core import to_jsonable_python
        return to_jsonable_python(value, fallback=str)
    except Exception:  # noqa: BLE001 - comparison is best-effort
        return value


def _canonical(value) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:  # noqa: BLE001 - comparison is best-effort
        return repr(value)


@functools.cache
def _bool_adapter():
    from pydantic import TypeAdapter
    return TypeAdapter(bool)


def _as_bool(value) -> bool | None:
    """*value* as pydantic's lax bool validation reads it (``1``, ``"yes"``,
    ``"off"``, ``"t"``, …), or ``None`` when validation would reject it."""
    try:
        return _bool_adapter().validate_python(value)
    except Exception:  # noqa: BLE001 - not a bool pydantic accepts
        return None


def _as_decimal(value):
    """*value* as an exact ``Decimal`` — an int, a float (by its shortest repr,
    so ``5.0`` is 5), or text that spells a number (``"42"``, ``" 4_2 "``,
    ``"5.0"``, ``"1e3"``) — else ``None``. Never builds an int from text, so a
    model-sent ``"1e999999999"`` costs nothing (an int with that many digits
    would freeze the process), and integers past 2**53 keep every digit."""
    from decimal import Decimal, InvalidOperation

    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, int):
            return Decimal(value)
        if isinstance(value, float):
            number = Decimal(repr(value))
        elif isinstance(value, str):
            number = Decimal(value.strip().replace("_", ""))
        else:
            return None
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _loosely_equal(asked, ran) -> bool:
    """Whether *ran* is *asked* after validation's lossless conversions.

    Equal when the canonical JSON matches. Otherwise containers compare element
    by element (a dict ignores keys only *ran* has: nested schema defaults), and
    scalars compare by ``str()`` (``"5"`` → ``5``); when either side is a
    boolean, by pydantic's own bool parsing (``1``/``"yes"``/``"on"`` →
    ``True``, ``0``/``"no"``/``"off"`` → ``False``; anything it rejects
    differs); when either side is a number: as floats when validation made
    *ran* a float (that is the conversion validation itself performed, so
    ``"0.1"`` → ``0.1`` and a big int → its float are equal), otherwise exactly
    as decimals (``"9007199254740993"`` ≠ ``9007199254740992``, ``"5.0"`` →
    ``5``).
    """
    if _canonical(asked) == _canonical(ran):
        return True
    if isinstance(asked, dict) and isinstance(ran, dict):
        return all(k in ran and _loosely_equal(v, ran[k]) for k, v in asked.items())
    if isinstance(asked, list) and isinstance(ran, list):
        return len(asked) == len(ran) and all(
            _loosely_equal(a, r) for a, r in zip(asked, ran, strict=True))
    if isinstance(asked, (dict, list)) or isinstance(ran, (dict, list)):
        return False
    try:
        if str(asked) == str(ran):
            return True
        if isinstance(asked, bool) or isinstance(ran, bool):
            a, r = _as_bool(asked), _as_bool(ran)
            return a is not None and a == r
        if isinstance(asked, (int, float)) or isinstance(ran, (int, float)):
            if isinstance(ran, float):
                return float(asked) == ran
            a, r = _as_decimal(asked), _as_decimal(ran)
            return a is not None and r is not None and a == r
    except (TypeError, ValueError, OverflowError):
        pass
    return False


def arguments_differ(asked, ran) -> bool:
    """True when the inputs *ran* lack an argument the model asked for, or carry
    a different value for one.

    *asked* is the model's raw arguments; *ran* is one tool layer's inputs, as
    its ``on_tool_start`` reports them. A wrapper layer's inputs are what
    LangChain validated — schema defaults filled in, values coerced — so the two
    are compared loosely rather than by hash: governance-only keys are dropped
    from both, every key the model sent must be present with a value that is
    equal by ``_loosely_equal``, and keys only *ran* has (defaults) are ignored.

    Returns False when either side is not a dict of arguments (a plain string
    input): there is nothing to compare, and a guess is not a finding.
    """
    if isinstance(asked, str):
        try:
            asked = json.loads(asked) if asked.strip() else {}
        except ValueError:
            return False
    if not isinstance(asked, dict) or not isinstance(ran, dict):
        return False
    ran_plain = {k: _jsonable(v) for k, v in ran.items() if k not in _GOVERNANCE_ONLY_ARGS}
    for key, value in asked.items():
        if key in _GOVERNANCE_ONLY_ARGS:
            continue
        if key not in ran_plain or not _loosely_equal(_jsonable(value), ran_plain[key]):
            return True
    return False


def _copy_args(args):
    """A private copy of the model's arguments, so nothing downstream that
    mutates the call's dict can change what we compare against."""
    import copy

    try:
        return copy.deepcopy(args)
    except Exception:  # noqa: BLE001
        return dict(args) if isinstance(args, dict) else args


class GraphCallbackHandler(BaseCallbackHandler):
    """LangChain callback handler that creates child SpanNodes for tool calls.

    Attach to ``graph.invoke(config={"callbacks": [handler]})`` so that
    every tool invocation appears as a real-time node in the execution graph.

    Must inherit from ``BaseCallbackHandler`` so LangChain's
    ``CallbackManager`` recognises it during event dispatch.

    One logical tool call is one span. Governance and context binding wrap a
    tool in same-named StructuredTools that call ``inner.invoke()``, and every
    layer fires ``on_tool_start`` as a CHILD run of the layer above
    (``parent_run_id``). So a start whose parent is one of this handler's open
    runs, with the same tool name, is a wrapper layer and maps to that run's
    span; any other start opens its own span — including a second call to the
    same tool running in parallel, which deduplicating by name used to fold
    into the first. (There is no name-only fallback: a nested ``invoke`` only
    reaches this handler through LangChain's propagated child config, which
    always carries ``parent_run_id``; a thread hop that lost that config would
    lose the handler too, so no same-name nested start can arrive without it.)
    """

    # Tell LangChain to skip non-tool events.
    raise_error: bool = False
    # LLM events are needed for on_llm_end: the tool calls the model ASKED for,
    # whose arguments are matched to the tool spans by tool_call_id.
    ignore_llm: bool = False
    ignore_chain: bool = True
    # NOTE: ignore_agent MUST be False — LangChain's CallbackManager.on_tool_start
    # uses "ignore_agent" as the ignore condition, so setting it True silently
    # drops all tool events.  We don't implement on_agent_* methods, so leaving
    # it False has no side effects.
    ignore_agent: bool = False
    ignore_retriever: bool = True
    ignore_retry: bool = True
    ignore_chat_model: bool = True

    def __init__(
        self, *, parent_span_id: str, graph: ExecutionGraph, trace_id: str,
        live_agent_name: str | None = None,
        heartbeat: TraceHeartbeat | None = None,
    ):
        super().__init__()
        self._parent_span_id = parent_span_id
        self._graph = graph
        self._trace_id = trace_id
        self._heartbeat = heartbeat or get_trace_heartbeat(trace_id)
        # Every open tool run — each wrapper layer included — → its span, its
        # tool name, and the tool_call_id of the logical call it belongs to (the
        # outermost layer carries it; inner layers inherit it from their parent).
        self._active: dict[str, str] = {}  # run_id → span_id
        self._run_names: dict[str, str] = {}  # run_id → tool name
        self._run_calls: dict[str, str] = {}  # run_id → tool_call_id
        # Runs that opened a span: the outermost layer of each logical call.
        self._roots: set[str] = set()
        # Spans whose completion was already pushed to TeamWork live output.
        self._live_pushed: set[str] = set()
        self._ctx_tokens: dict[str, object] = {}  # run_id → ContextVar token
        self._live_agent = live_agent_name  # push live output to TeamWork
        self._tool_count = 0
        # tool_call_id → hash of the arguments the model asked for.
        self._requested: dict[str, str] = {}
        # tool_call_id → the model's raw arguments. IN MEMORY ONLY: compared
        # with each layer's inputs while the call runs, dropped when it ends,
        # never written to the trace (which holds hashes and a flag, no values).
        self._requested_args: dict[str, object] = {}
        # Parallel tool calls fire their callbacks from different threads.
        self._lock = threading.Lock()

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        """Remember every tool call the model asked for: its hash and arguments."""
        try:
            for gens in getattr(response, "generations", None) or []:
                for gen in gens:
                    msg = getattr(gen, "message", None)
                    for tc in getattr(msg, "tool_calls", None) or []:
                        tcid = tc.get("id")
                        if not tcid:
                            continue
                        args = tc.get("args") or {}
                        with self._lock:
                            self._requested[tcid] = args_sha256(args)
                            self._requested_args[tcid] = _copy_args(args)
        except Exception:  # noqa: BLE001 - tracing must never break a turn
            logger.debug("could not record requested tool-call hashes", exc_info=True)

    def _span_depth(self, span_id: str) -> int:
        """Return the graph depth of a span, best-effort."""
        depth = 0
        with self._graph._lock:
            node = self._graph._nodes.get(span_id)
            seen: set[str] = set()
            while node and node.parent_id and node.parent_id not in seen:
                seen.add(node.span_id)
                depth += 1
                node = self._graph._nodes.get(node.parent_id)
        return depth

    def on_tool_start(
        self, serialized: dict, input_str: str, *, run_id,
        parent_run_id=None, **kwargs,
    ) -> None:
        rid = str(run_id)
        parent = str(parent_run_id) if parent_run_id else ""
        tool_name = serialized.get("name") or "unknown_tool"
        with self._lock:
            # Dedup guard: if this run_id already has a node, skip.
            if rid in self._active:
                return
            # A wrapper layer of a call that is already open (see class doc).
            outer_span = (self._active.get(parent)
                          if parent and self._run_names.get(parent) == tool_name else None)
            if outer_span:
                self._active[rid] = outer_span
                self._run_names[rid] = tool_name
                if parent in self._run_calls:
                    self._run_calls[rid] = self._run_calls[parent]
        self._heartbeat.touch(tool_name, f"started tool {tool_name}")
        if outer_span:
            self._record_args(outer_span, rid, kwargs)
            return

        # Push live output to TeamWork so the user sees tool calls in real time.
        if self._live_agent:
            with self._lock:
                self._tool_count += 1
                count = self._tool_count
            try:
                from prax.services.teamwork_hooks import push_live_output
                line = f"[{count}] {tool_name}..."
                push_live_output(
                    self._live_agent, line + "\n",
                    status="running", append=count != 1,
                )
            except Exception:
                pass

        inputs = kwargs.get("inputs")
        span_id = uuid.uuid4().hex[:12]
        node = SpanNode(
            span_id=span_id,
            name=tool_name,
            parent_id=self._parent_span_id,
            trace_id=self._trace_id,
            spoke_or_category="tool",
            summary=args_preview_for_tool(
                tool_name, inputs if isinstance(inputs, dict) else input_str, 500),
        )
        self._graph.add_node(node)
        with self._lock:
            self._active[rid] = span_id
            self._run_names[rid] = tool_name
            self._roots.add(rid)
            tcid = kwargs.get("tool_call_id")
            if tcid:
                self._run_calls[rid] = tcid
        self._record_args(span_id, rid, kwargs)

        # For delegation tools (delegate_*), update the trace context so
        # that run_spoke's start_span() nests under this tool span instead
        # of the parent agent span.
        if tool_name.startswith("delegate_"):
            parent_ctx = _current_trace.get()
            parent_depth = (
                parent_ctx.depth
                if parent_ctx
                else self._span_depth(self._parent_span_id)
            )
            tool_ctx = TraceContext(
                trace_id=self._trace_id,
                span_id=span_id,
                parent_id=self._parent_span_id,
                origin=tool_name,
                depth=parent_depth + 1,
                graph=self._graph,
            )
            self._ctx_tokens[rid] = _current_trace.set(tool_ctx)
            register_pending_delegation_context(tool_name, tool_ctx, input_str)

    def _record_args(self, span_id: str, rid: str, kwargs: dict) -> None:
        """Hash what the model asked for, and check every layer ran with it.

        LangGraph fires on_tool_start once per layer of one call: first the
        outermost wrapper (carrying the tool_call_id and the model's raw
        arguments), then each inner invocation, innermost last, with the inputs
        LangChain validated for it. "What was asked" is the model's arguments,
        taken from its response by tool_call_id. Each layer's inputs are
        compared with them (``arguments_differ``) and any difference sets
        ``args_changed`` — so a wrapper that rewrote or dropped an argument
        shows up whichever layer it sits at. ``args_sha256`` keeps the LAST
        start's inputs: what the tool body itself received, post-validation.
        """
        try:
            with self._graph._lock:
                node = self._graph._nodes.get(span_id)
            if node is None:
                return
            inputs = kwargs.get("inputs")
            with self._lock:
                tcid = self._run_calls.get(rid)
                requested = (self._requested.pop(tcid, None)
                             if tcid and not node.requested_args_sha256 else None)
                asked = self._requested_args.get(tcid) if tcid else None
            if inputs is not None:
                node.args_sha256 = args_sha256(inputs)
            if requested:
                node.requested_args_sha256 = requested
            if asked is not None and inputs is not None and arguments_differ(asked, inputs):
                node.args_changed = True
        except Exception:  # noqa: BLE001 - tracing must never break a turn
            logger.debug("could not record tool-call argument hashes", exc_info=True)

    def _end_run(self, rid: str) -> tuple[str | None, str, bool]:
        """Forget *rid*: (its span, its tool name, whether to push live output).

        Live output is pushed once per span, at the first layer to end (the
        innermost, or the outermost when governance refused the call and no
        inner layer ran). When the outermost layer ends the logical call is
        over, and the model's raw arguments are dropped.
        """
        with self._lock:
            span_id = self._active.pop(rid, None)
            tool_name = self._run_names.pop(rid, "")
            tcid = self._run_calls.pop(rid, None)
            push = bool(span_id) and span_id not in self._live_pushed
            if push:
                self._live_pushed.add(span_id)
            if rid in self._roots:
                self._roots.discard(rid)
                self._live_pushed.discard(span_id)
                if tcid:
                    self._requested_args.pop(tcid, None)
                    self._requested.pop(tcid, None)
        return span_id, tool_name, push

    def on_tool_end(self, output, *, run_id, **kwargs) -> None:
        rid = str(run_id)
        # Restore trace context if we modified it for a delegation tool.
        token = self._ctx_tokens.pop(rid, None)
        if token:
            _current_trace.reset(token)
        span_id, tool_name, push = self._end_run(rid)
        if span_id:
            discard_pending_delegation_context(span_id)
            # A ToolCall invoke ends with a ToolMessage, whose str() is a
            # pydantic repr, so trace reports read "content='...' name='...'".
            # A credential tool's output (a password) is withheld.
            text = preview_for_tool(tool_name, output, 2000)
            # A tool that handles its own error (handle_tool_error, or one that
            # returns an error ToolMessage) ends HERE with status="error";
            # on_tool_error never fires for it.
            failed = isinstance(output, ToolMessage) and output.status == "error"
            self._graph.complete_node(
                span_id, status="failed" if failed else "completed", summary=text,
            )
            verb = "failed" if failed else "completed"
            self._heartbeat.touch(tool_name or "tool", f"{verb} tool {tool_name or span_id}")

            # Push completion to live output + activity log
            if self._live_agent and tool_name and push:
                try:
                    from prax.services.teamwork_hooks import log_activity, push_live_output
                    result_preview = text[:200] or "(no output)"
                    mark = "✘" if failed else "✔"
                    push_live_output(
                        self._live_agent,
                        f"    {mark} {tool_name}: {result_preview}\n",
                        status="running",
                    )
                    log_activity(
                        self._live_agent, "tool_use",
                        f"{tool_name}: {result_preview}",
                    )
                except Exception:
                    pass

    def on_tool_error(self, error, *, run_id, **kwargs) -> None:
        rid = str(run_id)
        token = self._ctx_tokens.pop(rid, None)
        if token:
            _current_trace.reset(token)
        span_id, tool_name, _push = self._end_run(rid)
        if span_id:
            discard_pending_delegation_context(span_id)
            text = error_preview_for_tool(tool_name, error, 2000)
            self._graph.complete_node(span_id, status="failed", summary=text)
            self._heartbeat.touch("tool_error", f"tool failed: {text[:160]}")
