"""Tool callbacks against the outputs LangChain really hands them.

Two silent observability losses, both from assuming ``on_tool_end`` receives a
``str``. It receives a ``ToolMessage`` whenever the tool is invoked with a
ToolCall — every ToolNode call — and the tool's raw return value otherwise:

- ``OTelToolCallback`` sliced the message (``TypeError``) after popping its
  span, so ``span.end()`` never ran and every successful tool span was lost.
  LangChain logs and swallows callback errors, so nothing failed loudly.
- ``GraphCallbackHandler`` stored ``str(ToolMessage)`` — a pydantic repr — as
  every tool node's summary, which is what users read in trace reports.

Plus the duplicate count: governance and context binding wrap a tool in
same-named StructuredTools that each fire on_tool_start, so one logical call
was counted (and spanned) once per layer.

Every handler here runs with ``raise_error = True`` so a callback exception
fails the test instead of being logged away. Keyless and offline.
"""
from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import ToolException, tool
from langgraph.types import Command
from pydantic import Field

from prax.agent.message_text import tool_output_text


def _call(name: str, **args: Any) -> dict:
    return {"name": name, "args": args, "id": "tc1", "type": "tool_call"}


# ---------------------------------------------------------------------------
# Tools under test
# ---------------------------------------------------------------------------

@tool
def say(text: str) -> str:
    """Return *text*."""
    return text


@tool
def as_dict(text: str) -> dict:
    """Return a dict."""
    return {"echo": text, "n": 1}


@tool
def nothing(text: str) -> None:
    """Return None."""
    return None


@tool
def blocks(text: str) -> list:
    """Return provider-style content blocks."""
    return [{"type": "text", "text": "first"}, {"type": "text", "text": text}]


@tool
def handled_failure(text: str) -> str:
    """Fail, and let the tool turn the failure into an error result."""
    raise ToolException("disk on fire")


handled_failure.handle_tool_error = True


@tool
def unhandled_failure(text: str) -> str:
    """Fail loudly."""
    raise ValueError("kaboom")


@tool
def outer(text: str) -> str:
    """Call a DIFFERENT tool from inside this one."""
    return "outer:" + say.invoke({"text": text})


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeSpan:
    def __init__(self, name: str, attributes: dict | None):
        self.name = name
        self.attributes = dict(attributes or {})
        self.ended = 0

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def end(self) -> None:
        self.ended += 1


class _FakeTracer:
    def __init__(self):
        self.spans: list[_FakeSpan] = []

    def start_span(self, name: str, attributes: dict | None = None) -> _FakeSpan:
        span = _FakeSpan(name, attributes)
        self.spans.append(span)
        return span


class _FakeCounter:
    def __init__(self):
        self.counts: dict[str, int] = {}

    def labels(self, *, tool: str):
        counter = self

        class _Child:
            def inc(self, amount: int = 1) -> None:
                counter.counts[tool] = counter.counts.get(tool, 0) + amount

        return _Child()


class _ScriptedLLM(BaseChatModel):
    """Plays back fixed AIMessages so a real ToolNode runs the tool calls."""

    responses: list = Field(default_factory=list)
    counter: list = Field(default_factory=lambda: [0])

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-callback-test"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        idx = self.counter[0]
        self.counter[0] = idx + 1
        msg = self.responses[idx] if idx < len(self.responses) else AIMessage(content="end")
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools: Any, **kwargs: Any) -> _ScriptedLLM:
        return self


@pytest.fixture
def otel(monkeypatch):
    """A raising OTelToolCallback wired to a fake tracer and a fake TOOL_CALLS."""
    import prax.observability.callbacks as cb_mod
    import prax.observability.metrics as metrics_mod

    tracer = _FakeTracer()
    counter = _FakeCounter()
    monkeypatch.setattr(cb_mod, "_get_tracer", lambda: tracer)
    monkeypatch.setattr(metrics_mod, "TOOL_CALLS", counter)
    handler = cb_mod.OTelToolCallback()
    handler.raise_error = True
    return handler, tracer, counter


