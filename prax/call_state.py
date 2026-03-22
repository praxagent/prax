"""Encapsulated call-state store with convenience helpers."""
from __future__ import annotations

from prax.helpers_functions import create_convo_state


class CallStateManager(dict):
    """A dict subclass that auto-creates call states on first access.

    Drop-in replacement for the bare ``convo_states = {}`` dict.
    """

    def ensure(self, call_sid: str, from_num: str) -> tuple[str, bool]:
        """Return (language, is_new) — create state if missing."""
        if call_sid not in self:
            self[call_sid] = create_convo_state()
            self[call_sid]["from_num"] = from_num
            self[call_sid]["language"] = "en"
            return "en", True
        return self[call_sid].get("language", "en"), False
