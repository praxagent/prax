"""E2E tests for web search and URL fetch workflows."""
from tests.e2e.conftest import ai, ai_tools, make_async_return, tc


def test_web_search(run_e2e):
    """Agent searches the web and synthesizes a response."""
    response, llm = run_e2e(
        "What is quantum computing?",
        [
            ai_tools(tc("background_search_tool", {"query": "quantum computing"})),
            ai("Quantum computing uses qubits to perform calculations exponentially faster than classical computers."),
        ],
        mocks={
            "prax.helpers_functions.background_search": make_async_return(
                "Quantum computing leverages quantum mechanics — superposition "
                "and entanglement — to process information in fundamentally new ways."
            ),
        },
    )
    assert "qubits" in response
    assert llm.call_count == 2  # tool call + final response


def test_url_fetch(run_e2e):
    """Agent fetches a URL and summarizes its content."""
    import requests as _requests

    class FakeResponse:
        status_code = 200
        ok = True
        text = "<html><head><title>Test Article</title></head><body>This is about machine learning advances in 2026.</body></html>"
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            pass

    response, llm = run_e2e(
        "Summarize https://example.com/article",
        [
            ai_tools(tc("fetch_url_content", {"url": "https://example.com/article"})),
            ai("The article discusses recent machine learning advances in 2026."),
        ],
        mocks={
            "requests.get": FakeResponse(),
        },
    )
    assert "machine learning" in response
    assert llm.call_count == 2


def test_search_then_followup(run_e2e):
    """Agent does a search, then uses the result in a follow-up response."""
    response, llm = run_e2e(
        "Find information about the James Webb Space Telescope discoveries",
        [
            ai_tools(tc("background_search_tool", {"query": "James Webb Space Telescope discoveries 2026"})),
            ai(
                "The James Webb Space Telescope has made several groundbreaking discoveries, "
                "including detecting the earliest galaxies ever observed and providing new "
                "insights into exoplanet atmospheres."
            ),
        ],
        mocks={
            "prax.helpers_functions.background_search": make_async_return(
                "JWST has observed galaxies from 300 million years after the Big Bang. "
                "It has also detected CO2 in exoplanet atmospheres for the first time."
            ),
        },
    )
    assert "James Webb" in response or "galaxies" in response
    assert llm.call_count == 2


def test_parallel_tool_calls(run_e2e):
    """Agent makes multiple tool calls in a single LLM turn."""
    response, llm = run_e2e(
        "What time is it and also search for today's news?",
        [
            ai_tools(
                tc("get_current_datetime", {"timezone_name": "America/New_York"}, call_id="call_time"),
                tc("background_search_tool", {"query": "today's top news"}, call_id="call_news"),
            ),
            ai("It's currently 2:30 PM ET. Today's top stories include new climate legislation and tech earnings."),
        ],
        mocks={
            "prax.helpers_functions.background_search": make_async_return(
                "Top stories: New climate bill passes Senate. Tech earnings exceed expectations."
            ),
        },
    )
    assert "2:30" in response or "news" in response.lower()
    assert llm.call_count == 2
