"""``delegate_parallel`` must give every worker its OWN copy of the caller's context.

It used to take one ``contextvars.copy_context()`` and submit ``ctx.run`` for
every task.  A ``Context`` can be entered by one thread at a time, so with
three overlapping tasks the first entered and the other two died immediately
with ``RuntimeError: cannot enter context`` — before doing any work.  The
fan-in banner reported them as failed, but the failure was the plumbing, not
the tasks.  ``agent_loop.invoke_isolated`` already had the right pattern (one
copy per invocation); this pins it for the fan-out.
"""
from __future__ import annotations

import contextvars
import threading
import time

import pytest

from prax.agent import subagent, trace


@pytest.fixture(autouse=True)
def _quiet_trace(monkeypatch):
    monkeypatch.setattr(trace, "get_graph_summary", lambda: "")


def _tasks(*names):
    return [{"task": f"do {n}", "name": n, "category": "research"} for n in names]


def _run(task_list):
    return subagent.delegate_parallel.func(task_list)


def test_three_overlapping_tasks_all_run(monkeypatch):
    # The barrier forces genuine overlap: no worker returns until all three are
    # inside the task body at the same time.  On the shared-context code two of
    # them never reach the body, the barrier times out, and the fan-in reports
    # an incomplete fan-out.
    inside = threading.Barrier(3, timeout=5)
    ran: list[str] = []

    def fake(spec):
        inside.wait()
        time.sleep(0.02)
        ran.append(spec["name"])
        return f"result for {spec['name']}"

    monkeypatch.setattr(subagent, "_run_spoke_or_subagent", fake)
    out = _run(_tasks("a", "b", "c"))

    assert sorted(ran) == ["a", "b", "c"]
    assert "INCOMPLETE FAN-OUT" not in out
    assert "cannot enter context" not in out
    for n in "abc":
        assert f"result for {n}" in out


def test_every_worker_inherits_the_callers_contextvars(monkeypatch):
    """The copy is still a copy OF the caller: user identity reaches each worker."""
    from prax.agent.user_context import current_user_id

    inside = threading.Barrier(3, timeout=5)
    seen: list[str | None] = []

    def fake(spec):
        inside.wait()
        seen.append(current_user_id.get())
        return "ok"

    monkeypatch.setattr(subagent, "_run_spoke_or_subagent", fake)
    token = current_user_id.set("user-xyz")
    try:
        _run(_tasks("a", "b", "c"))
    finally:
        current_user_id.reset(token)
    assert seen == ["user-xyz"] * 3


def test_worker_mutations_do_not_leak_between_workers_or_to_the_caller(monkeypatch):
    probe: contextvars.ContextVar[str] = contextvars.ContextVar("probe", default="unset")
    written = threading.Event()
    seen: dict[str, str] = {}

    def fake(spec):
        if spec["name"] == "writer":
            probe.set("written")
            written.set()
        else:
            assert written.wait(timeout=5)
            seen["reader"] = probe.get()
        return "ok"

    monkeypatch.setattr(subagent, "_run_spoke_or_subagent", fake)
    _run(_tasks("writer", "reader"))
    assert seen["reader"] == "unset"
    assert probe.get() == "unset"
