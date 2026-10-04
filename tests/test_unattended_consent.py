"""Consent read from a message needs a person present.

In a scheduler or task-runner turn the "user message" is a schedule's prompt
or a Kanban card: written earlier, and a card can come from any agent with a
space key. It must never satisfy "the user named it" for a hard floor (which
would make a public link with no person deciding) or auto-approve a HIGH-risk
browser action.

Also: a parked schedule, once a person approved it, re-runs (the resume
imported a function that does not exist, so it never did).
"""
from __future__ import annotations

import threading

import pytest

import prax.agent.governed_tool as gov
from prax.agent.user_context import current_turn_source, current_user_id, current_user_message
from prax.services import share_registry

USER = "usr_test_unattended"


def _live():
    import prax.settings
    return prax.settings.settings


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    from prax.services import workspace_service
    monkeypatch.setattr(workspace_service.settings, "workspace_dir", str(tmp_path))
    monkeypatch.setattr(_live(), "workspace_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(_live(), "out_of_band_approvals_enabled", False)
    import prax.utils.ngrok as ng
    monkeypatch.setattr(ng, "get_ngrok_url", lambda: "https://prax-test.ngrok.example")
    token = current_user_id.set(USER)
    gov.begin_turn()
    workspace_service.save_file(USER, "report.pdf", b"%PDF-1.4 test")
    yield
    gov.drain_audit_log()
    current_user_id.reset(token)


@pytest.fixture
def turn():
    tokens = []

    def _turn(text, source):
        tokens.append((current_user_message, current_user_message.set(text)))
        tokens.append((current_turn_source, current_turn_source.set(source)))
    yield _turn
    for var, tok in reversed(tokens):
        var.reset(tok)


def _share():
    from prax.agent.plugin_tools import workspace_share_file
    return gov.wrap_with_governance(workspace_share_file).invoke({"file_path": "active/report.pdf"})


@pytest.mark.parametrize("source", ["task_runner", "scheduler"])
def test_a_card_or_schedule_naming_the_file_does_not_make_it_public(turn, source):
    turn("share report.pdf publicly", source)
    out = _share()
    assert out.startswith("⛔ Not done")
    assert share_registry.list_all(USER) == []


def test_the_same_words_from_a_person_still_count(turn):
    turn("share report.pdf publicly", "teamwork")
    out = _share()
    assert "File shared" in out and len(share_registry.list_all(USER)) == 1


def test_browser_auto_approval_needs_a_person_present(turn):
    turn("click the Buy button on that page", "teamwork")
    assert gov._user_explicitly_requested_action("browser_click") is True
    turn("click the Buy button on that page", "task_runner")
    assert gov._user_explicitly_requested_action("browser_click") is False


def test_an_approved_parked_schedule_reruns(monkeypatch):
    from prax.services import parked_approvals, scheduler_service
    fired = threading.Event()
    seen = {}

    def fake_fire(user_id, schedule_id, prompt, channel=None):
        seen.update(user_id=user_id, schedule_id=schedule_id, prompt=prompt)
        fired.set()
    monkeypatch.setattr(scheduler_service, "_on_fire", fake_fire)
    entry = {"approval_id": "ap123456789", "user_id": USER, "key": "k",
             "recipe": {"kind": "schedule", "args": {"schedule_id": "s1", "prompt": "daily report"}}}
    parked_approvals._resume(entry, "person:tj")
    assert fired.wait(5), "the approved schedule never re-ran"
    assert seen == {"user_id": USER, "schedule_id": "s1", "prompt": "daily report"}
