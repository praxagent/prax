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
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

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


class ExecutionGraph:
    """Thread-safe tree of all agent invocations in a delegation chain."""

    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self._nodes: dict[str, SpanNode] = {}
        self._lock = threading.Lock()
        self.trigger: str = ""  # User message or cron/event that started this trace
        self.session_id: str = ""  # Groups related traces into a session

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
                nodes.append({
                    "span_id": n.span_id,
                    "name": n.name,
                    "parent_id": n.parent_id,
                    "status": n.status,
                    "spoke_or_category": n.spoke_or_category,
                    "started_at": n.started_at.isoformat(),
                    "finished_at": n.finished_at.isoformat() if n.finished_at else None,
                    "tool_calls": n.tool_calls,
                    "summary": n.summary,
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
            result: dict = {
                "trace_id": self.trace_id,
                "status": overall_status,
                "node_count": len(nodes),
                "nodes": nodes,
            }
            if self.trigger:
                result["trigger"] = self.trigger
            if self.session_id:
                result["session_id"] = self.session_id
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
_GRAPH_RETENTION_DAYS = 7  # keep graph files for this many days
_graphs_loaded = False


# ---------------------------------------------------------------------------
# Persistence — save completed graphs to disk, load on startup
# ---------------------------------------------------------------------------


def _graphs_dir() -> Path:
    """Return the directory for persisted graph JSONL files.

    Stored INSIDE workspace_dir (not its parent) so Docker volume mounts
    that map workspace_dir to a host path also persist graphs across
    container restarts.
    """
    try:
        from prax.settings import settings
        base = Path(settings.workspace_dir).resolve()
    except Exception:
        base = Path(".")
    d = base / ".prax" / "graphs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _persist_graph(graph: ExecutionGraph) -> None:
    """Append a completed graph as one JSON line to today's file."""
    try:
        d = _graphs_dir()
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        filepath = d / f"graphs-{today}.jsonl"
        line = json.dumps(graph.to_dict(), default=str)
        with open(filepath, "a") as f:
            f.write(line + "\n")
    except Exception:
        logger.warning("Failed to persist execution graph %s", graph.trace_id, exc_info=True)


def _rotate_graph_files() -> None:
    """Delete graph files older than _GRAPH_RETENTION_DAYS."""
    try:
        d = _graphs_dir()
        from datetime import timedelta
        cutoff_date = datetime.now(UTC) - timedelta(days=_GRAPH_RETENTION_DAYS)
        cutoff_str = cutoff_date.strftime("%Y-%m-%d")
        for f in d.glob("graphs-*.jsonl"):
            # Extract date from filename: graphs-YYYY-MM-DD.jsonl
            date_part = f.stem.replace("graphs-", "")
            if date_part < cutoff_str:
                f.unlink(missing_ok=True)
                logger.debug("Rotated old graph file: %s", f.name)
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
                tier_choices = [
                    c for c in all_choices if c.get("span_id") == self.span_id
                ]
                # Put back choices for other spans
                if all_choices and len(tier_choices) < len(all_choices):
                    from prax.agent.llm_factory import _tier_choice_log, _tier_lock
                    others = [c for c in all_choices if c.get("span_id") != self.span_id]
                    with _tier_lock:
                        _tier_choice_log.extend(others)
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

    # Also remove from persisted files so it doesn't reload on restart.
    try:
        d = _graphs_dir()
        for filepath in d.glob("graphs-*.jsonl"):
            lines = filepath.read_text().strip().splitlines()
            kept = [ln for ln in lines if f'"trace_id": "{trace_id}"' not in ln]
            if len(kept) < len(lines):
                filepath.write_text("\n".join(kept) + "\n" if kept else "")
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

    # Update on disk — rewrite the line with the new session_id.
    try:
        d = _graphs_dir()
        for filepath in d.glob("graphs-*.jsonl"):
            lines = filepath.read_text().strip().splitlines()
            updated = False
            new_lines = []
            for ln in lines:
                if f'"trace_id": "{trace_id}"' in ln:
                    import json
                    data = json.loads(ln)
                    data["session_id"] = new_session_id
                    new_lines.append(json.dumps(data))
                    updated = True
                else:
                    new_lines.append(ln)
            if updated:
                filepath.write_text("\n".join(new_lines) + "\n")
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


class GraphCallbackHandler(BaseCallbackHandler):
    """LangChain callback handler that creates child SpanNodes for tool calls.

    Attach to ``graph.invoke(config={"callbacks": [handler]})`` so that
    every tool invocation appears as a real-time node in the execution graph.

    Must inherit from ``BaseCallbackHandler`` so LangChain's
    ``CallbackManager`` recognises it during event dispatch.
    """

    # Tell LangChain to skip non-tool events.
    raise_error: bool = False
    ignore_llm: bool = True
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
        self._active: dict[str, str] = {}  # run_id → span_id
        self._ctx_tokens: dict[str, object] = {}  # run_id → ContextVar token
        # Track active tool names for deduplication (LangGraph fires
        # on_tool_start at both ToolNode and individual invocation levels).
        self._active_names: dict[str, str] = {}  # tool_name → span_id
        self._live_agent = live_agent_name  # push live output to TeamWork
        self._tool_count = 0

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
        self, serialized: dict, input_str: str, *, run_id, **kwargs
    ) -> None:
        rid = str(run_id)
        # Dedup guard: if this run_id already has a node, skip.
        if rid in self._active:
            return
        tool_name = serialized.get("name") or "unknown_tool"
        self._heartbeat.touch(tool_name, f"started tool {tool_name}")

        # Dedup: LangGraph fires on_tool_start at multiple levels for the
        # same tool call (ToolNode processor + individual invocation).
        # If we already have an active span with the same name, map this
        # run_id to the existing span instead of creating a duplicate.
        if tool_name in self._active_names:
            self._active[rid] = self._active_names[tool_name]
            return

        # Push live output to TeamWork so the user sees tool calls in real time.
        if self._live_agent:
            self._tool_count += 1
            try:
                from prax.services.teamwork_hooks import push_live_output
                is_first = self._tool_count == 1
                line = f"[{self._tool_count}] {tool_name}..."
                push_live_output(
                    self._live_agent, line + "\n",
                    status="running", append=not is_first,
                )
            except Exception:
                pass

        span_id = uuid.uuid4().hex[:12]
        node = SpanNode(
            span_id=span_id,
            name=tool_name,
            parent_id=self._parent_span_id,
            trace_id=self._trace_id,
            spoke_or_category="tool",
            summary=str(input_str)[:500],
        )
        self._graph.add_node(node)
        self._active[rid] = span_id
        self._active_names[tool_name] = span_id

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

    def on_tool_end(self, output, *, run_id, **kwargs) -> None:
        rid = str(run_id)
        # Restore trace context if we modified it for a delegation tool.
        token = self._ctx_tokens.pop(rid, None)
        if token:
            _current_trace.reset(token)
        span_id = self._active.pop(rid, None)
        if span_id:
            discard_pending_delegation_context(span_id)
            # Remove from active names dedup tracker.
            tool_name = ""
            for k, v in list(self._active_names.items()):
                if v == span_id:
                    tool_name = k
            self._active_names = {
                k: v for k, v in self._active_names.items() if v != span_id
            }
            preview = str(output)[:2000] if output else ""
            self._graph.complete_node(span_id, status="completed", summary=preview)
            self._heartbeat.touch(tool_name or "tool", f"completed tool {tool_name or span_id}")

            # Push completion to live output + activity log
            if self._live_agent and tool_name:
                try:
                    from prax.services.teamwork_hooks import log_activity, push_live_output
                    result_preview = str(output)[:200] if output else "(no output)"
                    push_live_output(
                        self._live_agent,
                        f"    \u2714 {tool_name}: {result_preview}\n",
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
        span_id = self._active.pop(rid, None)
        if span_id:
            discard_pending_delegation_context(span_id)
            self._active_names = {
                k: v for k, v in self._active_names.items() if v != span_id
            }
            self._graph.complete_node(
                span_id, status="failed", summary=str(error)[:2000]
            )
            self._heartbeat.touch("tool_error", f"tool failed: {str(error)[:160]}")
