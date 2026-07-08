"""The single construction seam for the LangChain agent loop.

Every ReAct loop in Prax — the orchestrator, spokes, and one-shot sub-agents —
is built HERE and nowhere else.  ``scripts/check_layers.py`` (rule 4) enforces
that no other module imports ``langchain.agents`` or ``langgraph``, so anything
that changes how the loop is built — enabling middleware, adopting a new
LangChain major, or swapping in an owned loop — is a change to one module, not
a codebase sweep.

Usage (drop-in for the old ``create_agent`` / ``create_react_agent`` calls)::

    from prax.agent.agent_loop import build_agent_loop

    graph = build_agent_loop(llm, tools)                       # spokes
    graph = build_agent_loop(llm, tools, checkpointer=saver)   # orchestrator

The full lang-stack usage map (what we use from each package, the middleware
architecture, version policy, and the durable-resume decision gate) lives in
``docs/architecture/lang-stack.md``.
"""
from __future__ import annotations

from typing import Any

from langchain.agents import create_agent

# Re-exported through the seam so callers (e.g. the orchestrator's retry loop)
# can catch the loop's recursion-limit error without importing langgraph
# directly — keeping the loop-construction seam the only langgraph importer.
from langgraph.errors import GraphRecursionError

from prax.agent.loop_middleware import default_middleware

__all__ = ["build_agent_loop", "GraphRecursionError"]


def build_agent_loop(
    llm: Any,
    tools: Any,
    *,
    checkpointer: Any = None,
    extra_middleware: list[Any] | None = None,
) -> Any:
    """Build the ReAct agent loop for *llm* + *tools*.

    Flag-gated: with ``AGENT_MIDDLEWARE_ENABLED`` off (the default) the
    *default* stack is empty and the compiled graph is identical to a bare
    ``create_agent(llm, tools)`` call — keyless CI stays green.

    ``extra_middleware`` is a **caller-owned opt-in and is deliberately NOT
    flag-gated**: a call site passing middleware explicitly is code-level
    intent, not deployment config.  It is appended after the default stack
    (defaults run first).  Middleware classes must have distinct class names —
    ``create_agent`` rejects duplicate-named middleware.
    """
    middleware = list(default_middleware())
    if extra_middleware:
        middleware.extend(extra_middleware)

    kwargs: dict[str, Any] = {}
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    if middleware:
        kwargs["middleware"] = middleware
    return create_agent(llm, tools, **kwargs)
