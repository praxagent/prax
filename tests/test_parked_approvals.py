"""Parked approvals: an unattended run waits for a person instead of giving up.

Pattern credit: OpenWorker (Andrew Ng et al.) — unattended runs never
self-approve; requests park in an inbox.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import prax.settings as prax_settings
from prax.agent import human_approval
from prax.agent.user_context import (
    capture_user_context,
    current_turn_source,
    current_user_id,
    use_user_context,
)
from prax.services import parked_approvals as pa
from prax.services.approval_service import Outcome


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(prax_settings.settings, "workspace_dir", str(tmp_path))
    monkeypatch.setattr(prax_settings.settings, "parked_approvals_enabled", True)
    monkeypatch.setattr(prax_settings.settings, "parked_approval_hours", 12)
    monkeypatch.setattr(prax_settings.settings, "parked_max_resumes", 2)
    monkeypatch.setattr(prax_settings.settings, "approval_wait_seconds", 300)
    asks, spends = [], []

    def ask(capability, payload, *, reason, wait_seconds, expires_in_seconds=None, **_):
        asks.append({"cap": capability, "wait": wait_seconds, "expires_in": expires_in_seconds})
        return env.outcome
    env = SimpleNamespace(asks=asks, spends=spends,
                          outcome=Outcome("pending", "ap1", "no answer", expires_at="2026-10-01T12:00:00"))
    monkeypatch.setattr("prax.services.approval_service.ask_and_wait", ask)
    monkeypatch.setattr("prax.services.approval_service.spend",
                        lambda aid, cap, payload: spends.append(aid) or env.spend_ok)
    env.spend_ok = True
    tokens = [current_user_id.set("u1")]
    yield env
    for t in tokens:
        current_user_id.reset(t)


def _unattended(recipe=None, source="scheduler"):
    return (current_turn_source.set(source),
            pa.current_recipe.set(recipe if recipe is not None else
                                  {"kind": "schedule", "resumes": 0,
                                   "args": {"schedule_id": "s1", "prompt": "p", "channel": "discord"}}))


def _reset(tokens):
    current_turn_source.reset(tokens[0])
    pa.current_recipe.reset(tokens[1])


def _request():
    return human_approval.request("plugin_import", {"name": "weather"}, kind="hard_floor",
                                  reason="r", summary="s")


def test_an_unattended_run_parks_instead_of_waiting(env):
    t = _unattended()
    try:
        d = _request()
    finally:
        _reset(t)
    assert not d.approved and d.message.startswith("PARKED — plugin_import needs the user's approval")
    assert env.asks == [{"cap": "prax.tool.plugin_import", "wait": 1, "expires_in": 12 * 3600}]
    (entry,) = pa.pending()
    assert entry["approval_id"] == "ap1" and entry["recipe"]["kind"] == "schedule"
    assert entry["channel"] == "discord"
    # naive UTC from TeamWork, read as UTC — not local time
    assert entry["expires_at"] == pytest.approx(1790856000.0)


def test_an_attended_turn_still_waits_as_before(env):
    t = current_turn_source.set("teamwork")
    try:
        _request()
    finally:
        current_turn_source.reset(t)
    assert env.asks[0]["wait"] == 300 and pa.pending() == []


def test_off_by_default(env, monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "parked_approvals_enabled", False)
    t = _unattended()
    try:
        _request()
    finally:
        _reset(t)
    assert env.asks[0]["wait"] == 300 and pa.pending() == []


def test_a_rerun_spends_the_approval_for_that_exact_action(env):
    payload = human_approval.action_payload("plugin_import", {"name": "weather"}, "hard_floor", "s")
    key = pa.action_key("prax.tool.plugin_import", payload)
    t = pa.current_preapproved.set({key: {"approval_id": "ap1", "decided_by": "tj"}})
    try:
        d = _request()
        other = human_approval.request("plugin_import", {"name": "crypto-miner"},
                                       kind="hard_floor", reason="r", summary="s")
    finally:
        pa.current_preapproved.reset(t)
    assert d.approved and d.approval_id == "ap1" and d.decided_by == "tj"
    assert env.spends == ["ap1"]
    assert not other.approved  # a different action asks again


def test_resumes_are_capped(env):
    t = _unattended({"kind": "schedule", "resumes": 2, "args": {}})
    try:
        d = _request()
    finally:
        _reset(t)
    assert not d.approved and "already been resumed 2 times" in d.message
    assert env.asks == []


def test_the_recipe_travels_in_the_context_snapshot(env):
    t = _unattended()
    try:
        snap = capture_user_context()
    finally:
        _reset(t)
    assert pa.current_recipe.get() is None
    with use_user_context(snap):
        assert pa.current_recipe.get()["kind"] == "schedule"
    assert pa.current_recipe.get() is None


# --- the poller ---------------------------------------------------------------------

@pytest.fixture
def poller(env, monkeypatch):
    statuses = {}
    notes, resumed = [], []
    client = SimpleNamespace(enabled=True, approval_status=lambda aid: statuses[aid])
    monkeypatch.setattr("prax.services.teamwork_service.get_teamwork_client", lambda: client)
    monkeypatch.setattr(pa, "_notify", lambda entry, text: notes.append((entry["approval_id"], text)))
    monkeypatch.setattr(pa, "_resume", lambda entry, by: resumed.append((entry["approval_id"], by)))
    return SimpleNamespace(statuses=statuses, notes=notes, resumed=resumed)


def _parked(aid, expires_in=3600):
    return pa.park(user_id="u1", approval_id=aid, tool_name="plugin_import",
                   capability="prax.tool.plugin_import", payload={"x": 1},
                   recipe={"kind": "schedule", "args": {}}, channel="discord",
                   expires_at=time.time() + expires_in)


def test_poller_resumes_on_approval_and_reports_the_rest(poller):
    for aid in ("yes", "no", "wait", "late"):
        _parked(aid, expires_in=-1 if aid == "late" else 3600)
    poller.statuses.update({
        "yes": {"status": "approved", "decided_by": "tj"},
        "no": {"status": "rejected"},
        "wait": {"status": "pending", "expired": False},
        "late": {"status": "pending", "expired": False},
    })
    pa.tick()
    assert poller.resumed == [("yes", "tj")]
    notes = dict(poller.notes)
    assert "declined" in notes["no"] and "expired" in notes["late"]
    assert [e["approval_id"] for e in pa.pending()] == ["wait"]


def test_the_store_survives_a_restart(poller):
    _parked("keep")
    assert [e["approval_id"] for e in pa._load()] == ["keep"]


def test_a_parked_floor_action_tells_the_model_it_is_waiting(env, monkeypatch):
    from langchain_core.tools import StructuredTool

    import prax.agent.governed_tool as gov

    monkeypatch.setattr(prax_settings.settings, "hard_floors_enabled", True)
    monkeypatch.setattr(prax_settings.settings, "out_of_band_approvals_enabled", True)
    gov.begin_turn()
    t = _unattended()
    try:
        tool = gov.wrap_with_governance(
            StructuredTool.from_function(func=lambda name="": "ran", name="plugin_import", description="t"),
            layer="spoke", enforce=False)
        out = tool.invoke({"name": "weather"})
    finally:
        _reset(t)
        gov.drain_audit_log()
    assert out.startswith("PARKED — plugin_import needs the user's approval")
    assert len(pa.pending()) == 1
