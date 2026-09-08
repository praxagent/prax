"""Per-turn governance state lives in ONE object held in a ContextVar.

July's root cause 2: the audit buffer, the HIGH-risk and lethal-trifecta
latches and the tool-call budget were module globals in ``governed_tool`` —
one set for the whole process.  Two turns in flight (two users, or the same
user's follow-up racing an in-flight turn) shared them: one turn's confirmation
unlocked the other's HIGH tools, one turn's audit entries were flushed into the
other's trace.

Now ``begin_turn()`` binds a fresh :class:`TurnGovernanceState` in the turn's
context; the orchestrator re-binds the same object inside its graph worker
thread (``use_turn_state``) and drains it at the end.  The old module-level
names remain as thin accessors onto the current context's state, so the
single-turn behaviour is unchanged — the equivalence half of this file.
"""
from __future__ import annotations

import contextvars
import threading

from langchain_core.tools import StructuredTool

import prax.agent.governed_tool as gt


def _tool(name: str):
    return StructuredTool.from_function(
        func=lambda x="": f"executed:{x}", name=name, description=name)


# ---------------------------------------------------------------------------
# Equivalence: one context behaves exactly as the module globals did
# ---------------------------------------------------------------------------

class TestSingleContextEquivalence:
    def test_module_aliases_read_and_write_the_current_state(self):
        """``gov._tool_call_budget = 15`` (the way older tests set state) must
        hit the live state, and reads must see what the functions changed."""
        gt.drain_audit_log()
        gt._tool_call_budget = 15
        gt._tool_call_count = 10
        assert gt.get_budget_status() == (10, 15)
        gt.extend_budget(20)
        assert gt._tool_call_budget == 35
        gt._high_risk_confirmed = True
        assert gt.current_turn_state().high_risk_confirmed is True
        gt.drain_audit_log()
        assert gt._tool_call_budget == 0
        assert gt._high_risk_confirmed is False

    def test_from_imported_names_track_the_in_place_reset(self):
        gt.drain_audit_log()
        from prax.agent.governed_tool import _audit_buffer, _high_risk_seen
        governed = gt.wrap_with_governance(_tool("plugin_write"))
        governed.invoke({"x": "a"})
        assert "plugin_write" in _high_risk_seen
        assert len(_audit_buffer) == 1
        gt.drain_audit_log()
        assert len(_audit_buffer) == 0
        assert len(_high_risk_seen) == 0

    def test_init_turn_budget_keeps_the_audit_buffer(self):
        """``init_turn_budget`` only resets the budget — same as before."""
        gt.drain_audit_log()
        gt.wrap_with_governance(_tool("note_list")).invoke({"x": "a"})
        gt.init_turn_budget(3)
        assert len(gt.current_turn_state().audit) == 1
        assert gt.get_budget_status() == (0, 3)

    def test_begin_turn_starts_fresh_and_drain_returns_only_this_turn(self):
        gt.drain_audit_log()
        gt.wrap_with_governance(_tool("note_list")).invoke({"x": "stale"})
        state = gt.begin_turn(7)
        assert state.audit == [] and gt.get_budget_status() == (0, 7)
        gt.wrap_with_governance(_tool("note_list")).invoke({"x": "fresh"})
        drained = gt.drain_audit_log()
        assert [e["args"] for e in drained] == ["{'x': 'fresh'}"]

    def test_drain_resets_the_state_in_place(self):
        state = gt.begin_turn()
        gt.wrap_with_governance(_tool("plugin_write")).invoke({"x": "a"})
        gt.drain_audit_log()
        assert gt.current_turn_state() is state
        assert state.audit == [] and state.high_risk_seen == set()


# ---------------------------------------------------------------------------
# Isolation: turns in different contexts never see each other
# ---------------------------------------------------------------------------

