"""Prax's side of the sandbox egress gate: questions go to a person, taint follows turns."""
from __future__ import annotations

import pytest
from langchain_core.tools import StructuredTool

import prax.agent.governed_tool as gov
import prax.settings as prax_settings
from prax.services import approval_service
from prax.services import egress_gate_service as egs


@pytest.fixture
def gate(monkeypatch):
    calls = []
    monkeypatch.setattr(prax_settings.settings, "egress_gate_url", "http://127.0.0.1:8790")
    monkeypatch.setattr(prax_settings.settings, "egress_gate_token", "t")
    monkeypatch.setattr(egs, "_call", lambda method, path, body=None: calls.append((method, path, body)) or {"pending": []})
    # Send taint synchronously so the test can see it.
    monkeypatch.setattr(egs, "_send_taint", lambda tainted, reason: calls.append(("TAINT", tainted, reason)))
    egs._tainted_turns.clear()
    yield calls
    egs._tainted_turns.clear()


def test_unconfigured_is_a_no_op(monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "egress_gate_url", "")
    assert egs.start() is False
    egs.mark_tainted(1, "x")  # must not raise or send
    assert egs._tainted_turns == {}


def test_taint_holds_until_the_last_tainted_turn_ends(gate):
    egs.mark_tainted(1, "turn one read memory")
    egs.mark_tainted(2, "turn two ran code")
    egs.release(1)
    egs.release(2)
    taints = [c for c in gate if c[0] == "TAINT"]
    assert taints == [("TAINT", True, "turn one read memory"), ("TAINT", False, "")]


def test_a_persons_answer_goes_back_to_the_gate(gate, monkeypatch):
    asked = []
    monkeypatch.setattr(approval_service, "ask_and_wait",
                        lambda cap, payload, **kw: asked.append((cap, payload)) or approval_service.Outcome("approved", "a1"))
    assert egs.answer({"id": "7", "host": "example.net", "port": 443, "tainted": True}) is True
    assert asked[0][0] == "prax.egress.example.net" and asked[0][1]["tainted"] is True
    assert ("POST", "/pending/7", {"allow": True, "by": "a person in TeamWork"}) in gate


@pytest.mark.parametrize("status", ["rejected", "pending", "unavailable", "error"])
def test_anything_but_approval_is_a_deny(gate, monkeypatch, status):
    monkeypatch.setattr(approval_service, "ask_and_wait",
                        lambda *a, **k: approval_service.Outcome(status))
    assert egs.answer({"id": "8", "host": "x.example", "port": 443}) is False
    assert any(c[:2] == ("POST", "/pending/8") and c[2]["allow"] is False for c in gate)


def test_sandbox_exec_taints_the_gate_and_turn_end_releases_it(gate):
    def sandbox_shell(command: str = "") -> str:
        return "ok"
    gov.drain_audit_log()
    tool = gov.wrap_with_governance(
        StructuredTool.from_function(func=sandbox_shell, name="sandbox_shell", description="t"),
        layer="spoke", enforce=False)
    tool.invoke({"command": "cat /workspace/notes.md"})
    tool.invoke({"command": "ls"})  # once per turn
    assert [c for c in gate if c[0] == "TAINT"] == [("TAINT", True, "sandbox_shell ran code over the workspace")]
    gov.drain_audit_log()  # end of turn
    assert [c for c in gate if c[0] == "TAINT"][-1] == ("TAINT", False, "")
