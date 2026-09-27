"""A person can stop a running turn, and a failing spoke cannot be retried forever.

The incident: a browser loop ran 26 minutes and $3.41; "stop" could not reach
it, and — arriving as a turn that did not know anything was running — was read
as "delete the user's schedule".
"""
from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langchain_core.tools import StructuredTool

import prax.agent.governed_tool as gov
import prax.settings as prax_settings
from prax.services import turn_registry as reg
from prax.services.turn_registry import TurnCancelled


@pytest.fixture(autouse=True)
def _clean():
    reg._turns.clear()
    yield
    reg._turns.clear()


# --- registry ---------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "stop", "Stop!", "please stop", "stop it", "stop that", "STOP NOW", "cancel",
    "never mind", "nevermind", "abort", "stop please",
])
def test_bare_stop_requests(text):
    assert reg.is_stop_request(text)


@pytest.mark.parametrize("text", [
    "stop the schedule", "stop sending me interview questions", "don't stop",
    "can you stop by the store", "stopwatch", "", "please solve this",
])
def test_messages_that_name_something_are_not_bare_stops(text):
    assert not reg.is_stop_request(text)


def test_cancel_stops_only_this_users_other_turns():
    mine = reg.begin("u1", "please solve this")
    other_user = reg.begin("u2", "something")
    current = reg.begin("u1", "stop solving that")
    stopped = reg.cancel("u1", exclude=current)
    assert stopped == [mine]
    assert mine.cancel.is_set()
    assert not current.cancel.is_set() and not other_user.cancel.is_set()
    assert reg.running("u1") == [current]  # a cancelled turn is no longer "running"
    reg.end(mine)
    assert mine.id not in reg._turns


def test_describe_names_the_request_and_age():
    turn = reg.begin("u1", "please   solve\nthis")
    turn.started -= 26 * 60
    assert reg.describe(turn) == '"please solve this" (running 26 min)'


def test_turn_cancelled_passes_through_catch_all_handlers():
    # Spoke runners and retry loops catch Exception; a stop must not be one.
    assert not issubclass(TurnCancelled, Exception)


# --- governed tools ---------------------------------------------------------

def _tool(name="sandbox_browser_act", calls=None):
    def act(value: str = "") -> str:
        (calls if calls is not None else []).append(value)
        return "ok"
    return gov.wrap_with_governance(
        StructuredTool.from_function(func=act, name=name, description="t"),
        layer="spoke", enforce=False)


def test_a_stopped_turn_ends_at_its_next_tool_call():
    calls: list = []
    tool = _tool(calls=calls)
    state = gov.begin_turn()
    state.turn = reg.begin("u1", "please solve this")
    assert tool.invoke({"value": "a"}) == "ok"
    state.turn.cancel.set()
    with pytest.raises(TurnCancelled):
        tool.invoke({"value": "b"})
    assert calls == ["a"]
    gov.drain_audit_log()


def test_tools_outside_a_registered_turn_are_unaffected():
    gov.begin_turn()
    assert _tool().invoke({"value": "x"}) == "ok"
    gov.drain_audit_log()


# --- conversation entry: a bare stop never reaches the model -----------------

@pytest.fixture
def convo(monkeypatch, tmp_path):
    from prax.services import conversation_service as cs

    service = cs.ConversationService.__new__(cs.ConversationService)
    saved = []
    service.resolve_conversation = lambda uid, key, space_slug=None: ("db", 1)
    service._save = lambda db, key, msg: saved.append(msg)
    service._reply = lambda *a, **k: "MODEL RAN"
    monkeypatch.setattr(prax_settings.settings, "turn_stop_enabled", True)
    return service, saved


def test_stop_while_running_cancels_and_answers_without_the_model(convo):
    service, saved = convo
    running = reg.begin("u1", "please solve this")
    out = service.reply("u1", "stop")
    assert running.cancel.is_set()
    assert out.startswith('Stopped "please solve this"') and "MODEL RAN" not in out
    assert [m["role"] for m in saved] == ["user", "assistant"]


def test_stop_with_nothing_running_goes_to_the_model(convo):
    service, _ = convo
    assert service.reply("u1", "stop") == "MODEL RAN"


def test_stop_does_nothing_special_when_the_flag_is_off(convo, monkeypatch):
    service, _ = convo
    monkeypatch.setattr(prax_settings.settings, "turn_stop_enabled", False)
    running = reg.begin("u1", "please solve this")
    assert service.reply("u1", "stop") == "MODEL RAN"
    assert not running.cancel.is_set()


def test_a_new_turn_is_told_what_is_still_running(convo):
    service, _ = convo
    reg.begin("u1", "please solve this")
    note = service._running_note("u1")
    assert '"please solve this"' in note and "stop_running_task" in note
    assert service._running_note("someone-else") == ""


