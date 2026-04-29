"""Tests for restoring request context around LangGraph tool execution."""
from __future__ import annotations

from langchain_core.tools import tool


def test_bind_tool_user_context_restores_user_id():
    from prax.agent.user_context import (
        bind_tool_user_context,
        current_user_id,
    )

    @tool
    def whoami() -> str:
        """Return the current Prax user id."""
        return current_user_id.get() or "none"

    previous_user_id = current_user_id.get()
    token = current_user_id.set("user-123")
    try:
        bound = bind_tool_user_context(whoami)
    finally:
        current_user_id.reset(token)

    assert current_user_id.get() == previous_user_id
    assert bound.invoke({}) == "user-123"
    assert current_user_id.get() == previous_user_id


def test_governed_tool_restores_bound_user_context():
    from prax.agent.governed_tool import drain_audit_log, init_turn_budget, wrap_with_governance
    from prax.agent.user_context import current_user_id

    drain_audit_log()
    init_turn_budget(0)

    @tool
    def governed_whoami() -> str:
        """Return the current Prax user id."""
        return current_user_id.get() or "none"

    previous_user_id = current_user_id.get()
    token = current_user_id.set("scheduled-user")
    try:
        wrapped = wrap_with_governance(governed_whoami)
    finally:
        current_user_id.reset(token)

    assert current_user_id.get() == previous_user_id
    assert wrapped.invoke({}) == "scheduled-user"
    assert current_user_id.get() == previous_user_id
    drain_audit_log()
