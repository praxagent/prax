"""Per-request user context using contextvars (thread-safe, async-safe)."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from langchain_core.tools import BaseTool

    from prax.services.identity_service import User

# Holds the current user's UUID (or legacy phone number) for the duration of a request.
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)

# The resolved User object for the current request.
current_user: ContextVar[User | None] = ContextVar("current_user", default=None)

# Holds the TeamWork channel ID that originated the current request.
# When set, agent hooks should post responses to this channel instead of #general.
current_channel_id: ContextVar[str | None] = ContextVar("current_channel_id", default=None)

# Holds the TeamWork channel name (e.g., "general", "research", or "DM") for
# the current request.  Used to inject channel context into the system prompt.
current_channel_name: ContextVar[str] = ContextVar("current_channel_name", default="")

# The raw user message for the current turn — used by the smart confirmation
# gate to detect when the user explicitly requested an action.
current_user_message: ContextVar[str] = ContextVar("current_user_message", default="")

# The currently executing component (e.g., "orchestrator", "research", "browser")
# — used by earned trust to look up per-component reliability.
current_component: ContextVar[str] = ContextVar("current_component", default="orchestrator")

# The TeamWork active_view for this request (e.g., "terminal", "browser", "chat").
# When "terminal", tools should execute in the shared terminal the user is watching.
current_active_view: ContextVar[str] = ContextVar("current_active_view", default="")


@dataclass(frozen=True)
class UserContextSnapshot:
    """Copy of request context that can be restored around tool execution."""

    user_id: str | None
    user: User | None
    channel_id: str | None
    channel_name: str
    user_message: str
    component: str
    active_view: str


def capture_user_context() -> UserContextSnapshot:
    """Capture the current request context for later tool execution."""
    return UserContextSnapshot(
        user_id=current_user_id.get(),
        user=current_user.get(),
        channel_id=current_channel_id.get(),
        channel_name=current_channel_name.get(),
        user_message=current_user_message.get(),
        component=current_component.get(),
        active_view=current_active_view.get(),
    )


@contextmanager
def use_user_context(snapshot: UserContextSnapshot) -> Iterator[None]:
    """Temporarily restore a captured request context."""
    tokens = [
        (current_user_id, current_user_id.set(snapshot.user_id)),
        (current_user, current_user.set(snapshot.user)),
        (current_channel_id, current_channel_id.set(snapshot.channel_id)),
        (current_channel_name, current_channel_name.set(snapshot.channel_name)),
        (current_user_message, current_user_message.set(snapshot.user_message)),
        (current_component, current_component.set(snapshot.component)),
        (current_active_view, current_active_view.set(snapshot.active_view)),
    ]
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def bind_tool_user_context(
    tool: BaseTool,
    snapshot: UserContextSnapshot | None = None,
) -> BaseTool:
    """Return a tool wrapper that restores request context before invoking.

    LangGraph may execute a tool body in a fresh ``contextvars`` context.  This
    wrapper captures the active request at graph-construction time and restores
    it at the last possible moment before the real tool runs.
    """
    from langchain_core.tools import StructuredTool

    bound_snapshot = snapshot or capture_user_context()

    def _context_bound_run(**kwargs):
        with use_user_context(bound_snapshot):
            return tool.invoke(kwargs if kwargs else {})

    return StructuredTool.from_function(
        func=_context_bound_run,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        return_direct=getattr(tool, "return_direct", False),
    )


def bind_tools_user_context(
    tools: list[BaseTool],
    snapshot: UserContextSnapshot | None = None,
) -> list[BaseTool]:
    """Bind every tool in a list to the same captured request context."""
    bound_snapshot = snapshot or capture_user_context()
    return [bind_tool_user_context(tool, bound_snapshot) for tool in tools]
