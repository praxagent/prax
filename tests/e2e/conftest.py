"""E2E test infrastructure — ScriptedLLM, fixtures, and helpers.

The core idea: replace the real LLM with a ``ScriptedLLM`` that plays back
a deterministic sequence of AIMessages (some with tool_calls, some with
plain text).  External services are mocked at the service layer so the full
orchestration pipeline runs for real: governance, tool execution, checkpoint
management, claim auditing, and trace logging.
"""
from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from prax.agent.user_context import current_user_id

# ---------------------------------------------------------------------------
# ScriptedLLM — the heart of e2e testing
# ---------------------------------------------------------------------------


class ScriptedLLM(BaseChatModel):
    """Chat model that plays back pre-scripted AIMessage responses.

    Each call to ``_generate`` returns the next response in the script.
    After the script is exhausted, returns a sentinel message.

    ``counter`` is a single-element list so mutations bypass pydantic's
    frozen-model protection.  ``call_messages`` records what the agent
    sent to the LLM on each invocation (useful for assertions).
    """

    responses: list = Field(default_factory=list)
    counter: list = Field(default_factory=lambda: [0])
    call_messages: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-test"

    def _generate(
        self,
        messages: list,
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        # Record what the agent sent us (message count, last message snippet).
        self.call_messages.append(len(messages))

        idx = self.counter[0]
        if idx < len(self.responses):
            msg = self.responses[idx]
        else:
            msg = AIMessage(content="[ScriptedLLM: script exhausted]")
        self.counter[0] = idx + 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedLLM":
        """Return self — tool schemas are irrelevant for scripted responses."""
        return self

    @property
    def call_count(self) -> int:
        return self.counter[0]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def tc(name: str, args: dict, call_id: str | None = None) -> dict:
    """Build a tool_calls entry for an AIMessage.

    Usage::

        AIMessage(content="", tool_calls=[tc("background_search_tool", {"query": "test"})])
    """
    return {
        "name": name,
        "args": args,
        "id": call_id or f"call_{name}",
        "type": "tool_call",
    }


def ai(content: str) -> AIMessage:
    """Shorthand for a text-only AIMessage (final response)."""
    return AIMessage(content=content)


def ai_tools(*tool_calls: dict) -> AIMessage:
    """Shorthand for an AIMessage that triggers tool execution."""
    return AIMessage(content="", tool_calls=list(tool_calls))


# ---------------------------------------------------------------------------
# Core e2e fixture
# ---------------------------------------------------------------------------

# Default mock targets that every e2e test needs.
_CORE_MOCKS = [
    # LLM construction — replaced per-test
    # (handled separately because we need the ScriptedLLM instance)

    # TeamWork hooks — silence HTTP calls
    "prax.services.teamwork_hooks.set_role_status",
    "prax.services.teamwork_hooks.post_to_channel",
    "prax.services.teamwork_hooks.reset_all_idle",
]


@pytest.fixture
def run_e2e(tmp_path):
    """Factory fixture: run a full agent turn with scripted LLM responses.

    Returns ``(response_text, llm)`` where ``llm`` is the ScriptedLLM
    instance so tests can assert on ``llm.call_count`` etc.

    Usage::

        def test_greeting(run_e2e):
            response, llm = run_e2e("Hello!", [ai("Hi there!")])
            assert "Hi" in response
            assert llm.call_count == 1

        def test_search(run_e2e):
            response, llm = run_e2e(
                "What is quantum computing?",
                [
                    ai_tools(tc("background_search_tool", {"query": "quantum computing"})),
                    ai("Quantum computing uses qubits..."),
                ],
                mocks={
                    "prax.helpers_functions.background_search":
                        make_async_return("Quantum computing leverages quantum mechanics..."),
                },
            )
            assert "qubits" in response
    """
    def _run(
        user_msg: str,
        responses: list[AIMessage],
        *,
        mocks: dict[str, Any] | None = None,
        conversation: list | None = None,
        workspace_context: str = "",
    ) -> tuple[str, ScriptedLLM]:
        llm = ScriptedLLM(responses=responses)

        # Mock plugin loader to avoid loading real plugins (speed + isolation).
        mock_loader = MagicMock()
        mock_loader.get_tools.return_value = []
        mock_loader.version = 0
        mock_loader.load_all.return_value = None

        with ExitStack() as stack:
            # --- Core mocks ---
            stack.enter_context(
                patch("prax.agent.orchestrator.build_llm", return_value=llm)
            )
            stack.enter_context(
                patch("prax.plugins.loader.get_plugin_loader", return_value=mock_loader)
            )
            stack.enter_context(
                patch("prax.agent.tool_registry.get_plugin_loader", return_value=mock_loader)
            )

            for target in _CORE_MOCKS:
                stack.enter_context(patch(target))

            # --- Per-test mocks ---
            mock_objects: dict[str, MagicMock] = {}
            if mocks:
                for target, value in mocks.items():
                    if callable(value) and not isinstance(value, MagicMock):
                        m = stack.enter_context(patch(target, side_effect=value))
                    else:
                        m = stack.enter_context(patch(target, return_value=value))
                    mock_objects[target] = m

            # --- Create agent inside mock context ---
            from prax.agent.orchestrator import ConversationAgent

            agent = ConversationAgent()

            # Set user context.
            current_user_id.set("+10000000000")

            response = agent.run(
                conversation=conversation or [],
                user_input=user_msg,
                workspace_context=workspace_context,
            )

        return response, llm

    return _run


# ---------------------------------------------------------------------------
# Async mock helper
# ---------------------------------------------------------------------------

def make_async_return(value: Any):
    """Create an async function that returns *value*.

    Use for mocking async functions like ``background_search``::

        mocks={"prax.helpers_functions.background_search": make_async_return("results")}
    """
    async def _fake(*args, **kwargs):
        return value
    return _fake
