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
    tier_choices: list[dict] = field(default_factory=list)


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
        tier_choices: list[dict] | None = None,
    ) -> None:
        with self._lock:
            node = self._nodes.get(span_id)
            if node:
                node.status = status
                node.finished_at = datetime.now(UTC)
                node.summary = summary[:200]
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

# Preserves the execution graph from the most recent completed root span.
# Without this, the graph is lost when root_span.end() resets the contextvar.
# Read by integration tests and diagnostics after agent.run() returns.
_last_completed_graph: ExecutionGraph | None = None


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
                    from prax.agent.llm_factory import _tier_lock, _tier_choice_log
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


def start_span(name: str, spoke_or_category: str) -> SpanHandle:
    """Create a span -- child of current trace, or new trace if none exists.

    Returns a :class:`SpanHandle`.  Call ``handle.end(...)`` to close,
    or use as a context manager.

    If OpenTelemetry is initialized, a corresponding OTel span is also created
    and linked to the Prax execution graph for distributed trace export.

    Raises :class:`DelegationDepthExceeded` if the delegation chain exceeds
    the configured ``AGENT_MAX_DELEGATION_DEPTH``.
    """
    parent = _current_trace.get()

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