@pytest.fixture
def graph_handler():
    from prax.agent.trace import ExecutionGraph, GraphCallbackHandler, TraceHeartbeat

    graph = ExecutionGraph("t-tool-outputs")
    handler = GraphCallbackHandler(
        parent_span_id="root", graph=graph, trace_id="t-tool-outputs",
        heartbeat=TraceHeartbeat("t-tool-outputs"),
    )
    handler.raise_error = True
    return handler, graph


def _only_node(graph):
    nodes = list(graph._nodes.values())
    assert len(nodes) == 1, [n.name for n in nodes]
    return nodes[0]


def _assert_no_open_runs(handler) -> None:
    # The handler is a process-wide singleton: anything left behind grows forever.
    assert handler._spans == {}
    assert handler._start_times == {}
    assert handler._tool_names == {}
    assert handler._nested == set()


# ---------------------------------------------------------------------------
# tool_output_text
# ---------------------------------------------------------------------------

class TestToolOutputText:
    def test_none_is_empty(self):
        assert tool_output_text(None) == ""

    def test_str_passes_through(self):
        assert tool_output_text("plain") == "plain"

    def test_tool_message_yields_content_not_repr(self):
        msg = ToolMessage(content="the result", name="say", tool_call_id="tc1")
        assert tool_output_text(msg) == "the result"

    def test_tool_message_with_block_content_is_flattened(self):
        msg = ToolMessage(content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
                          tool_call_id="tc1")
        assert tool_output_text(msg) == "a\nb"

    def test_dict_and_int_use_str(self):
        assert tool_output_text({"a": 1}) == "{'a': 1}"
        assert tool_output_text(7) == "7"

    def test_command_uses_str(self):
        cmd = Command(update={"k": "v"})
        assert tool_output_text(cmd) == str(cmd)

    def test_list_of_records_is_not_reduced_to_empty(self):
        """content_text keeps only text blocks; records are not message content."""
        rows = [{"id": 1, "name": "x"}, {"id": 2, "name": "y"}]
        assert tool_output_text(rows) == str(rows)
        assert tool_output_text([1, 2]) == "[1, 2]"

    def test_unprintable_object_does_not_raise(self):
        class Broken:
            def __str__(self):
                raise RuntimeError("no")

        assert tool_output_text(Broken()) == "<unprintable Broken>"


# ---------------------------------------------------------------------------
# OTelToolCallback — direct ToolCall invokes
# ---------------------------------------------------------------------------

class TestOTelToolOutputs:
    @pytest.mark.parametrize("tool_obj, expected", [
        (say, "hello"),
        (as_dict, '{"echo": "hello", "n": 1}'),
        # ToolCall invokes JSON-stringify a None return; the preview shows
        # exactly what the model was shown.
        (nothing, "null"),
        (blocks, "first\nhello"),
    ])
    def test_span_ends_with_content_preview(self, otel, tool_obj, expected):
        handler, tracer, counter = otel
        result = tool_obj.invoke(_call(tool_obj.name, text="hello"),
                                 config={"callbacks": [handler]})

        assert isinstance(result, ToolMessage)
        (span,) = tracer.spans
        assert span.name == f"tool.{tool_obj.name}"
        assert span.ended == 1
        assert span.attributes["prax.tool.output_preview"] == expected
        assert counter.counts == {tool_obj.name: 1}
        _assert_no_open_runs(handler)

    def test_raw_none_output_previews_empty(self, otel):
        """A kwargs invoke hands the callback the raw return value."""
        handler, tracer, _ = otel
        nothing.invoke({"text": "x"}, config={"callbacks": [handler]})

        (span,) = tracer.spans
        assert span.ended == 1
        assert span.attributes["prax.tool.output_preview"] == ""

    def test_preview_is_capped(self, otel):
        handler, tracer, _ = otel
        say.invoke(_call("say", text="x" * 500), config={"callbacks": [handler]})
        assert tracer.spans[0].attributes["prax.tool.output_preview"] == "x" * 200

    def test_error_ends_span_with_error_attributes(self, otel):
        handler, tracer, counter = otel
        with pytest.raises(ValueError):
            unhandled_failure.invoke(_call("unhandled_failure", text="x"),
                                     config={"callbacks": [handler]})

        (span,) = tracer.spans
        assert span.ended == 1
        assert span.attributes["error"] is True
        assert span.attributes["error.message"] == "kaboom"
        assert counter.counts == {"unhandled_failure": 1}
        _assert_no_open_runs(handler)

    def test_span_ends_even_when_setting_the_preview_fails(self, otel, monkeypatch):
        import prax.observability.callbacks as cb_mod
        handler, _, _ = otel

        def _refuse(key, value):
            raise RuntimeError("exporter rejected attribute")

        class _RefusingTracer(_FakeTracer):
            def start_span(self, name, attributes=None):
                span = super().start_span(name, attributes)
                span.set_attribute = _refuse
                return span

        refusing = _RefusingTracer()
        monkeypatch.setattr(cb_mod, "_get_tracer", lambda: refusing)
        with pytest.raises(RuntimeError):
            say.invoke(_call("say", text="x"), config={"callbacks": [handler]})
        assert refusing.spans[0].ended == 1


