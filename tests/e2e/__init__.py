"""End-to-end workflow tests with mocked LLM responses.

These tests exercise the full agent orchestration loop — from user message
through tool calls and back to final response — with a ScriptedLLM that
plays back predetermined responses.  External services (web search, sandbox,
TeamWork, etc.) are mocked at the service boundary so that tool governance,
audit logging, and checkpoint management all run for real.

Directory layout::

    tests/e2e/
        conftest.py           # ScriptedLLM, fixtures, helpers
        test_chat.py          # Simple conversations (no tools)
        test_search.py        # Web search & URL fetch workflows
        test_notes.py         # Note creation & update workflows
        test_sandbox.py       # Sandbox code-execution workflows
        test_delegation.py    # Sub-agent & spoke delegation
        test_errors.py        # Error handling & recovery
"""
