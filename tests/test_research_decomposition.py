"""Tests for research agent topic decomposition / parallel sub-research."""
from __future__ import annotations

import threading
import time
from unittest.mock import patch


def _invoke(tool_fn, arg):
    """Invoke a @tool-decorated function with a single argument.

    LangChain's ``@tool`` decorator turns the callable into a BaseTool whose
    ``.invoke()`` expects a dict of kwargs AND validates types.  Because
    ``research_subtopics`` is declared as ``str`` for the LLM contract but
    we also want to test the tolerant list path, we bypass validation by
    calling the underlying ``.func`` directly.
    """
    func = getattr(tool_fn, "func", None) or tool_fn
    return func(arg)


def test_depth_guard_refuses_at_max_depth():
    """At depth 2, research_subtopics must refuse to decompose further."""
    from prax.agent import research_agent

    token = research_agent._research_depth.set(2)
    try:
        out = _invoke(
            research_agent.research_subtopics,
            '["a", "b"]',
        )
    finally:
        research_agent._research_depth.reset(token)

    assert "depth limit reached" in out.lower()


def test_max_subtopics_truncates_to_five():
    """Passing more than 5 subtopics truncates to 5 with a warning note."""
    from prax.agent import research_agent

    calls: list[str] = []
    lock = threading.Lock()

    def fake_run(question: str, depth: int = 0) -> str:
        with lock:
            calls.append(question)
        return f"finding for {question}"

    topics = [f"question {i}" for i in range(10)]
    with patch.object(research_agent, "_run_research", side_effect=fake_run):
        out = _invoke(research_agent.research_subtopics, topics)

    assert len(calls) == 5
    assert "truncated" in out.lower()
    # The first five subtopics should be the ones that actually ran.
    assert set(calls) == {f"question {i}" for i in range(5)}


def test_parallel_execution_beats_sequential_wall_time():
    """Three subtopics should run concurrently, not sequentially."""
    from prax.agent import research_agent

    concurrent_now: list[int] = [0]
    peak_concurrent: list[int] = [0]
    lock = threading.Lock()

    def fake_run(question: str, depth: int = 0) -> str:
        with lock:
            concurrent_now[0] += 1
            peak_concurrent[0] = max(peak_concurrent[0], concurrent_now[0])
        time.sleep(0.4)
        with lock:
            concurrent_now[0] -= 1
        return f"finding for {question}"

    topics = ["q1", "q2", "q3"]
    start = time.monotonic()
    with patch.object(research_agent, "_run_research", side_effect=fake_run):
        out = _invoke(research_agent.research_subtopics, topics)
    elapsed = time.monotonic() - start

    # Sequential would be ~1.2s; parallel should be well under 1s.
    assert elapsed < 1.0, f"expected parallel run, took {elapsed:.2f}s"
    assert peak_concurrent[0] >= 2, (
        f"expected concurrent execution, peak={peak_concurrent[0]}"
    )
    # All three results should be in the output in labeled sections.
    assert "Subtopic 1: q1" in out
    assert "Subtopic 2: q2" in out
    assert "Subtopic 3: q3" in out


def test_one_subtopic_failure_does_not_break_others():
    """A failing subtopic reports its error; the others still return results."""
    from prax.agent import research_agent

    def fake_run(question: str, depth: int = 0) -> str:
        if "bad" in question:
            raise RuntimeError("boom")
        return f"ok: {question}"

    with patch.object(research_agent, "_run_research", side_effect=fake_run):
        out = _invoke(
            research_agent.research_subtopics,
            '["good one", "bad topic", "another good"]',
        )

    assert "ok: good one" in out
    assert "ok: another good" in out
    assert "[error]" in out
    assert "boom" in out
    # The error should be contained in the "bad topic" section.
    bad_section = out.split("## Subtopic 2: bad topic", 1)[1]
    assert "[error]" in bad_section.split("## Subtopic 3:", 1)[0]


