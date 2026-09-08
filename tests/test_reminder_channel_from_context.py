"""A reminder must fire where the user asked for it — and never vanish unsent.

Review finding: ``scheduler_service._infer_channel`` classified the user id by
string SHAPE (a leading digit meant "phone") and could never answer
``teamwork``.  A TeamWork user with no phone who asked for a reminder got an
entry routed to ``sms``; at fire time there was no number, nothing was logged
(the "no address" warning only fired for channel="all"), and the reminder was
deleted as if delivered.

Now: the creating turn's channel is persisted on the reminder, legacy ids are
classified only when they are fully numeric or ``D<digits>``, a selected
channel with no address always warns, and a reminder whose delivery produced
no send is kept and marked instead of deleted.

How the channel reaches the tool (the part the first version of this fix got
wrong): ``ConversationService.reply`` sets
``prax.agent.user_context.current_turn_source`` on the REQUEST thread, but the
orchestrator runs the graph on a fresh ``threading.Thread`` and a spoke's
tools may run on yet another.  ContextVars do not cross threads; the only thing
that does is ``UserContextSnapshot`` — captured on the request thread by the
governed ``delegate_*`` wrapper, restored inside the worker around the
delegate call, re-captured by ``govern_spoke_tools`` when the spoke builds its
tools, and restored again around each tool call.  ``current_turn_source`` is
therefore a snapshot field; a same-thread test cannot prove that, so
``test_tool_sees_the_turn_source_across_the_worker_threads`` re-enacts the
hops.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

import prax.services.identity_service as ids
from prax.services import scheduler_service as sched
from prax.services import workspace_service


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fresh identity DB + workspace root, no running APScheduler."""
    monkeypatch.setattr(ids, "_db_path", lambda: str(tmp_path / "identity.db"))
    monkeypatch.setattr(ids, "_initialized", False)
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(sched.settings, "workspace_dir", str(ws))
    monkeypatch.setattr(sched.settings, "prax_user_id", "")
    monkeypatch.setattr(sched.settings, "teamwork_user_phone", "")
    monkeypatch.setattr(sched.settings, "teamwork_url", "")  # teamwork_active → False
    monkeypatch.setattr(sched, "_scheduler", None)
    sched._user_jobs.clear()
    ids.init_identity_db()
    return ws


@pytest.fixture
def teamwork_user(env):
    """A canonical user whose ONLY identity is the TeamWork UI — no phone, no Discord."""
    return ids.resolve_user("teamwork", "default")


def _reminders(user_id: str) -> list[dict]:
    path = Path(workspace_service.workspace_root(user_id)) / "schedules.yaml"
    if not path.exists():
        return []
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("reminders", [])


def _write_reminder(user_id: str, channel: str | None) -> str:
    root = Path(workspace_service.workspace_root(user_id))
    root.mkdir(parents=True, exist_ok=True)
    entry = {"id": "rem-test-000001", "description": "test", "prompt": "drink water",
             "fire_at": "2030-01-01T10:00:00+00:00", "timezone": "UTC"}
    if channel:
        entry["channel"] = channel
    (root / "schedules.yaml").write_text(
        yaml.safe_dump({"timezone": "UTC", "schedules": [], "reminders": [entry]}),
        encoding="utf-8")
    return entry["id"]


# ── Classification ───────────────────────────────────────────────────────────

def test_legacy_ids_are_classified_only_when_fully_numeric_or_D_digits(env):
    assert sched._infer_channel("+15551234567") == "sms"
    assert sched._infer_channel("15551234567") == "sms"
    assert sched._infer_channel("D1034618247871483964") == "discord"
    assert sched._is_legacy_id("90c2b48f-1234-4abc-8def-000000000001") is False
    assert sched._is_legacy_id("Dave") is False
    assert sched._is_legacy_id("usr_90c2b48f") is False


def test_teamwork_only_user_infers_teamwork(teamwork_user):
    # Old code: identities without "discord" → "sms" (no phone to send to).
    assert sched._infer_channel(teamwork_user.id) == "teamwork"


def test_channel_for_source():
    assert sched._channel_for_source("teamwork") == "teamwork"
    assert sched._channel_for_source("discord") == "discord"
    assert sched._channel_for_source("sms") == "sms"
    assert sched._channel_for_source("voice") == "sms"
    assert sched._channel_for_source("scheduler") is None
    assert sched._channel_for_source("") is None
    assert sched._channel_for_source(None) is None


