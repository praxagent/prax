"""Per-request user context using contextvars (thread-safe, async-safe)."""
from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