class TestInterleavedTurns:
    def test_two_contexts_do_not_share_audit_entries_or_latches(self):
        ctx_a = contextvars.copy_context()
        ctx_b = contextvars.copy_context()
        ctx_a.run(gt.begin_turn)
        ctx_b.run(gt.begin_turn)
        high = gt.wrap_with_governance(_tool("plugin_write"))
        other = gt.wrap_with_governance(_tool("schedule_create"))

        # Turn A: first call blocked; the second confirms and (scoped confirm
        # off) unlocks every HIGH tool — for turn A.
        assert "HIGH risk" in ctx_a.run(high.invoke, {"x": "a1"})
        assert "executed:a1" in ctx_a.run(high.invoke, {"x": "a1"})
        assert "executed:o" in ctx_a.run(other.invoke, {"x": "o"})

        # Turn B, interleaved: none of A's latches apply.
        assert "HIGH risk" in ctx_b.run(high.invoke, {"x": "b1"})
        assert "HIGH risk" in ctx_b.run(other.invoke, {"x": "ob"})

        audit_a = ctx_a.run(gt.drain_audit_log)
        audit_b = ctx_b.run(gt.drain_audit_log)
        assert [e["tool_name"] for e in audit_a] == ["plugin_write", "plugin_write", "schedule_create"]
        assert [e["tool_name"] for e in audit_b] == ["plugin_write", "schedule_create"]
        assert all("BLOCKED" in e["result"] for e in audit_b)

    def test_budgets_are_per_turn(self):
        ctx_a = contextvars.copy_context()
        ctx_b = contextvars.copy_context()
        ctx_a.run(gt.begin_turn, 1)
        ctx_b.run(gt.begin_turn, 1)
        governed = gt.wrap_with_governance(_tool("note_list"))
        assert "executed:a" in ctx_a.run(governed.invoke, {"x": "a"})
        assert "executed:b" in ctx_b.run(governed.invoke, {"x": "b"})  # B's own budget
        assert "budget exhausted" in ctx_a.run(governed.invoke, {"x": "a2"}).lower()
        assert ctx_b.run(gt.get_budget_status) == (1, 1)

    def test_bound_worker_thread_shares_the_callers_turn(self):
        """The orchestrator's pattern: capture on the caller, bind in the worker."""
        state = gt.begin_turn()
        governed = gt.wrap_with_governance(_tool("note_list"))

        def worker():
            with gt.use_turn_state(state):
                governed.invoke({"x": "from-thread"})

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert [e["tool_name"] for e in gt.drain_audit_log()] == ["note_list"]

    def test_unbound_thread_does_not_write_into_the_callers_turn(self):
        state = gt.begin_turn()
        governed = gt.wrap_with_governance(_tool("note_list"))
        t = threading.Thread(target=lambda: governed.invoke({"x": "stray"}))
        t.start()
        t.join()
        assert state.audit == []
        gt.drain_audit_log()


# ---------------------------------------------------------------------------
# The orchestrator wires it: begin at turn start, bind in the graph worker
# ---------------------------------------------------------------------------

def test_orchestrator_graph_worker_writes_into_the_callers_turn():
    """``_invoke_graph_once`` runs the graph on a daemon thread (fresh context);
    it must re-bind the calling thread's turn state there.  (Guards the new
    wiring — the old process-wide globals passed this trivially.)"""
    from types import SimpleNamespace

    from prax.agent.orchestrator import ConversationAgent

    agent = ConversationAgent.__new__(ConversationAgent)
    agent.llm = SimpleNamespace(model_name="fake")
    governed = gt.wrap_with_governance(_tool("note_list"))

    class _Graph:
        def invoke(self, inputs, config=None):
            governed.invoke({"x": "in-worker"})
            return {"messages": []}

    agent.graph = _Graph()
    state = gt.begin_turn()
    try:
        agent._invoke_graph_once([], {}, "user-1")
        assert [e["tool_name"] for e in state.audit] == ["note_list"]
    finally:
        gt.drain_audit_log()


def test_orchestrator_run_begins_a_fresh_turn():
    """``run()`` used to call ``init_turn_budget`` (budget only); it now calls
    ``begin_turn`` so the audit buffer and latches start clean per turn.
    (A source check: running the real turn body needs a model.)"""
    import inspect

    from prax.agent.orchestrator import ConversationAgent

    src = inspect.getsource(ConversationAgent._run_turn)
    assert "begin_turn(effective_limit)" in src
    assert "init_turn_budget(" not in src


# ---------------------------------------------------------------------------
# TURN_LOCK_PER_USER — flag-gated serialisation of a user's turns
# ---------------------------------------------------------------------------

