"""E2E tests for basic chat workflows — no tool calls."""
from tests.e2e.conftest import ai


def test_simple_greeting(run_e2e):
    """Agent responds to a greeting without calling any tools."""
    response, llm = run_e2e("Hello!", [ai("Hi there! How can I help you today?")])
    assert "Hi there" in response
    assert llm.call_count == 1


def test_factual_response(run_e2e):
    """Agent answers a factual question directly (no tool use)."""
    response, llm = run_e2e(
        "What is 2 + 2?",
        [ai("2 + 2 equals 4.")],
    )
    assert "4" in response
    assert llm.call_count == 1


def test_multi_sentence_response(run_e2e):
    """Agent returns a longer response preserving full content."""
    long_response = (
        "Python is a versatile programming language. "
        "It supports multiple paradigms including object-oriented, "
        "functional, and procedural programming. "
        "It is widely used in data science, web development, and automation."
    )
    response, llm = run_e2e(
        "Tell me about Python",
        [ai(long_response)],
    )
    assert "versatile" in response
    assert "data science" in response
    assert llm.call_count == 1
