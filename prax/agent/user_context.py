"""Per-request user context using contextvars (thread-safe, async-safe)."""
from __future__ import annotations

from contextvars import ContextVar

# Holds the current user's phone number (e.g., "+15551234567") for the duration of a request.
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)

# Holds the TeamWork channel ID that originated the current request.
# When set, agent hooks should post responses to this channel instead of #general.
current_channel_id: ContextVar[str | None] = ContextVar("current_channel_id", default=None)