# ── Creation persists the creating turn's channel ────────────────────────────

def test_create_reminder_records_the_creating_turns_channel(teamwork_user):
    fire_at = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0).isoformat()
    result = sched.create_reminder(teamwork_user.id, "Water", "drink water", fire_at,
                                   source="teamwork")
    assert "error" not in result, result
    assert result["reminder"]["channel"] == "teamwork"
    assert _reminders(teamwork_user.id)[0]["channel"] == "teamwork"


def test_explicit_channel_beats_source_beats_inference(teamwork_user):
    fire_at = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0).isoformat()
    explicit = sched.create_reminder(teamwork_user.id, "A", "p", fire_at,
                                     channel="all", source="discord")
    assert explicit["reminder"]["channel"] == "all"
    inferred = sched.create_reminder(teamwork_user.id, "B", "p", fire_at)
    assert inferred["reminder"]["channel"] == "teamwork"


def _capture_create(monkeypatch) -> dict:
    """Replace ``create_reminder`` with a recorder of its keyword arguments."""
    captured: dict = {}

    def fake_create(uid, description, prompt, fire_at, **kwargs):
        captured.update(kwargs, uid=uid)
        return {"status": "created", "reminder": {
            "id": "rem-x", "description": description, "prompt": prompt,
            "fire_at": fire_at, "timezone": "UTC", "channel": kwargs.get("channel")}}

    monkeypatch.setattr(sched, "create_reminder", fake_create)
    return captured


_REMINDER_ARGS = {"description": "Water", "prompt": "drink", "fire_at": "2030-01-01T10:00:00"}


def test_tool_passes_the_turn_source_through(env, monkeypatch):
    """Same-thread baseline: the tool reads the ContextVar at all."""
    from prax.agent import scheduler_tools
    from prax.agent.user_context import current_turn_source, current_user_id

    captured = _capture_create(monkeypatch)
    current_user_id.set("some-user")
    token = current_turn_source.set("teamwork")
    try:
        scheduler_tools.schedule_reminder.invoke(dict(_REMINDER_ARGS))
    finally:
        current_turn_source.reset(token)
    # Old code: create_reminder was called without any notion of the turn's channel.
    assert captured["source"] == "teamwork"
    assert captured["channel"] is None


def test_tool_sees_the_turn_source_across_the_worker_threads(env, monkeypatch):
    """The production shape, re-enacted thread for thread.

    request thread: reply() sets the var; the governed delegate_* wrapper
                    captures a UserContextSnapshot at build time
    graph worker:   fresh context (ConversationAgent._invoke_graph_once);
                    the delegate wrapper restores the snapshot, and inside
                    that run_spoke builds the spoke's tools via
                    govern_spoke_tools (re-capturing the context)
    tool executor:  fresh context again; the spoke tool's binding wrapper
                    restores what govern_spoke_tools captured

    With the var living outside the snapshot, the value set on the request
    thread never reached the tool: ``captured["source"] == ""`` and
    create_reminder fell back to inferring the channel from the user's
    identities (Discord, on a user with sms+discord+teamwork).  The
    ``source == "teamwork"`` assertion below fails on that code.
    """
    from prax.agent import scheduler_tools
    from prax.agent.governed_tool import govern_spoke_tools
    from prax.agent.user_context import (
        capture_user_context,
        current_turn_source,
        current_user_id,
        use_user_context,
    )

    captured = _capture_create(monkeypatch)
    failures: list[BaseException] = []

    # -- request thread --------------------------------------------------
    uid_token = current_user_id.set("some-user")
    src_token = current_turn_source.set("teamwork")
    try:
        request_snapshot = capture_user_context()
    finally:
        current_turn_source.reset(src_token)
        current_user_id.reset(uid_token)

    def _tool_executor(tool) -> None:
        try:
            assert current_turn_source.get() == "", "a new thread must start blank"
            tool.invoke(dict(_REMINDER_ARGS))
        except BaseException as exc:  # noqa: BLE001 — surfaced on the main thread
            failures.append(exc)

    def _graph_worker() -> None:
        try:
            assert current_turn_source.get() == "", "a new thread must start blank"
            with use_user_context(request_snapshot):
                [tool] = govern_spoke_tools([scheduler_tools.schedule_reminder])
            # The spoke's tool runs after the delegate wrapper's restore has
            # exited and on a thread of its own — only the snapshot the spoke
            # build site captured can carry the value from here.
            t = threading.Thread(target=_tool_executor, args=(tool,))
            t.start()
            t.join(10)
            assert not t.is_alive(), "tool thread hung"
        except BaseException as exc:  # noqa: BLE001 — surfaced on the main thread
            failures.append(exc)

    worker = threading.Thread(target=_graph_worker)
    worker.start()
    worker.join(20)
    assert not worker.is_alive(), "graph worker hung"
    assert not failures, failures

    assert captured["uid"] == "some-user"          # user id already crossed the threads
    assert captured["source"] == "teamwork"        # old code: "" (var not in the snapshot)
    assert captured["channel"] is None


