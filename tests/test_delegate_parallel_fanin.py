"""A fan-out's merge must count its inputs against what it dispatched.

`delegate_parallel` already wrote a failure string into each dead slot, so a
failure was visible — but nothing counted them, and the trace span asserted
"{N} tasks completed" whatever happened. A model reading twelve results of
which three say "Task failed" can still write a confident synthesis over the
nine, and the trace (the artifact a person opens to find out what went wrong)
carried an unearned completion claim.

Third instance of this defect class in one week, and the reason it is worth a
dedicated test: the eval aggregate dropped errored cases from its pass rate,
the PDF reader skipped pages it could not parse, and this merge counted every
dispatched task as complete. Same shape each time — a step that reports on a
set without checking the set is whole.
"""

import pytest

from prax.agent import subagent, trace


@pytest.fixture()
def runner(monkeypatch):
    """Drive delegate_parallel with a scripted outcome per task name."""
    def _install(behaviour: dict):
        def fake(spec):
            name = spec.get("name") or spec.get("task", "")
            action = behaviour.get(name, "ok")
            if action == "raise":
                raise RuntimeError("boom")
            if action == "hang":
                import time
                time.sleep(5)
            return f"result for {name}"

        # delegate_parallel imports these from prax.agent.trace INSIDE the
        # function, so the patch must land on the source module.
        monkeypatch.setattr(subagent, "_run_spoke_or_subagent", fake)
        monkeypatch.setattr(trace, "get_graph_summary", lambda: "")
    return _install


def tasks(*names):
    return [{"task": f"do {n}", "name": n, "category": "research"} for n in names]


def run(task_list):
    """Call the underlying function — delegate_parallel is @tool-decorated, so
    the module attribute is a StructuredTool, not a callable."""
    return subagent.delegate_parallel.func(task_list)


class TestCompleteFanOutStaysClean:
    def test_no_banner_when_everything_returns(self, runner):
        """A guard that fires on healthy input gets ignored."""
        runner({})
        out = run(tasks("a", "b", "c"))
        assert "INCOMPLETE FAN-OUT" not in out
        assert "result for a" in out and "result for c" in out


class TestPartialFanOutIsAnnounced:
    def test_failure_is_counted_and_led_with(self, runner):
        runner({"b": "raise"})
        out = run(tasks("a", "b", "c"))
        assert out.startswith("[INCOMPLETE FAN-OUT]")
        assert "2 of 3" in out
        assert "1 failed" in out

    def test_banner_precedes_the_results(self, runner):
        """A caveat after the content is a caveat nobody reads."""
        runner({"b": "raise"})
        out = run(tasks("a", "b", "c"))
        assert out.index("INCOMPLETE") < out.index("result for a")

    def test_caller_is_told_not_to_present_it_as_complete(self, runner):
        """The banner exists to change what the agent SAYS, not just to log."""
        runner({"b": "raise"})
        out = run(tasks("a", "b"))
        assert "PARTIAL" in out
        assert "rather than presenting it as" in out

    def test_individual_failure_text_is_still_present(self, runner):
        """The summary must not replace the per-slot detail — the reader needs
        to know WHICH task died, not only that one did."""
        runner({"b": "raise"})
        out = run(tasks("a", "b"))
        assert "Task failed" in out

    def test_all_failed_is_reported_as_zero(self, runner):
        runner({"a": "raise", "b": "raise"})
        out = run(tasks("a", "b"))
        assert "0 of 2" in out


class TestTraceSpanTellsTheTruth:
    def test_span_summary_reports_the_real_count(self, runner, monkeypatch):
        """The span used to claim every dispatched task completed."""
        captured = {}

        class _Span:
            def end(self, status="completed", summary=""):
                captured["status"] = status
                captured["summary"] = summary

        monkeypatch.setattr(trace, "start_span", lambda *a, **k: _Span())
        runner({"b": "raise"})
        run(tasks("a", "b", "c"))
        assert captured["summary"].startswith("2/3")
        assert "1 failed" in captured["summary"]
        assert captured["status"] == "partial"

    def test_clean_run_span_says_completed(self, runner, monkeypatch):
        captured = {}

        class _Span:
            def end(self, status="completed", summary=""):
                captured["status"] = status
                captured["summary"] = summary

        monkeypatch.setattr(trace, "start_span", lambda *a, **k: _Span())
        runner({})
        run(tasks("a", "b"))
        assert captured["status"] == "completed"
        assert captured["summary"].startswith("2/2")
