"""Keyless tests for the Terminal-Bench harbor adapter (prax/eval/tb_agent.py).

harbor is NOT a prax dependency: everything here exercises the
harness-independent core (tools + loop wiring + accounting) with fakes.
"""
import importlib
from types import SimpleNamespace

import prax.eval.tb_agent as tb


def _exec_recorder(replies=None):
    calls = []
    replies = replies or {}

    def execute(command, timeout_sec=180):
        calls.append((command, timeout_sec))
        return replies.get(
            command, {"stdout": f"ran: {command}", "stderr": "", "return_code": 0}
        )

    return execute, calls


class TestTerminalTool:
    def _tools(self, execute):
        state = {"steps": 0, "done": False, "summary": None}
        terminal, task_done = tb._make_tools(execute, state)
        return terminal, task_done, state

    def test_terminal_runs_command_and_reports_exit_code(self):
        execute, calls = _exec_recorder()
        terminal, _, state = self._tools(execute)
        out = terminal.invoke({"command": "ls /app"})
        assert calls == [("ls /app", 180)]
        assert "exit_code: 0" in out and "ran: ls /app" in out
        assert state["steps"] == 1

    def test_terminal_surfaces_stderr_and_nonzero_exit(self):
        execute, _ = _exec_recorder(
            {"bad": {"stdout": "", "stderr": "boom", "return_code": 2}}
        )
        terminal, _, _ = self._tools(execute)
        out = terminal.invoke({"command": "bad"})
        assert "exit_code: 2" in out and "stderr:\nboom" in out

    def test_terminal_bounds_giant_output_head_and_tail(self):
        execute, _ = _exec_recorder(
            {"cat big": {"stdout": "A" * 20_000 + "TAIL", "stderr": "", "return_code": 0}}
        )
        terminal, _, _ = self._tools(execute)
        out = terminal.invoke({"command": "cat big"})
        assert len(out) < 12_000
        assert "chars omitted" in out
        assert out.rstrip().endswith("TAIL")  # the tail survives truncation

    def test_execution_error_is_reported_not_raised(self):
        def execute(command, timeout_sec=180):
            raise ConnectionError("container gone")

        terminal, _, _ = self._tools(execute)
        out = terminal.invoke({"command": "ls"})
        assert "execution error" in out and "container gone" in out

    def test_task_done_records_summary_and_flags_state(self):
        execute, _ = _exec_recorder()
        _, task_done, state = self._tools(execute)
        task_done.invoke({"summary": "built and verified the binary"})
        assert state["done"] is True
        assert state["summary"] == "built and verified the binary"


class TestRunTerminalTask:
    def _fake_graph(self, messages, state_mutations=None):
        class FakeGraph:
            def invoke(self, payload, config=None):
                for fn in state_mutations or []:
                    fn()
                return {"messages": messages}

        return FakeGraph()

    def test_accounting_and_result_shape(self, monkeypatch):
        msg = SimpleNamespace(
            usage_metadata={"input_tokens": 100, "output_tokens": 40},
            response_metadata={"model_name": "test-model"},
        )
        monkeypatch.setattr(tb, "build_agent_loop", lambda llm, tools: self._fake_graph([msg, msg]))
        import prax.agent.llm_factory as f
        monkeypatch.setattr(f, "build_llm", lambda **kw: object())
        import prax.eval.pricing as pricing
        monkeypatch.setattr(pricing, "estimate_cost", lambda m, i, o: 0.0042)

        r = tb.run_terminal_task("do the thing", lambda c, timeout_sec=180: {})
        assert r["tokens_in"] == 200 and r["tokens_out"] == 80
        assert r["model"] == "test-model"
        assert r["cost_usd"] == 0.0042
        assert r["error"] is None
        assert r["done"] is False  # no task_done call happened

    def test_unknown_model_cost_is_none_not_zero(self, monkeypatch):
        msg = SimpleNamespace(
            usage_metadata={"input_tokens": 10, "output_tokens": 5},
            response_metadata={},
        )
        monkeypatch.setattr(tb, "build_agent_loop", lambda llm, tools: self._fake_graph([msg]))
        import prax.agent.llm_factory as f
        monkeypatch.setattr(f, "build_llm", lambda **kw: object())

        r = tb.run_terminal_task(
            "task", lambda c, timeout_sec=180: {}, model="no-such-model-xyz"
        )
        assert r["cost_usd"] is None

    def test_recursion_limit_becomes_step_budget_error(self, monkeypatch):
        class ExplodingGraph:
            def invoke(self, payload, config=None):
                raise tb.GraphRecursionError("hit limit")

        monkeypatch.setattr(tb, "build_agent_loop", lambda llm, tools: ExplodingGraph())
        import prax.agent.llm_factory as f
        monkeypatch.setattr(f, "build_llm", lambda **kw: object())

        r = tb.run_terminal_task("task", lambda c, timeout_sec=180: {}, max_steps=3)
        assert "step budget exhausted" in r["error"]
        assert r["done"] is False

    def test_system_prompt_is_benchmark_agnostic(self):
        """Anti-spike guard: the prompt must contain no benchmark vocabulary."""
        lowered = tb._SYSTEM_PROMPT.lower()
        for banned in ("terminal-bench", "harbor", "benchmark", "score", "verifier"):
            assert banned not in lowered

    def test_harbor_class_is_lazy(self):
        """Importing the module must not import harbor; only touching
        PraxAgent does (and fails cleanly when harbor is absent)."""
        mod = importlib.reload(tb)
        assert "PraxAgent" not in vars(mod)
        try:
            _ = mod.PraxAgent
        except ModuleNotFoundError as e:
            assert "harbor" in str(e)
