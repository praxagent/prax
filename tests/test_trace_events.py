"""Tests for prax.trace_events — canonical event type vocabulary."""
from __future__ import annotations


class TestTraceEventVocabulary:
    def test_all_expected_types_exist(self):
        from prax.trace_events import TraceEvent
        expected = {
            "user", "assistant", "system", "tool_call", "tool_result", "audit", "error",
            "decision",
            "plugin_import", "plugin_activate", "plugin_block",
            "plugin_rollback", "plugin_remove", "plugin_security_warn",
            "tier_choice",
            "think",
            "prediction_error", "epistemic_gate", "logprob_entropy", "semantic_entropy",
            "feedback", "failure_case", "eval_result",
        }
        assert TraceEvent.values() == expected

    def test_is_valid_known_types(self):
        from prax.trace_events import TraceEvent
        assert TraceEvent.is_valid("user")
        assert TraceEvent.is_valid("audit")
        assert TraceEvent.is_valid("tool_call")

    def test_is_valid_rejects_unknown(self):
        from prax.trace_events import TraceEvent
        assert not TraceEvent.is_valid("unknown")
        assert not TraceEvent.is_valid("foo")
        assert not TraceEvent.is_valid("")

    def test_enum_values_are_strings(self):
        from prax.trace_events import TraceEvent
        # TraceEvent inherits from str, so values work as dict keys/values.
        assert TraceEvent.AUDIT == "audit"
        assert TraceEvent.TOOL_CALL == "tool_call"
        assert isinstance(TraceEvent.USER, str)

    def test_can_be_used_as_dict_type(self):
        """TraceEvent values work seamlessly as entry['type'] values."""
        from prax.trace_events import TraceEvent
        entry = {"type": TraceEvent.AUDIT, "content": "test"}
        assert entry["type"] == "audit"
        assert entry["type"].upper() == "AUDIT"


class TestTierChoiceInTraceGraph:
    """Verify tier choices are tracked in the execution graph."""

    def test_span_node_stores_tier_choices(self):
        from prax.agent.trace import SpanNode
        node = SpanNode(
            span_id="s1", name="test", parent_id=None,
            trace_id="t1", spoke_or_category="orchestrator",
        )
        assert node.tier_choices == []
        node.tier_choices.append({"tier_requested": "low", "model": "gpt-nano"})
        assert len(node.tier_choices) == 1

    def test_complete_node_accepts_tier_choices(self):
        from prax.agent.trace import ExecutionGraph, SpanNode
        graph = ExecutionGraph("t1")
        node = SpanNode(
            span_id="s1", name="test", parent_id=None,
            trace_id="t1", spoke_or_category="orchestrator",
        )
        graph.add_node(node)
        graph.complete_node(
            "s1", status="completed",
            tier_choices=[{"tier_requested": "high", "model": "gpt-5.4"}],
        )
        assert len(graph._nodes["s1"].tier_choices) == 1
        assert graph._nodes["s1"].tier_choices[0]["tier_requested"] == "high"

    def test_timed_out_root_keeps_graph_terminal_even_if_child_leaks_running(self):
        from prax.agent.trace import ExecutionGraph, SpanNode
        graph = ExecutionGraph("t1")
        root = SpanNode(
            span_id="root", name="orchestrator", parent_id=None,
            trace_id="t1", spoke_or_category="orchestrator",
        )
        child = SpanNode(
            span_id="child", name="late_tool", parent_id="root",
            trace_id="t1", spoke_or_category="tool",
        )
        graph.add_node(root)
        graph.add_node(child)
        graph.complete_node("root", status="timed_out", summary="timeout")

        data = graph.to_dict()

        assert data["status"] == "timed_out"
        assert next(n for n in data["nodes"] if n["span_id"] == "root")["status"] == "timed_out"
        assert next(n for n in data["nodes"] if n["span_id"] == "child")["status"] == "running"

    def test_graph_summary_includes_tier_info(self):
        from prax.agent.trace import ExecutionGraph, SpanNode
        graph = ExecutionGraph("t1")
        node = SpanNode(
            span_id="s1", name="orchestrator", parent_id=None,
            trace_id="t1", spoke_or_category="orchestrator",
            tier_choices=[
                {"tier_requested": "low", "model": "gpt-nano"},
                {"tier_requested": "low", "model": "gpt-nano"},
                {"tier_requested": "medium", "model": "gpt-mini"},
            ],
        )
        graph.add_node(node)
        summary = graph.get_summary()
        assert "tiers:" in summary
        assert "low→gpt-nano x2" in summary
        assert "medium→gpt-mini" in summary

    def test_get_all_tier_choices(self):
        from prax.agent.trace import ExecutionGraph, SpanNode, get_all_tier_choices
        graph = ExecutionGraph("t1")
        graph.add_node(SpanNode(
            span_id="s1", name="orch", parent_id=None,
            trace_id="t1", spoke_or_category="orchestrator",
            tier_choices=[{"ts": 1, "tier_requested": "low", "model": "nano"}],
        ))
        graph.add_node(SpanNode(
            span_id="s2", name="research", parent_id="s1",
            trace_id="t1", spoke_or_category="research",
            tier_choices=[{"ts": 2, "tier_requested": "medium", "model": "mini"}],
        ))
        choices = get_all_tier_choices(graph)
        assert len(choices) == 2
        assert choices[0]["model"] == "nano"
        assert choices[1]["model"] == "mini"

    def test_tier_choice_event_type_is_valid(self):
        from prax.trace_events import TraceEvent
        assert TraceEvent.is_valid("tier_choice")
        assert TraceEvent.TIER_CHOICE == "tier_choice"


