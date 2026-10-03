"""A secret a credential tool hands out is masked wherever it shows up again.

Under the default ``BROWSER_SECRETS_OUT_OF_CONTEXT=false``, ``browser_login``
returns ``username=…\\npassword=…`` and its docstring tells the model to type the
password with ``browser_fill(selector, text)``. ``browser_fill`` is not a
credential tool, so withholding credential-tool output was not enough: the
password came back as an ARGUMENT and reached the OTel input preview, the
trace's running node, the spoke tool log, governance's INFO log and the audit
entry the orchestrator writes into the workspace ``trace.log``.

``prax.agent.turn_secrets`` remembers those values for the turn and every sink
masks them. Keyless and offline.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import Field

import prax.agent.governed_tool as gov
from prax.agent import turn_secrets
from prax.agent.message_text import WITHHELD_OUTPUT

SECRET = "Hunter2-s3cret!"
USERNAME = "tjuser"
SITE = "secret-site.example"
filled: list[str] = []


@tool
def browser_login(domain: str) -> str:
    """Stand-in for the real tool: returns a stored password to the model."""
    return f"username={USERNAME}\npassword={SECRET}"


@tool
def browser_fill(selector: str, text: str) -> str:
    """Stand-in for the real tool: types *text* into the page."""
    filled.append(text)
    return f"Filled '{selector}' with text."


@pytest.fixture
def turn():
    """A fresh turn state for the test, unbound afterwards — so nothing a test
    registers can reach another test."""
    state = gov.TurnGovernanceState()
    with gov.use_turn_state(state):
        yield state


@pytest.fixture
def quiet_governance(monkeypatch):
    """Record-only spoke governance, no hard floors: the sinks are under test."""
    import prax.settings as prax_settings
    from prax.agent import hard_floors
    monkeypatch.setattr(hard_floors, "enabled", lambda: False)
    monkeypatch.setattr(prax_settings.settings, "spoke_governance_enabled", False)
    filled.clear()


@pytest.fixture
def teamwork(monkeypatch):
    import prax.services.teamwork_hooks as hooks
    sent: list[str] = []
    monkeypatch.setattr(hooks, "push_live_output", lambda agent, text, **kw: sent.append(str(text)))
    monkeypatch.setattr(hooks, "log_activity", lambda agent, kind, text, *a, **kw: sent.append(str(text)))
    monkeypatch.setattr(hooks, "set_role_status", lambda *a, **kw: None)
    monkeypatch.setattr(hooks, "post_to_channel", lambda *a, **kw: sent.append(str(a)))
    return sent


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_a_credential_tools_secret_fields_are_registered(self, turn):
        turn_secrets.register_from_output("browser_login", f"username={USERNAME}\npassword={SECRET}")
        assert turn_secrets.scrub(f"typed {SECRET} into #pw") == "typed *** into #pw"
        assert turn_secrets.scrub(USERNAME) == USERNAME          # a username is not a secret

    @pytest.mark.parametrize("line", [
        "password=a:b=c, d", "Password: a:b=c, d", "api_token = a:b=c, d",
        "Recovery code: a:b=c, d", "totp_secret=a:b=c, d",
    ])
    def test_field_formats(self, turn, line):
        turn_secrets.register_from_output("browser_credentials", f"intro\n{line}\nmore")
        assert turn_secrets.scrub("x a:b=c, d y") == "x *** y"   # the whole value, to end of line

    def test_an_ordinary_tools_output_is_not_registered(self, turn):
        turn_secrets.register_from_output("browser_read_page", f"password: {SECRET}")
        assert turn_secrets.scrub(SECRET) == SECRET

    def test_short_values_are_not_registered(self, turn):
        turn_secrets.register_from_output("browser_login", "password=abc")
        turn_secrets.register("xyz", "  ab  ", None)
        assert turn.secrets == set()
        assert turn_secrets.scrub("abc xyz ab") == "abc xyz ab"
        turn_secrets.register("abcd")                            # MIN_LENGTH is 4
        assert turn_secrets.scrub("abcd") == "***"

    def test_escaped_forms_are_masked_too(self, turn):
        tricky = 'pa\'ss\\wd"é'
        turn_secrets.register(tricky)
        for rendered in (str({"text": tricky}), json.dumps({"text": tricky}),
                         json.dumps({"text": tricky}, ensure_ascii=False)):
            assert turn_secrets.scrub(rendered).count("***") == 1, rendered

    def test_longest_value_is_masked_whole(self, turn):
        turn_secrets.register("abcd", "abcdefgh")
        assert turn_secrets.scrub("abcdefgh abcd") == "*** ***"

    def test_outside_a_turn_nothing_is_masked_and_no_state_is_made(self):
        import contextvars

        def _probe():
            # An empty context, whatever earlier tests left in this thread's.
            return turn_secrets.scrub(SECRET), gov.peek_turn_state()
        assert contextvars.Context().run(_probe) == (SECRET, None)


class TestBrowserToolsRegisterAtTheSource:
    @pytest.fixture
    def stored(self, monkeypatch):
        import prax.settings as prax_settings
        from prax.services import browser_service
        monkeypatch.setattr(prax_settings.settings, "browser_secrets_out_of_context", False)
        monkeypatch.setattr(browser_service, "get_credentials", lambda d: {
            "domain": d, "username": USERNAME, "password": SECRET, "totp_secret": "JBSWY3DPEHPK"})

    def test_browser_credentials_comma_joined_secret_fields(self, turn, stored):
        from prax.agent import browser_tools
        out = browser_tools.browser_credentials.func(domain=SITE)
        assert "totp_secret: JBSWY3DPEHPK" in out             # the model's view is unchanged
        assert turn_secrets.scrub(out).count("JBSWY3DPEHPK") == 0
        assert turn_secrets.scrub(SECRET) == "***"           # the password it read, too
        assert turn_secrets.scrub(USERNAME) == USERNAME

    def test_browser_login(self, turn, stored):
        from prax.agent import browser_tools
        assert SECRET in browser_tools.browser_login.func(domain=SITE)
        assert turn_secrets.scrub(SECRET) == "***"


# ---------------------------------------------------------------------------
# Turn scope
# ---------------------------------------------------------------------------

class TestTurnScope:
    def test_a_value_from_one_turn_is_not_kept_in_the_next(self):
        with gov.use_turn_state(gov.TurnGovernanceState()) as first:
            turn_secrets.register(SECRET)
            assert turn_secrets.scrub(SECRET) == "***"
        with gov.use_turn_state(gov.TurnGovernanceState()) as second:
            assert turn_secrets.scrub(SECRET) == SECRET
            assert second.secrets == set()
        assert SECRET in first.secrets  # unbound with its turn, not shared

    def test_begin_turn_starts_empty(self, turn):
        turn_secrets.register(SECRET)
        import contextvars

        def _next_turn():
            gov.begin_turn()
            return turn_secrets.scrub(SECRET)
        assert contextvars.copy_context().run(_next_turn) == SECRET

    def test_draining_the_audit_log_keeps_them(self, turn):
        """A timed-out or stopped turn's worker can still run tools after the
        turn-end drain, on this same state; its calls must stay masked."""
        turn_secrets.register(SECRET)
        gov.drain_audit_log()
        assert turn_secrets.scrub(f"text={SECRET}") == "text=***"

    def test_the_next_turn_starts_without_them(self, turn):
        turn_secrets.register(SECRET)
        gov.begin_turn()
        assert turn_secrets.scrub(SECRET) == SECRET

    def test_the_state_repr_never_shows_them(self, turn):
        turn_secrets.register(SECRET)
        assert SECRET not in repr(turn)

    def test_another_users_turn_never_sees_them(self, turn):
        turn_secrets.register(SECRET)
        with gov.use_turn_state(gov.TurnGovernanceState()):
            assert turn_secrets.scrub(SECRET) == SECRET
        assert turn_secrets.scrub(SECRET) == "***"

    def test_scrubber_keeps_the_values_for_the_trace_write(self, turn):
        turn_secrets.register(SECRET)
        mask = turn_secrets.scrubber()
        gov.drain_audit_log()
        assert mask(f"a {SECRET}") == "a ***"
        assert turn_secrets.scrub(SECRET) == "***"     # the drain no longer clears them


# ---------------------------------------------------------------------------
# The audit log (-> trace.log)
# ---------------------------------------------------------------------------

class TestAuditLog:
    def test_a_spoke_wrapped_login_leaves_no_password_in_the_audit(self, turn, quiet_governance):
        (login,) = gov.govern_spoke_tools([browser_login])
        out = login.invoke({"domain": SITE})
        assert SECRET in out                           # the model still gets it
        audit = gov.drain_audit_log()
        assert audit, "the call was not audited"
        assert SECRET not in repr(audit)
        (entry,) = audit
        assert entry["result"] == WITHHELD_OUTPUT
        assert entry["args"] == f"{{'domain': '{SITE}'}}"   # which site: the audit's question

    def test_the_password_passed_on_to_browser_fill_is_masked(self, turn, quiet_governance):
        login, fill = gov.govern_spoke_tools([browser_login, browser_fill])
        login.invoke({"domain": SITE})
        fill.invoke({"selector": "#pw", "text": SECRET})
        assert filled == [SECRET]                      # the page got the real value
        audit = gov.drain_audit_log()
        assert SECRET not in repr(audit)
        assert audit[1]["args"] == "{'selector': '#pw', 'text': '***'}"

    def test_a_credential_tools_exception_message_is_withheld(self, turn, quiet_governance):
        @tool
        def browser_credentials(domain: str) -> str:
            """Raises with the secret in its message."""
            raise RuntimeError(f"bad entry: password={SECRET}")

        (creds,) = gov.govern_spoke_tools([browser_credentials])
        with pytest.raises(RuntimeError):
            creds.invoke({"domain": SITE})
        (entry,) = gov.drain_audit_log()
        assert entry["result"] == f"ERROR: RuntimeError ({WITHHELD_OUTPUT})"

    def test_a_governance_verdict_on_a_credential_tool_is_kept(self, turn):
        from prax.agent.action_policy import RiskLevel, log_action
        entry = log_action("browser_login", RiskLevel.HIGH, {"domain": SITE},
                           result="REFUSED — hard floor, no person's decision", from_tool=False)
        assert entry["result"] == "REFUSED — hard floor, no person's decision"
        assert entry["args"] == f"{{'domain': '{SITE}'}}"

    def test_masked_before_truncation(self, turn):
        from prax.agent.action_policy import RiskLevel, log_action
        turn_secrets.register(SECRET)
        # The secret straddles the 200-char cut: no prefix of it may survive.
        entry = log_action("say", RiskLevel.LOW, {"text": "x" * 190 + SECRET},
                           result="y" * 195 + SECRET)
        assert SECRET[:4] not in entry["args"] and SECRET[:4] not in entry["result"]


# ---------------------------------------------------------------------------
# Every sink, through a real agent loop
# ---------------------------------------------------------------------------

class _ScriptedLLM(BaseChatModel):
    responses: list = Field(default_factory=list)
    counter: list = Field(default_factory=lambda: [0])

    @property
    def _llm_type(self) -> str:
        return "scripted-turn-secrets-test"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        idx = self.counter[0]
        self.counter[0] = idx + 1
        msg = self.responses[idx] if idx < len(self.responses) else AIMessage(content="end")
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools: Any, **kwargs: Any) -> _ScriptedLLM:
        return self


class _FakeSpan:
    def __init__(self, name, attributes):
        self.name, self.attributes = name, dict(attributes or {})

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def end(self):
        pass


class _FakeTracer:
    def __init__(self):
        self.spans: list[_FakeSpan] = []

    def start_span(self, name, attributes=None):
        self.spans.append(_FakeSpan(name, attributes))
        return self.spans[-1]


def test_login_then_fill_reaches_no_sink(turn, quiet_governance, teamwork, monkeypatch, caplog):
    import prax.agent.orchestrator as orch
    import prax.observability.callbacks as cb_mod
    from prax.agent.agent_loop import build_agent_loop
    from prax.agent.spokes._runner import _log_tool_calls
    from prax.agent.trace import ExecutionGraph, GraphCallbackHandler, TraceHeartbeat

    tracer = _FakeTracer()
    monkeypatch.setattr(cb_mod, "_get_tracer", lambda: tracer)
    otel = cb_mod.OTelToolCallback()
    otel.raise_error = True
    graph = ExecutionGraph("t-secrets")
    running: list[str] = []  # every node's summary as it was added (the running node)
    add_node = graph.add_node
    monkeypatch.setattr(graph, "add_node", lambda node: (running.append(node.summary), add_node(node))[1])
    handler = GraphCallbackHandler(parent_span_id="root", graph=graph, trace_id="t-secrets",
                                   live_agent_name="Browser Agent",
                                   heartbeat=TraceHeartbeat("t-secrets"))
    handler.raise_error = True
    written: list[dict] = []
    monkeypatch.setattr(orch, "append_trace", lambda uid, entries: written.extend(entries))

    llm = _ScriptedLLM(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "browser_login", "args": {"domain": SITE}, "id": "tc1"}]),
        AIMessage(content="", tool_calls=[
            {"name": "browser_fill", "args": {"selector": "#pw", "text": SECRET}, "id": "tc2"}]),
        AIMessage(content="Logged in."),
    ])
    with caplog.at_level(logging.DEBUG):
        out = build_agent_loop(llm, gov.govern_spoke_tools([browser_login, browser_fill])).invoke(
            {"messages": [HumanMessage("log in")]}, config={"callbacks": [otel, handler]})
        _log_tool_calls(out, "browser", role_name="Browser Agent")   # what run_spoke does next
        orch.ConversationAgent._write_trace("u1", "log in", out["messages"])

    assert filled == [SECRET]                                  # the tool got the real value
    fill_span = next(s for s in tracer.spans if s.name == "tool.browser_fill")
    assert fill_span.attributes["prax.tool.input_preview"] == "{'selector': '#pw', 'text': '***'}"
    sinks = {
        "otel": repr([s.attributes for s in tracer.spans]),
        "trace running node": repr(running),
        "trace graph": repr(graph.to_dict()),
        "teamwork": "".join(teamwork),
        "logs": caplog.text,
        "trace.log": repr(written),
    }
    leaked = [name for name, text in sinks.items() if SECRET in text]
    assert not leaked, f"password reached: {leaked}"
    assert "Tool browser_fill starting" in caplog.text           # the INFO log ran, masked
    assert any("browser_fill" in str(e.get("content")) for e in written)


# ---------------------------------------------------------------------------
# The sub-agent's tool log goes through the same redaction
# ---------------------------------------------------------------------------

def test_subagent_tool_log_withholds_and_masks(turn, teamwork, monkeypatch, caplog):
    from prax.agent import subagent

    turn_secrets.register(SECRET)
    messages = [
        AIMessage(content="", tool_calls=[
            {"name": "browser_login", "args": {"domain": SITE}, "id": "tc1"},
            {"name": "browser_fill", "args": {"selector": "#pw", "text": SECRET}, "id": "tc2"}]),
        ToolMessage(content=f"username={USERNAME}\npassword={SECRET}", name="browser_login",
                    tool_call_id="tc1"),
        ToolMessage(content="Filled '#pw' with text.", name="browser_fill", tool_call_id="tc2"),
        AIMessage(content="done"),
    ]

    class _Graph:
        def invoke(self, inputs, config=None):
            return {"messages": messages}

    monkeypatch.setattr(subagent, "build_agent_loop", lambda llm, tools, **kw: _Graph())
    monkeypatch.setattr(subagent, "build_llm", lambda **kw: object())
    monkeypatch.setattr(subagent, "_get_tools_for_category", lambda category: [browser_fill])
    monkeypatch.setattr(subagent, "_auto_advance_plan", lambda: None)
    with caplog.at_level(logging.DEBUG):
        assert subagent._run_subagent("log in", "workspace") == "done"
    shown = caplog.text + "".join(teamwork)
    assert SECRET not in shown
    assert f"browser_login: {WITHHELD_OUTPUT}" in shown
    assert "browser_fill({'selector': '#pw', 'text': '***'})" in shown
    assert "Sub-agent [workspace] tool:" in caplog.text


def test_running_node_masks_a_registered_value(turn):
    from prax.agent.trace import ExecutionGraph, GraphCallbackHandler, TraceHeartbeat
    turn_secrets.register(SECRET)
    graph = ExecutionGraph("t-run")
    handler = GraphCallbackHandler(parent_span_id="root", graph=graph, trace_id="t-run",
                                   heartbeat=TraceHeartbeat("t-run"))
    inputs = {"selector": "#pw", "text": SECRET}
    handler.on_tool_start({"name": "browser_fill"}, str(inputs), run_id=uuid4(), inputs=inputs)
    (node,) = graph._nodes.values()
    assert node.summary == "{'selector': '#pw', 'text': '***'}"


class TestOutcomesOfCredentialToolsThatReturnNoSecret:
    def test_a_fill_login_outcome_stays_in_the_audit(self, turn):
        """browser_fill_login types the password itself and reports what it did;
        "refused: not https" is exactly what the audit is for."""
        from prax.agent.action_policy import RiskLevel, log_action
        turn_secrets.register(SECRET)
        entry = log_action("browser_fill_login", RiskLevel.HIGH, {"domain": SITE},
                           result=f"refused: {SITE} is not https (typed {SECRET}?)")
        assert entry["result"].startswith(f"refused: {SITE} is not https")
        assert SECRET not in entry["result"]
        assert entry["args"] == f"{{'domain': '{SITE}'}}"

    def test_a_secret_returning_tool_output_is_still_withheld(self, turn):
        from prax.agent.action_policy import RiskLevel, log_action
        entry = log_action("browser_credentials", RiskLevel.HIGH, {"domain": SITE},
                           result=f"user: tj, password: {SECRET}")
        assert entry["result"] == WITHHELD_OUTPUT



class TestSpokeAnswers:
    """A spoke that echoes a password it was handed must not post it to
    TeamWork, the trace or the logs — the model still gets the answer."""

    def test_a_spoke_answer_is_scrubbed_at_its_sinks(self, turn, monkeypatch, caplog):
        from prax.agent.spokes import _runner

        class _Graph:
            def invoke(self, inputs, config=None):
                return {"messages": [AIMessage(content=f"Logged in; the password was {SECRET}")]}

        posted, pushed = [], []
        monkeypatch.setattr(_runner, "build_agent_loop", lambda llm, tools, **k: _Graph())
        monkeypatch.setattr(_runner, "build_llm", lambda **k: object())
        import prax.services.teamwork_hooks as hooks
        monkeypatch.setattr(hooks, "post_to_channel", lambda ch, content, agent_name=None: posted.append(content))
        monkeypatch.setattr(hooks, "push_live_output", lambda *a, **k: pushed.append(a[1] if len(a) > 1 else ""))
        monkeypatch.setattr(hooks, "set_role_status", lambda *a, **k: None)
        turn_secrets.register(SECRET)
        with caplog.at_level(logging.INFO):
            answer = _runner.run_spoke(
                task="log in", system_prompt="s", config_key="subagent_test",
                tools=[_tool_named("noop")], role_name="Browser Agent", channel="browser")
        assert SECRET in answer                       # the caller gets it as written
        assert posted and SECRET not in "".join(posted)
        assert SECRET not in "".join(pushed)
        assert SECRET not in caplog.text


def _tool_named(name):
    from langchain_core.tools import StructuredTool
    return StructuredTool.from_function(func=lambda x="": name, name=name, description=name)
