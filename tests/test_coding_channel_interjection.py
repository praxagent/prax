"""A human message in a legacy coding-agent channel falls through to Prax.

The coding-agent CLIs and their bridge helpers were removed from the sandbox
(2026-07-20), but TeamWork projects still have the #claude-code/#codex/
#opencode channels.  The interjection handler imported the vanished helpers
unguarded, so every message posted there raised ImportError and the user got
a generic error reply instead of an answer.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import prax.blueprints.teamwork_routes as tr


def test_a_message_in_a_legacy_coding_channel_is_left_to_prax():
    tw = MagicMock()
    assert tr._handle_claude_code_interjection(tw, "hello?", "chan-claude-code") is False
    tw.send_message.assert_not_called()


def test_it_falls_through_even_if_the_tools_module_is_gone_entirely(monkeypatch):
    # Pins the contract independent of what claude_code_tools still exports:
    # no bridge helpers means no session to relay to, never an error.
    monkeypatch.setitem(sys.modules, "prax.agent.claude_code_tools",
                        types.ModuleType("prax.agent.claude_code_tools"))
    tw = MagicMock()
    assert tr._handle_claude_code_interjection(tw, "hello?", "chan-codex") is False
    tw.send_message.assert_not_called()