def test_stop_running_task_tool_spares_its_own_turn(monkeypatch):
    from prax.agent.turn_tools import stop_running_task
    from prax.agent.user_context import current_user_id

    old = reg.begin("u1", "please solve this")
    state = gov.begin_turn()
    state.turn = reg.begin("u1", "stop solving that leetcode thing")
    token = current_user_id.set("u1")
    try:
        out = stop_running_task.invoke({})
    finally:
        current_user_id.reset(token)
    assert old.cancel.is_set() and not state.turn.cancel.is_set()
    assert out.startswith('Stopped: "please solve this"')


def test_the_stop_tool_exists_only_with_the_flag(monkeypatch):
    from prax.agent.turn_tools import build_turn_tools
    monkeypatch.setattr(prax_settings.settings, "turn_stop_enabled", False)
    assert build_turn_tools() == []
    monkeypatch.setattr(prax_settings.settings, "turn_stop_enabled", True)
    assert [t.name for t in build_turn_tools()] == ["stop_running_task"]


# --- orchestrator: registration and a prompt stop ----------------------------

def test_orchestrator_stops_waiting_as_soon_as_the_turn_is_cancelled(monkeypatch):
    from prax.agent import orchestrator as orch

    agent = orch.ConversationAgent.__new__(orch.ConversationAgent)
    release = threading.Event()
    agent.graph = SimpleNamespace(invoke=lambda *a, **k: release.wait(30) or {"messages": []})
    agent._record_answering_model = lambda payload: None
    monkeypatch.setattr(orch.settings, "agent_run_timeout", 60)
    state = gov.begin_turn()
    state.turn = reg.begin("u1", "please solve this")
    threading.Timer(0.3, state.turn.cancel.set).start()
    started = time.monotonic()
    try:
        with pytest.raises(TurnCancelled):
            agent._invoke_graph_once([], {}, "u1")
    finally:
        release.set()
    assert time.monotonic() - started < 5


# --- repeated spoke failures -------------------------------------------------

def test_a_spoke_that_keeps_failing_is_refused_with_an_instruction_to_report(monkeypatch):
    from prax.agent.spokes import _runner

    monkeypatch.setattr(prax_settings.settings, "spoke_failure_limit", 2)
    gov.begin_turn()
    assert _runner._failure_limit_refusal("browser") == ""
    for _ in range(2):
        _runner._record_failure("browser", RuntimeError("Recursion limit of 75 reached"))
    refusal = _runner._failure_limit_refusal("browser")
    assert "failed 2 times this turn" in refusal and "tell the user" in refusal
    assert "Recursion limit of 75" in refusal
    assert _runner._failure_limit_refusal("sandbox") == ""  # per spoke
    gov.begin_turn()
    assert _runner._failure_limit_refusal("browser") == ""  # per turn


def test_no_limit_by_default(monkeypatch):
    from prax.agent.spokes import _runner

    monkeypatch.setattr(prax_settings.settings, "spoke_failure_limit", 0)
    gov.begin_turn()
    for _ in range(10):
        _runner._record_failure("browser", RuntimeError("x"))
    assert _runner._failure_limit_refusal("browser") == ""


# --- trace retention ----------------------------------------------------------

def _graph_file(d, days_ago: int, size: int):
    day = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    f = d / f"graphs-{day}.jsonl"
    f.write_bytes(b"x" * size)
    return f


def test_retention_keeps_a_week_idle_history_and_caps_size(monkeypatch, tmp_path):
    from prax.agent import trace

    monkeypatch.setattr(trace, "_graphs_dir", lambda: tmp_path)
    monkeypatch.setattr(prax_settings.settings, "trace_retention_days", 90)
    monkeypatch.setattr(prax_settings.settings, "trace_retention_max_mb", 1)
    mb = 1024 * 1024
    today = _graph_file(tmp_path, 0, mb // 2)
    recent = _graph_file(tmp_path, 10, mb // 4)     # a week+ idle: kept now (was deleted)
    older = _graph_file(tmp_path, 30, mb // 2)      # pushes the total over 1 MB
    ancient = _graph_file(tmp_path, 120, 10)        # past the age window
    trace._rotate_graph_files()
    assert today.exists() and recent.exists()
    assert not older.exists(), "oldest in-window file goes first when over the cap"
    assert not ancient.exists()


def test_zero_keeps_everything(monkeypatch, tmp_path):
    from prax.agent import trace

    monkeypatch.setattr(trace, "_graphs_dir", lambda: tmp_path)
    monkeypatch.setattr(prax_settings.settings, "trace_retention_days", 0)
    monkeypatch.setattr(prax_settings.settings, "trace_retention_max_mb", 0)
    files = [_graph_file(tmp_path, n, 100) for n in (0, 400, 4000)]
    trace._rotate_graph_files()
    assert all(f.exists() for f in files)
    assert os.listdir(tmp_path)