# ---------------------------------------------------------------------------
# Nested governance wrappers — one logical call, one span, one count
# ---------------------------------------------------------------------------

def _spoke_stack(raw):
    """Production spoke composition: governance(bind_context(raw)) — 3 layers."""
    from prax.agent.governed_tool import wrap_with_governance
    from prax.agent.user_context import bind_tool_user_context
    return wrap_with_governance(bind_tool_user_context(raw), layer="spoke",
                                enforce=False, classify_as=raw)


def _hub_stack(raw):
    """Production hub composition (tool_registry): governance(raw) — 2 layers."""
    from prax.agent.governed_tool import wrap_with_governance
    return wrap_with_governance(raw)


class TestNestedWrappers:
    @pytest.mark.parametrize("stack", [_spoke_stack, _hub_stack], ids=["spoke", "hub"])
    def test_one_span_one_metric_per_logical_call(self, otel, stack):
        handler, tracer, counter = otel
        wrapped = stack(say)
        result = wrapped.invoke(_call("say", text="hi"), config={"callbacks": [handler]})

        assert result.content == "hi"
        (span,) = tracer.spans
        assert span.ended == 1
        assert span.attributes["prax.tool.output_preview"] == "hi"
        assert counter.counts == {"say": 1}
        _assert_no_open_runs(handler)

    def test_nested_error_is_one_failed_span(self, otel):
        handler, tracer, counter = otel
        with pytest.raises(ValueError):
            _spoke_stack(unhandled_failure).invoke(
                _call("unhandled_failure", text="x"), config={"callbacks": [handler]})

        (span,) = tracer.spans
        assert span.ended == 1
        assert span.attributes["error"] is True
        assert counter.counts == {"unhandled_failure": 1}
        _assert_no_open_runs(handler)

    def test_sequential_calls_each_count(self, otel):
        handler, tracer, counter = otel
        wrapped = _spoke_stack(say)
        for text in ("a", "b"):
            wrapped.invoke(_call("say", text=text), config={"callbacks": [handler]})

        assert [s.attributes["prax.tool.output_preview"] for s in tracer.spans] == ["a", "b"]
        assert all(s.ended == 1 for s in tracer.spans)
        assert counter.counts == {"say": 2}

    def test_a_different_tool_called_inside_is_its_own_call(self, otel):
        """The dedup is for same-named wrapper layers, not for real child calls."""
        handler, tracer, counter = otel
        outer.invoke(_call("outer", text="z"), config={"callbacks": [handler]})

        assert sorted(s.name for s in tracer.spans) == ["tool.outer", "tool.say"]
        assert all(s.ended == 1 for s in tracer.spans)
        assert counter.counts == {"outer": 1, "say": 1}
        _assert_no_open_runs(handler)

    def test_graph_handler_summary_is_content_through_wrappers(self, graph_handler):
        handler, graph = graph_handler
        _spoke_stack(say).invoke(_call("say", text="hi"), config={"callbacks": [handler]})

        node = _only_node(graph)
        assert node.status == "completed"
        assert node.summary == "hi"