class TestTurnLockPerUser:
    """Off (default): turns for the same user may overlap, as before.
    On: a second turn for the same user waits for the in-flight one, and the
    space-model pin applied for a turn is reverted when that turn ends."""

    @staticmethod
    def _agent():
        from prax.agent.orchestrator import ConversationAgent
        return ConversationAgent.__new__(ConversationAgent)  # no model, no tools

    def _peak_concurrency(self, monkeypatch, *, flag: bool, users: tuple[str, str],
                          expect_overlap: bool) -> int:
        import time

        from prax.agent.user_context import current_user_id
        from prax.settings import settings

        monkeypatch.setattr(settings, "turn_lock_per_user", flag)
        agent = self._agent()
        gauge = threading.Lock()
        active = 0
        peak = 0
        # When overlap is expected, both turns must be inside the body at once;
        # a barrier makes that deterministic instead of timing-dependent.
        both_inside = threading.Barrier(2, timeout=2) if expect_overlap else None

        def fake_run_turn(conversation, user_input, workspace_context="", trigger="", source=""):
            nonlocal active, peak
            with gauge:
                active += 1
                peak = max(peak, active)
            if both_inside is not None:
                both_inside.wait()
            time.sleep(0.1)
            with gauge:
                active -= 1
            return "ok"

        monkeypatch.setattr(agent, "_run_turn", fake_run_turn)
        errors: list[BaseException] = []

        def turn(uid: str):
            try:
                current_user_id.set(uid)
                assert agent.run([], "hi") == "ok"
            except BaseException as exc:  # noqa: BLE001 - surfaced to the test below
                errors.append(exc)

        threads = [threading.Thread(target=turn, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        return peak

    def test_flag_on_serialises_turns_for_the_same_user(self, monkeypatch):
        peak = self._peak_concurrency(monkeypatch, flag=True, users=("u1", "u1"),
                                      expect_overlap=False)
        assert peak == 1

    def test_flag_off_lets_turns_for_the_same_user_overlap(self, monkeypatch):
        peak = self._peak_concurrency(monkeypatch, flag=False, users=("u1", "u1"),
                                      expect_overlap=True)
        assert peak == 2

    def test_flag_on_does_not_serialise_different_users(self, monkeypatch):
        """The lock is per user — documented scope, not a global turn mutex."""
        peak = self._peak_concurrency(monkeypatch, flag=True, users=("u1", "u2"),
                                      expect_overlap=True)
        assert peak == 2

    def _pinned_agent(self, monkeypatch):
        from types import SimpleNamespace

        from prax.agent import orchestrator as orch

        agent = self._agent()
        agent._applied_model_override = "space-pinned-model"
        agent._active_provider = "openai"
        agent._orchestrator_tier = "medium"
        agent.tools = []
        agent.checkpoint_mgr = SimpleNamespace(saver=None)
        agent.llm = "PINNED-LLM"
        agent.graph = "PINNED-GRAPH"
        monkeypatch.setattr(orch, "build_llm", lambda **kw: ("LLM", kw.get("tier")))
        monkeypatch.setattr(orch, "build_agent_loop", lambda llm, tools, **kw: ("GRAPH", llm))
        monkeypatch.setattr(agent, "_run_turn", lambda *a, **k: "ok")
        return agent

    def test_flag_on_reverts_the_space_model_pin_after_the_turn(self, monkeypatch):
        from prax.agent.user_context import current_user_id
        from prax.settings import settings

        monkeypatch.setattr(settings, "turn_lock_per_user", True)
        agent = self._pinned_agent(monkeypatch)
        token = current_user_id.set("u1")
        try:
            assert agent.run([], "hi") == "ok"
        finally:
            current_user_id.reset(token)
        assert agent._applied_model_override is None
        assert agent.llm == ("LLM", "medium")          # back on the tier default
        assert agent.graph == ("GRAPH", ("LLM", "medium"))

    def test_flag_off_leaves_the_pin_in_place_as_before(self, monkeypatch):
        from prax.settings import settings

        monkeypatch.setattr(settings, "turn_lock_per_user", False)
        agent = self._pinned_agent(monkeypatch)
        assert agent.run([], "hi") == "ok"
        assert agent._applied_model_override == "space-pinned-model"
        assert agent.llm == "PINNED-LLM"
        assert agent.graph == "PINNED-GRAPH"

    def test_resume_takes_the_same_lock(self, monkeypatch):
        from types import SimpleNamespace

        from prax.settings import settings

        monkeypatch.setattr(settings, "turn_lock_per_user", True)
        agent = self._agent()
        agent.checkpoint_mgr = SimpleNamespace(resume_turn=lambda uid: None)
        monkeypatch.setattr(agent, "_rebuild_if_needed", lambda: None)
        monkeypatch.setattr(agent, "_reset_to_primary_provider", lambda: None)
        lock = agent._turn_lock_for("u1")
        assert not lock.locked()
        # Hold the user's lock from another thread: resume must wait for it.
        lock.acquire()
        done = threading.Event()
        threading.Thread(target=lambda: (agent.resume_last_turn("u1"), done.set())).start()
        assert not done.wait(0.2)   # blocked behind the in-flight turn
        lock.release()
        assert done.wait(2)