def test_json_parsing_accepts_string():
    """JSON string input is parsed correctly."""
    from prax.agent import research_agent

    calls: list[str] = []

    def fake_run(question: str, depth: int = 0) -> str:
        calls.append(question)
        return "ok"

    with patch.object(research_agent, "_run_research", side_effect=fake_run):
        _invoke(
            research_agent.research_subtopics,
            '["first topic", "second topic"]',
        )

    assert calls == ["first topic", "second topic"] or set(calls) == {
        "first topic",
        "second topic",
    }


def test_json_parsing_accepts_plain_list():
    """A plain Python list (no JSON encoding) is also accepted."""
    from prax.agent import research_agent

    calls: list[str] = []

    def fake_run(question: str, depth: int = 0) -> str:
        calls.append(question)
        return "ok"

    with patch.object(research_agent, "_run_research", side_effect=fake_run):
        _invoke(research_agent.research_subtopics, ["alpha", "beta"])

    assert set(calls) == {"alpha", "beta"}


def test_json_parsing_rejects_malformed_input():
    """Malformed JSON returns a helpful error message."""
    from prax.agent import research_agent

    with patch.object(
        research_agent, "_run_research", return_value="should not be called",
    ) as mock_run:
        out = _invoke(research_agent.research_subtopics, "{not valid json")

    assert mock_run.call_count == 0
    assert "research_subtopics" in out
    assert "json" in out.lower() or "parse" in out.lower()


def test_output_format_has_section_headers():
    """The combined report has one clearly-labeled section per subtopic."""
    from prax.agent import research_agent

    def fake_run(question: str, depth: int = 0) -> str:
        return f"body for {question}"

    with patch.object(research_agent, "_run_research", side_effect=fake_run):
        out = _invoke(
            research_agent.research_subtopics,
            '["What is X?", "How does Y work?"]',
        )

    assert "## Subtopic 1: What is X?" in out
    assert "## Subtopic 2: How does Y work?" in out
    assert "body for What is X?" in out
    assert "body for How does Y work?" in out


def test_empty_list_returns_helpful_error():
    """An empty list is rejected with a helpful message, not a silent pass."""
    from prax.agent import research_agent

    with patch.object(
        research_agent, "_run_research", return_value="should not be called",
    ) as mock_run:
        out = _invoke(research_agent.research_subtopics, "[]")

    assert mock_run.call_count == 0
    assert "empty" in out.lower()


def test_sub_agents_do_not_get_decomposition_tool():
    """At depth >= 1, research_subtopics must be excluded from the toolbox."""
    from prax.agent import research_agent

    # Patch out heavy dependencies so _build_research_tools can run.
    with (
        patch("prax.agent.tools.background_search_tool", create=True),
        patch("prax.agent.tools.fetch_url_content", create=True),
        patch("prax.agent.tools.get_current_datetime", create=True),
        patch("prax.plugins.loader.get_plugin_loader") as mock_loader,
    ):
        mock_loader.return_value.get_tools.return_value = []

        top_tools = research_agent._build_research_tools(depth=0)
        sub_tools = research_agent._build_research_tools(depth=1)

    top_names = {getattr(t, "name", None) for t in top_tools}
    sub_names = {getattr(t, "name", None) for t in sub_tools}

    assert "research_subtopics" in top_names
    assert "research_subtopics" not in sub_names


def test_child_depth_incremented_in_worker():
    """Spawned sub-research sees ``_research_depth`` at parent + 1."""
    from prax.agent import research_agent

    seen_depths: list[int] = []
    lock = threading.Lock()

    def fake_run(question: str, depth: int = 0) -> str:
        with lock:
            seen_depths.append(research_agent._research_depth.get())
        return "ok"

    with patch.object(research_agent, "_run_research", side_effect=fake_run):
        _invoke(research_agent.research_subtopics, '["a", "b"]')

    assert seen_depths
    assert all(d == 1 for d in seen_depths), seen_depths
    # Parent context untouched.
    assert research_agent._research_depth.get() == 0
