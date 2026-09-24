"""OUT_OF_BAND_APPROVALS_ENABLED: a person approves, and the model cannot.

Before, the HIGH-risk and lethal-trifecta gates told the model to "call again
with the same arguments", and the second call ran — the model confirmed itself
(July review #8). With the flag on, the call waits on an approval that only a
person can grant in the TeamWork UI, bound to the exact arguments.
"""
from __future__ import annotations

import pytest
from langchain_core.tools import StructuredTool

import prax.agent.governed_tool as gov
import prax.settings as prax_settings


class FakeTeamWork:
    """A TeamWork that answers every request with *answers* in turn."""

    def __init__(self, answers=("approved",), enabled=True, project_id="p1"):
        self.enabled = enabled
        self.project_id = project_id
        self.answers = list(answers)
        self.asked: list[dict] = []
        self.consumed: list[dict] = []

    def ask_approval(self, capability, payload, reason="", project_id=None):
        self.asked.append({"capability": capability, "payload": payload})
        return {"approval_id": f"a{len(self.asked)}", "status": "pending", "expired": False}

    def approval_status(self, approval_id):
        status = self.answers.pop(0) if self.answers else "pending"
        return {"approval_id": approval_id, "status": status, "expired": False}

    def consume_approval(self, approval_id, capability, payload, project_id=None):
        self.consumed.append({"id": approval_id, "capability": capability, "payload": payload})
        return {"status": "consumed"}


@pytest.fixture
def ran():
    return []


@pytest.fixture
def tool(ran):
    def plugin_write(x: str = "") -> str:  # a HIGH-risk tool name
        ran.append(x)
        return f"executed:{x}"
    gov.drain_audit_log()
    yield gov.wrap_with_governance(
        StructuredTool.from_function(func=plugin_write, name="plugin_write", description="t"))
    gov.drain_audit_log()


@pytest.fixture
def teamwork(monkeypatch):
    fake = FakeTeamWork()
    monkeypatch.setattr("prax.services.teamwork_service.get_teamwork_client", lambda: fake)
    monkeypatch.setattr("prax.services.approval_service.POLL_SECONDS", 0.0)
    monkeypatch.setattr(prax_settings.settings, "out_of_band_approvals_enabled", True)
    return fake


def test_flag_off_keeps_the_old_call_again_gate(tool, ran, monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "out_of_band_approvals_enabled", False)
    assert "call plugin_write again" in tool.invoke({"x": "a"})
    assert tool.invoke({"x": "a"}) == "executed:a"  # the self-confirm the flag removes


def test_a_person_approves_and_the_call_runs_once(tool, ran, teamwork):
    assert tool.invoke({"x": "a"}) == "executed:a"
    assert ran == ["a"]
    assert len(teamwork.asked) == 1 and teamwork.asked[0]["capability"] == "prax.tool.plugin_write"
    # Spent on exactly what was asked.
    assert teamwork.consumed[0]["payload"] == teamwork.asked[0]["payload"]


def test_denial_cannot_be_retried_past(tool, ran, teamwork):
    teamwork.answers = ["rejected", "rejected"]
    first = tool.invoke({"x": "a"})
    second = tool.invoke({"x": "a"})  # the old self-confirm move
    assert "DENIED" in first and "DENIED" in second
    assert ran == []
    assert len(teamwork.asked) == 2  # each attempt goes back to the person


def test_no_teamwork_fails_closed(tool, ran, teamwork):
    teamwork.enabled = False
    out = tool.invoke({"x": "a"})
    assert out.startswith("⛔") and ran == []


def test_unanswered_request_times_out_refused(tool, ran, teamwork, monkeypatch):
    teamwork.answers = []  # stays pending
    monkeypatch.setattr(prax_settings.settings, "approval_wait_seconds", 5)
    t = [0.0]
    monkeypatch.setattr("prax.services.approval_service.time.monotonic", lambda: t.__setitem__(0, t[0] + 3) or t[0])
    out = tool.invoke({"x": "a"})
    assert "no answer" in out and ran == []


def test_approval_is_bound_to_the_exact_arguments(tool, ran, teamwork):
    teamwork.answers = ["approved", "rejected"]
    assert tool.invoke({"x": "a"}) == "executed:a"
    # Same tool, different arguments: a fresh question, which the person refuses.
    assert "DENIED" in tool.invoke({"x": "b"})
    assert ran == ["a"]
    assert teamwork.asked[0]["payload"]["args_sha256"] != teamwork.asked[1]["payload"]["args_sha256"]


def test_a_high_risk_trifecta_sink_asks_once(ran, teamwork, monkeypatch):
    def send_email(x: str = "") -> str:
        ran.append(x)
        return "sent"
    gov.drain_audit_log()
    monkeypatch.setattr("prax.agent.trifecta.trifecta_guard_enabled", lambda: True)
    monkeypatch.setattr("prax.agent.trifecta.should_escalate_sink", lambda *a, **k: True)
    monkeypatch.setattr(gov, "get_risk_level", lambda name: gov.RiskLevel.HIGH, raising=False)
    tool = gov.wrap_with_governance(
        StructuredTool.from_function(func=send_email, name="send_email", description="t"))
    assert tool.invoke({"x": "a"}) == "sent"
    assert len(teamwork.asked) == 1
    assert teamwork.asked[0]["payload"]["kind"] == "lethal_trifecta"
    gov.drain_audit_log()


def test_a_vanished_request_fails_fast_instead_of_polling_to_the_deadline(tool, ran, teamwork):
    import requests

    def gone(approval_id):
        resp = requests.Response()
        resp.status_code = 404
        raise requests.HTTPError("404", response=resp)
    teamwork.approval_status = gone
    out = tool.invoke({"x": "a"})
    assert out.startswith("⛔") and "404" in out and ran == []