class TestOrchestratorUsesTraceEvents:
    """Verify the orchestrator emits entries using TraceEvent constants."""

    def test_orchestrator_imports_trace_events(self):
        import ast
        from pathlib import Path
        source = (Path(__file__).parent.parent / "prax" / "agent" / "orchestrator.py").read_text()
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "prax.trace_events":
                imports.extend(alias.name for alias in node.names)
        assert "TraceEvent" in imports

    def test_orchestrator_no_raw_type_strings(self):
        """The orchestrator should use TraceEvent.X, not raw 'type': 'user' strings."""
        import ast
        from pathlib import Path
        source = (Path(__file__).parent.parent / "prax" / "agent" / "orchestrator.py").read_text()
        tree = ast.parse(source)

        # Look for dict entries like {"type": "user"} (string literal as type value).
        # These should be TraceEvent.X instead.
        raw_type_strings = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=False):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "type"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    ):
                        raw_type_strings.append(value.value)
        assert raw_type_strings == [], (
            f"orchestrator.py uses raw type strings instead of TraceEvent: {raw_type_strings}"
        )


class TestGraphCallbackHandlerLangChainDispatch:
    """Exercise GraphCallbackHandler through LangChain's real dispatch path.

    The callback manager checks attributes like ``raise_error`` and
    ``ignore_chain`` on every handler before dispatching.  If any of those
    are missing, LangChain raises ``AttributeError`` — which crashes the
    whole agent loop.  These tests would have caught that.
    """

    def _make_handler(self):
        from prax.agent.trace import ExecutionGraph, GraphCallbackHandler
        graph = ExecutionGraph("test-trace")
        handler = GraphCallbackHandler(
            parent_span_id="root-span", graph=graph, trace_id="test-trace",
        )
        return handler, graph

    def test_handle_event_chain_start_does_not_crash(self):
        """LangGraph fires on_chain_start first — must not crash."""
        from uuid import uuid4

        from langchain_core.callbacks.manager import handle_event

        handler, _ = self._make_handler()
        # This is the exact codepath that crashed in production.
        # handle_event checks handler.ignore_chain and handler.raise_error.
        handle_event(
            [handler],
            "on_chain_start",
            None,  # ignore_condition_name for chain is "ignore_chain"
            {"name": "AgentExecutor"},
            {},
            run_id=uuid4(),
        )

    def test_handle_event_tool_start_creates_span_node(self):
        """Tool events must dispatch through handle_event and create nodes."""
        from uuid import uuid4

        from langchain_core.callbacks.manager import handle_event

        handler, graph = self._make_handler()
        rid = uuid4()
        handle_event(
            [handler],
            "on_tool_start",
            None,  # tool events have no ignore condition
            {"name": "web_search"},
            '{"query": "test"}',
            run_id=rid,
        )
        assert len(graph._nodes) == 1
        node = list(graph._nodes.values())[0]
        assert node.name == "web_search"
        assert node.status == "running"

    def test_tool_events_touch_heartbeat(self):
        from uuid import uuid4

        from prax.agent.trace import ExecutionGraph, GraphCallbackHandler, TraceHeartbeat

        graph = ExecutionGraph("test-trace")
        heartbeat = TraceHeartbeat("test-trace")
        handler = GraphCallbackHandler(
            parent_span_id="root-span",
            graph=graph,
            trace_id="test-trace",
            heartbeat=heartbeat,
        )
        before = heartbeat.snapshot()["last_activity_at"]
        rid = uuid4()

        handler.on_tool_start({"name": "delegate_content_editor"}, "{}", run_id=rid)
        started = heartbeat.snapshot()
        handler.on_tool_end("done", run_id=rid)
        ended = heartbeat.snapshot()

        assert started["last_activity_at"] >= before
        assert started["last_source"] == "delegate_content_editor"
        assert ended["last_activity_at"] >= started["last_activity_at"]
        assert "completed tool delegate_content_editor" in ended["last_message"]

    def test_delegate_spoke_can_claim_callback_context_from_separate_context(self):
        """Spoke spans should nest under delegate_* even when contextvars split."""
        import contextvars
        from uuid import uuid4

        import prax.agent.trace as trace_module
        from prax.agent.trace import ExecutionGraph, GraphCallbackHandler, SpanNode

        graph = ExecutionGraph("test-trace")
        graph.session_id = "session-1"
        graph.add_node(SpanNode(
            span_id="root-span",
            name="orchestrator",
            parent_id=None,
            trace_id="test-trace",
            spoke_or_category="orchestrator",
        ))
        handler = GraphCallbackHandler(
            parent_span_id="root-span", graph=graph, trace_id="test-trace",
        )

        run_id = uuid4()
        callback_context = contextvars.copy_context()
        callback_context.run(
            handler.on_tool_start,
            {"name": "delegate_browser"},
            '{"task": "look up the weather"}',
            run_id=run_id,
        )
        assert trace_module.get_current_trace() is None

        parent = trace_module.claim_pending_delegation_context(
            "delegate_browser",
            task="look up the weather",
        )
        assert parent is not None
        span = trace_module.start_span("browser", "browser", parent_context=parent)
        span.end(status="completed", summary="done")
        callback_context.run(handler.on_tool_end, "done", run_id=run_id)

        delegate = next(n for n in graph._nodes.values() if n.name == "delegate_browser")
        browser = next(n for n in graph._nodes.values() if n.name == "browser")
        assert delegate.parent_id == "root-span"
        assert browser.parent_id == delegate.span_id
        assert graph.to_dict()["session_id"] == "session-1"

    def test_handle_event_tool_end_completes_node(self):
        from uuid import uuid4

        from langchain_core.callbacks.manager import handle_event

        handler, graph = self._make_handler()
        rid = uuid4()
        handle_event(
            [handler], "on_tool_start", None,
            {"name": "web_search"}, '{"q": "x"}', run_id=rid,
        )
        handle_event(
            [handler], "on_tool_end", None,
            "search results here", run_id=rid,
        )
        node = list(graph._nodes.values())[0]
        assert node.status == "completed"
        assert "search results" in node.summary

    def test_handle_event_tool_error_marks_failed(self):
        from uuid import uuid4

        from langchain_core.callbacks.manager import handle_event

        handler, graph = self._make_handler()
        rid = uuid4()
        handle_event(
            [handler], "on_tool_start", None,
            {"name": "failing_tool"}, "{}", run_id=rid,
        )
        handle_event(
            [handler], "on_tool_error", None,
            RuntimeError("boom"), run_id=rid,
        )
        node = list(graph._nodes.values())[0]
        assert node.status == "failed"
        assert "boom" in node.summary

    def test_handle_event_llm_start_ignored_no_crash(self):
        """LLM events should be silently ignored (ignore_llm=True)."""
        from uuid import uuid4

        from langchain_core.callbacks.manager import handle_event

        handler, graph = self._make_handler()
        handle_event(
            [handler],
            "on_llm_start",
            "ignore_llm",
            {"name": "ChatOpenAI"},
            ["hello"],
            run_id=uuid4(),
        )
        # No nodes created — LLM events are ignored.
        assert len(graph._nodes) == 0

    def test_all_ignore_attributes_present(self):
        """Ensure all attributes that LangChain's handle_event checks exist."""
        handler, _ = self._make_handler()
        required_attrs = [
            "raise_error",
            "ignore_llm",
            "ignore_chain",
            "ignore_agent",
            "ignore_retriever",
            "ignore_retry",
            "ignore_chat_model",
        ]
        for attr in required_attrs:
            assert hasattr(handler, attr), (
                f"GraphCallbackHandler missing '{attr}' — LangChain's "
                f"handle_event will crash with AttributeError"
            )
