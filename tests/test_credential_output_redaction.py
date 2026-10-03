"""A credential tool's output never reaches an observability sink.

``browser_login`` returns ``username=…\\npassword=…`` in plain text under the
default ``BROWSER_SECRETS_OUT_OF_CONTEXT=false``. The model has to see it; the
OTel exporter, the execution trace, TeamWork's live output and activity feed,
and the spoke log do not — and each of them used to copy it. Every sink now
shows ``(output withheld: credential tool)`` for the credential tools named in
``hard_floors.CREDENTIAL_TOOLS``, whether or not hard floors are enabled.

Keyless and offline; handlers run with ``raise_error = True`` so a callback
exception fails the test instead of being logged away.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import Field

from prax.agent.message_text import (
    WITHHELD_OUTPUT,
    args_preview_for_tool,
    preview_for_tool,
)

SECRET = "hunter2"
SITE = "secret-site.example"


@tool
def browser_login(domain: str) -> str:
    """Stand-in for the real tool: returns a stored password to the model."""
    return f"username=tj\npassword={SECRET}"


@tool
def say(text: str) -> str:
    """Return *text*."""
    return text


def _call(name: str, **args: Any) -> dict:
    return {"name": name, "args": args, "id": "tc1", "type": "tool_call"}


def _spoke_stack(raw):
    from prax.agent.governed_tool import wrap_with_governance
    from prax.agent.user_context import bind_tool_user_context
    return wrap_with_governance(bind_tool_user_context(raw), layer="spoke",
                                enforce=False, classify_as=raw)


class _ScriptedLLM(BaseChatModel):
    responses: list = Field(default_factory=list)
    counter: list = Field(default_factory=lambda: [0])

    @property
    def _llm_type(self) -> str:
        return "scripted-credential-redaction-test"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        idx = self.counter[0]
        self.counter[0] = idx + 1
        msg = self.responses[idx] if idx < len(self.responses) else AIMessage(content="end")
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools: Any, **kwargs: Any) -> _ScriptedLLM:
        return self


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


@pytest.fixture
def otel(monkeypatch):
    import prax.observability.callbacks as cb_mod
    tracer = _FakeTracer()
    monkeypatch.setattr(cb_mod, "_get_tracer", lambda: tracer)
    handler = cb_mod.OTelToolCallback()
    handler.raise_error = True
    return handler, tracer


@pytest.fixture
def teamwork(monkeypatch):
    """Capture every push_live_output / log_activity call."""
    import prax.services.teamwork_hooks as hooks
    sent: list[str] = []
    monkeypatch.setattr(hooks, "push_live_output",
                        lambda agent, text, **kw: sent.append(str(text)))
    monkeypatch.setattr(hooks, "log_activity",
                        lambda agent, kind, text, *a, **kw: sent.append(str(text)))
    return sent


@pytest.fixture
def floors_off(monkeypatch):
    """Redaction does not depend on HARD_FLOORS_ENABLED."""
    from prax.agent import hard_floors
    monkeypatch.setattr(hard_floors, "enabled", lambda: False)


def _graph_handler(live: str | None = None):
    from prax.agent.trace import ExecutionGraph, GraphCallbackHandler, TraceHeartbeat
    graph = ExecutionGraph("t-cred")
    handler = GraphCallbackHandler(parent_span_id="root", graph=graph, trace_id="t-cred",
                                   live_agent_name=live, heartbeat=TraceHeartbeat("t-cred"))
    handler.raise_error = True
    return handler, graph


# ---------------------------------------------------------------------------
# The helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_credential_set_is_public_and_covers_the_floor_set(self):
        from prax.agent import hard_floors
        assert {"browser_login", "browser_credentials", "browser_fill_login",
                "browser_request_login", "browser_finish_login"} <= hard_floors.CREDENTIAL_TOOLS
        assert isinstance(hard_floors.CREDENTIAL_TOOLS, frozenset)
        assert hard_floors.CREDENTIAL_TOOLS <= hard_floors.BUILT_IN

    def test_credential_output_is_withheld(self, floors_off):
        msg = ToolMessage(content=f"password={SECRET}", name="browser_login", tool_call_id="x")
        assert preview_for_tool("browser_login", msg, 200) == WITHHELD_OUTPUT
        assert preview_for_tool("browser_credentials", f"password={SECRET}", 5) == WITHHELD_OUTPUT

    def test_other_output_is_flattened_and_capped(self):
        msg = ToolMessage(content=[{"type": "text", "text": "abcdef"}], tool_call_id="x")
        assert preview_for_tool("say", msg, 3) == "abc"
        assert preview_for_tool(None, "plain", 200) == "plain"

    def test_credential_args_show_names_only(self, floors_off):
        assert args_preview_for_tool("browser_login", {"domain": SITE}, 80) == "{'domain': '***'}"
        assert args_preview_for_tool("browser_login", f'{{"domain": "{SITE}"}}', 80) == "{'domain': '***'}"
        assert args_preview_for_tool("browser_login", f"{{'domain': '{SITE}'}}", 80) == "***"
        assert args_preview_for_tool("say", {"text": "hi"}, 80) == "{'text': 'hi'}"
        assert args_preview_for_tool("say", {"text": "x" * 100}, 10) == "{'text': '"


# ---------------------------------------------------------------------------
# OTel span attributes — they go to the exporter
# ---------------------------------------------------------------------------

class TestOTel:
    @pytest.mark.parametrize("wrap", [lambda t: t, _spoke_stack], ids=["raw", "spoke-stack"])
    def test_secret_never_in_span_attributes(self, otel, floors_off, wrap):
        handler, tracer = otel
        result = wrap(browser_login).invoke(_call("browser_login", domain=SITE),
                                            config={"callbacks": [handler]})
        assert SECRET in result.content          # the model still gets it
        (span,) = tracer.spans
        assert span.ended == 1
        assert span.attributes["prax.tool.output_preview"] == WITHHELD_OUTPUT
        assert span.attributes["prax.tool.input_preview"] == "{'domain': '***'}"
        flat = repr(span.attributes)
        assert SECRET not in flat and SITE not in flat

    def test_other_tools_keep_their_preview(self, otel):
        handler, tracer = otel
        say.invoke(_call("say", text="visible"), config={"callbacks": [handler]})
        (span,) = tracer.spans
        assert span.attributes["prax.tool.output_preview"] == "visible"
        assert span.attributes["prax.tool.input_preview"] == "{'text': 'visible'}"


# ---------------------------------------------------------------------------
# The execution trace and TeamWork live output / activity
# ---------------------------------------------------------------------------

class TestTraceAndTeamWork:
    def test_secret_never_in_trace_or_live_output(self, teamwork, floors_off):
        handler, graph = _graph_handler(live="Prax")
        _spoke_stack(browser_login).invoke(_call("browser_login", domain=SITE),
                                           config={"callbacks": [handler]})
        (node,) = graph._nodes.values()
        assert node.summary == WITHHELD_OUTPUT
        assert SECRET not in repr(graph.to_dict())
        assert teamwork, "the live output was not pushed at all"
        assert all(SECRET not in text for text in teamwork)
        assert f"    ✔ browser_login: {WITHHELD_OUTPUT}\n" in teamwork
        assert f"browser_login: {WITHHELD_OUTPUT}" in teamwork
        # Three wrapper layers, one logical call: completion is pushed once.
        assert sum("✔" in text for text in teamwork) == 1

    def test_running_node_shows_argument_names_only(self, floors_off):
        from uuid import uuid4
        handler, graph = _graph_handler()
        handler.on_tool_start({"name": "browser_login"}, str({"domain": SITE}),
                              run_id=uuid4(), inputs={"domain": SITE})
        (node,) = graph._nodes.values()
        assert node.status == "running"
        assert node.summary == "{'domain': '***'}"

    def test_through_the_agent_loop(self, otel, teamwork, floors_off):
        from prax.agent.agent_loop import build_agent_loop
        otel_handler, tracer = otel
        handler, graph = _graph_handler(live="Prax")
        llm = _ScriptedLLM(responses=[
            AIMessage(content="", tool_calls=[
                {"name": "browser_login", "args": {"domain": SITE}, "id": "tc1"}]),
            AIMessage(content="done"),
        ])
        out = build_agent_loop(llm, [_spoke_stack(browser_login)]).invoke(
            {"messages": [HumanMessage("log in")]},
            config={"callbacks": [otel_handler, handler]})
        (tool_msg,) = [m for m in out["messages"] if isinstance(m, ToolMessage)]
        assert SECRET in tool_msg.content
        assert SECRET not in repr(graph.to_dict())
        assert all(SECRET not in repr(s.attributes) for s in tracer.spans)
        assert all(SECRET not in text for text in teamwork)


# ---------------------------------------------------------------------------
# The spoke runner's tool log
# ---------------------------------------------------------------------------

class TestSpokeToolLog:
    def _result(self) -> dict:
        return {"messages": [
            AIMessage(content="", tool_calls=[
                {"name": "browser_login", "args": {"domain": SITE}, "id": "tc1"},
                {"name": "say", "args": {"text": "hello"}, "id": "tc2"},
            ]),
            ToolMessage(content=f"username=tj\npassword={SECRET}", name="browser_login",
                        tool_call_id="tc1"),
            ToolMessage(content="hello", name="say", tool_call_id="tc2"),
        ]}

    def test_secret_never_logged_or_pushed(self, caplog, teamwork, floors_off):
        from prax.agent.spokes._runner import _log_tool_calls
        with caplog.at_level(logging.DEBUG, logger="prax.agent.spokes._runner"):
            count = _log_tool_calls(self._result(), "browser", role_name="Browser Agent")
        assert count == 2
        logged = caplog.text + "".join(teamwork)
        assert SECRET not in logged
        assert SITE not in logged                    # values masked, names kept
        assert "browser_login({'domain': '***'})" in logged
        assert f"browser_login: {WITHHELD_OUTPUT}" in logged
        # Everything else is still shown.
        assert "say({'text': 'hello'})" in logged and "say: hello" in logged

    def test_a_failed_credential_call_is_still_marked_failed(self, teamwork, floors_off):
        from prax.agent.spokes._runner import _log_tool_calls
        result = {"messages": [ToolMessage(content=f"boom {SECRET}", name="browser_login",
                                           tool_call_id="tc1", status="error")]}
        _log_tool_calls(result, "browser", role_name="Browser Agent")
        assert teamwork == [f"  ✗ browser_login: {WITHHELD_OUTPUT}\n"]

    def test_a_credential_tool_reporting_failure_in_text_is_marked_failed(
            self, caplog, teamwork, floors_off):
        """The failure check reads the REAL output; the placeholder says
        nothing about it. The text itself is still not shown."""
        from prax.agent.spokes._runner import _log_tool_calls
        result = {"messages": [ToolMessage(
            content="Browser error filling the password field — check the selector.",
            name="browser_fill_login", tool_call_id="tc1")]}       # status="success"
        with caplog.at_level(logging.DEBUG, logger="prax.agent.spokes._runner"):
            _log_tool_calls(result, "browser", role_name="Browser Agent")
        assert teamwork == [f"  ✗ browser_fill_login: {WITHHELD_OUTPUT}\n"]
        assert "tool error [browser_fill_login]" in caplog.text
        assert "check the selector" not in caplog.text + "".join(teamwork)
