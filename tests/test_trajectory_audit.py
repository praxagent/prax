"""Key-free tests for the post-hoc lethal-trifecta trajectory auditor."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from prax.agent.trajectory_audit import (
    audit_trajectory,
    audit_trajectory_messages,
    tool_names_in_order,
)

# delegate_research = untrusted source; delegate_knowledge = private data;
# delegate_scheduler = pure external sink (scheduler is a sink, not a source).
_UNTRUSTED, _PRIVATE, _SINK = "delegate_research", "delegate_knowledge", "delegate_scheduler"


def test_flags_completed_trifecta():
    out = audit_trajectory([_UNTRUSTED, _PRIVATE, _SINK])
    assert out is not None
    assert out["sink"] == _SINK
    assert out["untrusted_source"] == _UNTRUSTED
    assert out["private_data"] == _PRIVATE


def test_no_flag_when_sink_fires_before_the_legs():
    # data must be ingested/read BEFORE it can leak — a leading sink is safe
    assert audit_trajectory([_SINK, _UNTRUSTED, _PRIVATE]) is None


def test_no_flag_missing_private_leg():
    assert audit_trajectory([_UNTRUSTED, _SINK]) is None


def test_no_flag_missing_untrusted_leg():
    assert audit_trajectory([_PRIVATE, _SINK]) is None


def test_empty_and_none_safe():
    assert audit_trajectory([]) is None
    assert audit_trajectory(["", None]) is None  # type: ignore[list-item]


def test_extracts_tool_calls_in_order_from_messages():
    msgs = [
        HumanMessage(content="hi"),
        AIMessage(content="", tool_calls=[{"name": _UNTRUSTED, "args": {}, "id": "1"}]),
        AIMessage(content="", tool_calls=[{"name": _PRIVATE, "args": {}, "id": "2"}]),
        AIMessage(content="", tool_calls=[{"name": _SINK, "args": {}, "id": "3"}]),
    ]
    assert tool_names_in_order(msgs) == [_UNTRUSTED, _PRIVATE, _SINK]
    assert audit_trajectory_messages(msgs) is not None