# ---------------------------------------------------------------------------
# GraphCallbackHandler — node summaries and status
# ---------------------------------------------------------------------------

class TestGraphHandlerOutputs:
    @pytest.mark.parametrize("tool_obj, expected", [
        (say, "hello"),
        (as_dict, '{"echo": "hello", "n": 1}'),
        (nothing, "null"),
        (blocks, "first\nhello"),
    ])
    def test_summary_is_content_not_repr(self, graph_handler, tool_obj, expected):
        handler, graph = graph_handler
        tool_obj.invoke(_call(tool_obj.name, text="hello"), config={"callbacks": [handler]})

        node = _only_node(graph)
        assert node.status == "completed"
        assert node.summary == expected
        assert "tool_call_id" not in node.summary

    def test_handled_failure_marks_node_failed(self, graph_handler):
        handler, graph = graph_handler
        result = handled_failure.invoke(_call("handled_failure", text="x"),
                                        config={"callbacks": [handler]})

        assert result.status == "error"
        node = _only_node(graph)
        assert node.status == "failed"
        assert node.summary == "disk on fire"

    def test_live_output_shows_content_and_failure_mark(self, monkeypatch):
        import prax.services.teamwork_hooks as hooks
        from prax.agent.trace import ExecutionGraph, GraphCallbackHandler, TraceHeartbeat

        pushed: list[str] = []
        logged: list[str] = []
        monkeypatch.setattr(hooks, "push_live_output",
                            lambda agent, text, **kw: pushed.append(text))
        monkeypatch.setattr(hooks, "log_activity",
                            lambda agent, kind, text, *a, **kw: logged.append(text))
        handler = GraphCallbackHandler(
            parent_span_id="root", graph=ExecutionGraph("t-live"), trace_id="t-live",
            live_agent_name="Prax", heartbeat=TraceHeartbeat("t-live"),
        )
        handler.raise_error = True

        say.invoke(_call("say", text="fine"), config={"callbacks": [handler]})
        handled_failure.invoke(_call("handled_failure", text="x"),
                               config={"callbacks": [handler]})

        assert "    ✔ say: fine\n" in pushed
        assert "    ✘ handled_failure: disk on fire\n" in pushed
        assert logged == ["say: fine", "handled_failure: disk on fire"]


# ---------------------------------------------------------------------------
# Through the real agent loop (create_agent's ToolNode)
# ---------------------------------------------------------------------------

class TestThroughAgentLoop:
    def _run(self, tool_name: str, callbacks: list) -> dict:
        from prax.agent.agent_loop import build_agent_loop
        llm = _ScriptedLLM(responses=[
            AIMessage(content="", tool_calls=[
                {"name": tool_name, "args": {"text": "x"}, "id": "tc1"},
            ]),
            AIMessage(content="done"),
        ])
        loop = build_agent_loop(llm, [say, handled_failure])
        return loop.invoke({"messages": [HumanMessage("go")]},
                           config={"callbacks": callbacks})

    def test_toolnode_success(self, otel, graph_handler):
        otel_handler, tracer, counter = otel
        handler, graph = graph_handler
        self._run("say", [otel_handler, handler])

        (span,) = tracer.spans
        assert span.ended == 1
        assert span.attributes["prax.tool.output_preview"] == "x"
        assert counter.counts == {"say": 1}
        node = _only_node(graph)
        assert (node.status, node.summary) == ("completed", "x")

    def test_toolnode_error_tool_message_marks_node_failed(self, otel, graph_handler):
        """A tool-handled failure ends via on_tool_end, never on_tool_error."""
        otel_handler, tracer, _ = otel
        handler, graph = graph_handler
        out = self._run("handled_failure", [otel_handler, handler])

        (tool_msg,) = [m for m in out["messages"] if isinstance(m, ToolMessage)]
        assert tool_msg.status == "error"
        node = _only_node(graph)
        assert (node.status, node.summary) == ("failed", "disk on fire")
        (span,) = tracer.spans
        assert span.ended == 1
        assert span.attributes["prax.tool.output_preview"] == "disk on fire"
