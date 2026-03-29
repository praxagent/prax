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
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

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


class ExecutionGraph:
    """Thread-safe tree of all agent invocations in a delegation chain."""

    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self._nodes: dict[str, SpanNode] = {}
        self._lock = threading.Lock()

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
    ) -> None:
        with self._lock:
            node = self._nodes.get(span_id)
            if node:
                node.status = status
                node.finished_at = datetime.now(UTC)
                node.summary = summary[:200]
                if tool_calls:
                    node.tool_calls = tool_calls

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

# Stores the trace_id of the most recent root span in the current context.
# Read by callers (e.g. teamwork_routes) after an agent run to attach
# trace_id to the response message.
last_root_trace_id: contextvars.ContextVar[str | None] = (
    contextvars.ContextVar("last_root_trace_id", default=None)
)


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
    ) -> None:
        if self._ended:
            return
        self._ended = True
        self.ctx.graph.complete_node(
            self.span_id,
            status=status,
            summary=summary,
            tool_calls=tool_calls,
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


def start_span(name: str, spoke_or_category: str) -> SpanHandle:
    """Create a span -- child of current trace, or new trace if none exists.

    Returns a :class:`SpanHandle`.  Call ``handle.end(...)`` to close,
    or use as a context manager.

    If OpenTelemetry is initialized, a corresponding OTel span is also created
    and linked to the Prax execution graph for distributed trace export.
    """
    parent = _current_trace.get()

    if parent:
        trace_id = parent.trace_id
        parent_id = parent.span_id
        graph = parent.graph
        depth = parent.depth + 1
    else:
        trace_id = uuid.uuid4().hex[:16]
        parent_id = None
        graph = ExecutionGraph(trace_id)
        depth = 0
        # Record the root trace_id so callers can attach it to responses.
        last_root_trace_id.set(trace_id)

    span_id = uuid.uuid4().hex[:12]

    node = SpanNode(
        span_id=span_id,
        name=name,
        parent_id=parent_id,
        trace_id=trace_id,
        spoke_or_category=spoke_or_category,
    )
    graph.add_node(node)

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


def get_graph_summary() -> str:
    """Return a human-readable summary of the current execution graph."""
    ctx = _current_trace.get()
    if not ctx:
        return "No active trace."
    return ctx.graph.get_summary()


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
