"""E2E tests for error handling and edge cases."""
from tests.e2e.conftest import ai, ai_tools, make_async_return, tc


def test_search_returns_empty(run_e2e):
    """Agent handles an empty search result gracefully."""
    response, llm = run_e2e(
        "Search for xyzzy12345nonexistent",
        [
            ai_tools(tc("background_search_tool", {"query": "xyzzy12345nonexistent"})),
            ai("I searched but couldn't find any relevant results for that query. Could you rephrase or provide more context?"),
        ],
        mocks={
            "prax.helpers_functions.background_search": make_async_return(""),
        },
    )
    assert "couldn't find" in response.lower() or "no" in response.lower()
    assert llm.call_count == 2


def test_no_active_sandbox(run_e2e):
    """Agent handles 'no active session' when trying to message sandbox."""
    response, llm = run_e2e(
        "Send a message to my sandbox",
        [
            ai_tools(tc("sandbox_message", {"message": "hello"})),
            ai("It looks like you don't have an active sandbox session. Would you like me to start one?"),
        ],
        mocks={
            "prax.services.sandbox_service.send_message": {
                "error": "No active sandbox session.",
            },
        },
    )
    assert "sandbox" in response.lower()
    assert llm.call_count == 2


def test_note_creation_error(run_e2e):
    """Agent handles a note service failure."""

    def _fail_publish(*args, **kwargs):
        raise ConnectionError("Hugo server unreachable")

    response, llm = run_e2e(
        "Create a note about testing",
        [
            ai_tools(tc("note_create", {
                "title": "Testing Notes",
                "content": "# Testing\n\nSome content about testing.",
                "tags": "testing",
            })),
            ai("I encountered an error creating the note — the publishing server appears to be down. I'll try again later."),
        ],
        mocks={
            "prax.services.note_service.save_and_publish": _fail_publish,
        },
    )
    assert "error" in response.lower() or "down" in response.lower()
    assert llm.call_count == 2


def test_url_fetch_failure(run_e2e):
    """Agent handles a URL fetch timeout."""
    import requests

    def _timeout(*args, **kwargs):
        raise requests.ConnectionError("Connection timed out")

    response, llm = run_e2e(
        "Fetch https://example.com/unreachable",
        [
            ai_tools(tc("fetch_url_content", {"url": "https://example.com/unreachable"})),
            ai("I wasn't able to fetch that URL — the connection timed out. The site might be down."),
        ],
        mocks={
            "requests.get": _timeout,
        },
    )
    assert "timed out" in response.lower() or "down" in response.lower()
    assert llm.call_count == 2


def test_delegation_failure(run_e2e):
    """Agent handles a sub-agent failure gracefully."""
    response, llm = run_e2e(
        "Research something obscure",
        [
            ai_tools(tc("delegate_task", {
                "task": "Research obscure topic",
                "category": "research",
            })),
            ai("The research sub-agent encountered an error. Let me try a direct search instead."),
        ],
        mocks={
            "prax.agent.subagent._run_subagent": "Sub-agent failed: LLM rate limit exceeded",
        },
    )
    assert "error" in response.lower() or "instead" in response.lower()
    assert llm.call_count == 2


def test_sandbox_max_rounds_reached(run_e2e):
    """Agent handles sandbox round limit exhaustion."""
    response, llm = run_e2e(
        "Keep working on the code",
        [
            ai_tools(tc("sandbox_message", {"message": "Continue working on the implementation"})),
            ai(
                "The sandbox has reached its maximum of 10 message rounds. "
                "I'll finish the session and save what we have."
            ),
        ],
        mocks={
            "prax.services.sandbox_service.send_message": {
                "error": "Sandbox has reached the maximum of 10 message rounds. Use sandbox_finish to save what you have, or sandbox_abort to discard.",
                "rounds_used": 10,
                "max_rounds": 10,
            },
        },
    )
    assert "maximum" in response.lower() or "10" in response
    assert llm.call_count == 2