def test_reply_publishes_the_turn_source_for_its_duration(env, monkeypatch):
    from prax.agent.user_context import current_turn_source
    from prax.services import conversation_service as cs

    seen: dict = {}

    class FakeAgent:
        def run(self, **kwargs):
            seen["during"] = current_turn_source.get()
            return "ok"

    monkeypatch.setattr(cs, "get_workspace_context", lambda *_a, **_k: "")
    svc = cs.ConversationService(agent=FakeAgent(), retriever=lambda *_a: [],
                                 saver=lambda *_a: None, database_name="x.db")
    assert current_turn_source.get() == ""
    svc.reply("+10000000000", "hi", source="teamwork")
    assert seen["during"] == "teamwork"
    assert current_turn_source.get() == ""  # reset after the turn


# ── Delivery: warn on a missing address, never delete an unsent reminder ─────

def test_selected_channel_with_no_address_warns_and_reports_no_send(teamwork_user, caplog):
    with caplog.at_level(logging.WARNING, logger="prax.services.scheduler_service"):
        sent = sched._deliver_message(teamwork_user.id, "hi", channel="sms")
    # Old code: returned None and logged nothing unless channel == "all".
    assert sent is False
    assert any("no phone number" in r.getMessage() for r in caplog.records)


def test_teamwork_user_reminder_is_kept_when_nothing_was_sent(teamwork_user, caplog):
    rid = _write_reminder(teamwork_user.id, channel=None)  # legacy entry, no channel recorded

    with caplog.at_level(logging.WARNING, logger="prax.services.scheduler_service"):
        sched._on_reminder_fire(teamwork_user.id, rid, "drink water", channel=None)

    remaining = _reminders(teamwork_user.id)
    # Old code: inferred "sms", found no phone, said nothing, deleted the reminder.
    assert [r["id"] for r in remaining] == [rid]
    assert remaining[0]["delivery_failed_at"]
    assert "no send" in remaining[0]["delivery_error"]
    messages = [r.getMessage() for r in caplog.records]
    assert any("Cannot deliver via TeamWork" in m for m in messages)
    assert any("NOT delivered" in m and rid in m for m in messages)


def test_teamwork_user_reminder_is_delivered_and_then_deleted(teamwork_user, monkeypatch):
    import prax.services.teamwork_hooks as hooks

    posted: list[tuple] = []
    monkeypatch.setattr(hooks, "post_to_channel",
                        lambda channel, content, agent_name=None: posted.append((channel, content)))
    monkeypatch.setattr(sched.settings, "teamwork_url", "http://teamwork.test")
    rid = _write_reminder(teamwork_user.id, channel="teamwork")

    sched._on_reminder_fire(teamwork_user.id, rid, "drink water", channel="teamwork")

    assert posted and posted[0][0] == "general" and "drink water" in posted[0][1]
    assert _reminders(teamwork_user.id) == []


def test_all_channel_counts_a_send_when_any_transport_worked(teamwork_user, monkeypatch, caplog):
    import prax.services.teamwork_hooks as hooks

    monkeypatch.setattr(hooks, "post_to_channel", lambda *a, **k: None)
    monkeypatch.setattr(sched.settings, "teamwork_url", "http://teamwork.test")
    with caplog.at_level(logging.WARNING, logger="prax.services.scheduler_service"):
        assert sched._deliver_message(teamwork_user.id, "hi", channel="all") is True
    messages = [r.getMessage() for r in caplog.records]
    assert any("no Discord ID" in m for m in messages)
    assert any("no phone number" in m for m in messages)
