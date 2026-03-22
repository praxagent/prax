"""Tests for scheduler_tools LangChain wrappers."""
import importlib

from prax.agent.user_context import current_user_id


def test_schedule_create(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.scheduler_tools"))
    svc = importlib.import_module("prax.services.scheduler_service")

    monkeypatch.setattr(
        svc, "create_schedule",
        lambda uid, desc, prompt, cron, timezone=None: {
            "status": "created",
            "schedule": {
                "id": "french-vocab-abc123",
                "description": desc,
                "prompt": prompt,
                "cron": cron,
                "timezone": timezone or "America/New_York",
            },
        },
    )
    current_user_id.set("+10000000000")

    result = module.schedule_create.invoke({
        "description": "French vocabulary",
        "prompt": "Send 5 French words",
        "cron": "0 9,11,13,15,17 * * 1-5",
        "timezone": "America/New_York",
    })
    assert "french-vocab" in result
    assert "America/New_York" in result


def test_schedule_create_error(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.scheduler_tools"))
    svc = importlib.import_module("prax.services.scheduler_service")

    monkeypatch.setattr(
        svc, "create_schedule",
        lambda uid, desc, prompt, cron, timezone=None: {"error": "Invalid cron"},
    )
    current_user_id.set("+10000000000")

    result = module.schedule_create.invoke({
        "description": "Bad", "prompt": "p", "cron": "bad",
    })
    assert "Failed" in result


def test_schedule_list(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.scheduler_tools"))
    svc = importlib.import_module("prax.services.scheduler_service")

    monkeypatch.setattr(
        svc, "list_schedules",
        lambda uid: [
            {
                "id": "french-abc",
                "description": "French vocab",
                "prompt": "Send French words",
                "cron": "0 9 * * 1-5",
                "timezone": "America/New_York",
                "enabled": True,
                "next_run": "2026-03-21T09:00:00-04:00",
                "last_run": "2026-03-20T09:00:00-04:00",
            },
        ],
    )
    current_user_id.set("+10000000000")

    result = module.schedule_list.invoke({})
    assert "french-abc" in result
    assert "French vocab" in result
    assert "enabled" in result.lower()


def test_schedule_list_empty(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.scheduler_tools"))
    svc = importlib.import_module("prax.services.scheduler_service")

    monkeypatch.setattr(svc, "list_schedules", lambda uid: [])
    current_user_id.set("+10000000000")

    result = module.schedule_list.invoke({})
    assert "No schedules" in result


def test_schedule_update(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.scheduler_tools"))
    svc = importlib.import_module("prax.services.scheduler_service")

    monkeypatch.setattr(
        svc, "update_schedule",
        lambda uid, sid, **kw: {"status": "updated", "schedule_id": sid, "updates": kw},
    )
    current_user_id.set("+10000000000")

    result = module.schedule_update.invoke({
        "schedule_id": "french-abc",
        "cron": "0 10 * * 1-5",
    })
    assert "updated" in result.lower()


def test_schedule_delete(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.scheduler_tools"))
    svc = importlib.import_module("prax.services.scheduler_service")

    monkeypatch.setattr(
        svc, "delete_schedule",
        lambda uid, sid: {"status": "deleted", "schedule_id": sid},
    )
    current_user_id.set("+10000000000")

    result = module.schedule_delete.invoke({"schedule_id": "french-abc"})
    assert "deleted" in result.lower()


def test_schedule_set_timezone(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.scheduler_tools"))
    svc = importlib.import_module("prax.services.scheduler_service")

    monkeypatch.setattr(
        svc, "set_user_timezone",
        lambda uid, tz: {"status": "updated", "timezone": tz},
    )
    current_user_id.set("+10000000000")

    result = module.schedule_set_timezone.invoke({"timezone": "America/New_York"})
    assert "America/New_York" in result


def test_schedule_reload(monkeypatch):
    module = importlib.reload(importlib.import_module("prax.agent.scheduler_tools"))
    svc = importlib.import_module("prax.services.scheduler_service")

    monkeypatch.setattr(
        svc, "reload_schedules",
        lambda uid: {"status": "reloaded", "count": 3, "timezone": "America/Chicago"},
    )
    current_user_id.set("+10000000000")

    result = module.schedule_reload.invoke({})
    assert "3" in result
    assert "Chicago" in result
