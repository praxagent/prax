"""Tests for scheduler_service — APScheduler and conversation interactions are mocked."""
import importlib

import pytest
import yaml


@pytest.fixture()
def sched_mod(monkeypatch, tmp_path):
    """Reload scheduler_service with a temp workspace and no real scheduler."""
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))

    import prax.settings as settings_mod
    importlib.reload(settings_mod)

    module = importlib.reload(
        importlib.import_module("prax.services.scheduler_service")
    )

    # Patch settings on the already-loaded module.
    monkeypatch.setattr(module.settings, "workspace_dir", str(tmp_path))

    # workspace_root() is now called by _schedules_path — patch its settings too.
    import prax.services.workspace_service as ws_mod
    monkeypatch.setattr(ws_mod.settings, "workspace_dir", str(tmp_path))

    # Reset module state.
    module._scheduler = None
    module._user_jobs.clear()

    return module


@pytest.fixture()
def sched_running(sched_mod):
    """Start the scheduler so APScheduler jobs actually get registered."""
    sched_mod.init_scheduler()
    yield sched_mod
    sched_mod.shutdown_scheduler()


# ---------- helpers ---------------------------------------------------------

def _safe_id(user_id):
    return user_id.lstrip("+")


def _read_yaml(tmp_path, user_id):
    with open(tmp_path / _safe_id(user_id) / "schedules.yaml") as f:
        return yaml.safe_load(f)


def _write_yaml(tmp_path, user_id, data):
    d = tmp_path / _safe_id(user_id)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "schedules.yaml", "w") as f:
        yaml.dump(data, f, default_flow_style=False)


# ---------- create ----------------------------------------------------------

class TestCreateSchedule:
    def test_creates_and_persists(self, sched_running, tmp_path):
        result = sched_running.create_schedule(
            "+10000000000", "French vocab", "Send 5 French words",
            "0 9,11,13,15,17 * * 1-5", timezone="America/New_York",
        )
        assert result["status"] == "created"
        s = result["schedule"]
        assert s["description"] == "French vocab"
        assert s["timezone"] == "America/New_York"
        assert s["enabled"] is True
        assert s["id"].startswith("french-vocab-")

        # Verify YAML file was written.
        data = _read_yaml(tmp_path, "+10000000000")
        assert len(data["schedules"]) == 1
        assert data["timezone"] == "America/New_York"  # adopted from first create

    def test_invalid_cron(self, sched_running):
        result = sched_running.create_schedule(
            "+10000000000", "Bad", "prompt", "not a cron",
        )
        assert "error" in result
        assert "5-field" in result["error"]

    def test_invalid_timezone(self, sched_running):
        result = sched_running.create_schedule(
            "+10000000000", "Bad", "prompt", "0 9 * * *", timezone="Fake/Zone",
        )
        assert "error" in result
        assert "timezone" in result["error"].lower()

    def test_multiple_schedules(self, sched_running, tmp_path):
        sched_running.create_schedule(
            "+10000000000", "Morning", "Good morning", "0 8 * * *",
            timezone="America/New_York",
        )
        sched_running.create_schedule(
            "+10000000000", "Evening", "Good night", "0 21 * * *",
        )
        data = _read_yaml(tmp_path, "+10000000000")
        assert len(data["schedules"]) == 2

    def test_adopts_timezone_only_from_utc(self, sched_running, tmp_path):
        # First schedule sets timezone.
        sched_running.create_schedule(
            "+10000000000", "S1", "p1", "0 9 * * *",
            timezone="America/Chicago",
        )
        data = _read_yaml(tmp_path, "+10000000000")
        assert data["timezone"] == "America/Chicago"

        # Second schedule with different tz should NOT change the default.
        sched_running.create_schedule(
            "+10000000000", "S2", "p2", "0 10 * * *",
            timezone="Europe/London",
        )
        data = _read_yaml(tmp_path, "+10000000000")
        assert data["timezone"] == "America/Chicago"  # unchanged


# ---------- list ------------------------------------------------------------

class TestListSchedules:
    def test_empty(self, sched_running):
        result = sched_running.list_schedules("+10000000000")
        assert result == []

    def test_lists_with_next_run(self, sched_running):
        sched_running.create_schedule(
            "+10000000000", "Daily", "Hello", "0 9 * * *",
            timezone="America/New_York",
        )
        result = sched_running.list_schedules("+10000000000")
        assert len(result) == 1
        assert result[0]["description"] == "Daily"
        assert "next_run" in result[0]


# ---------- update ----------------------------------------------------------

class TestUpdateSchedule:
    def test_updates_fields(self, sched_running, tmp_path):
        r = sched_running.create_schedule(
            "+10000000000", "Old", "old prompt", "0 9 * * *",
            timezone="UTC",
        )
        sid = r["schedule"]["id"]

        result = sched_running.update_schedule(
            "+10000000000", sid,
            description="New", prompt="new prompt", cron="30 10 * * 1-5",
        )
        assert result["status"] == "updated"

        data = _read_yaml(tmp_path, "+10000000000")
        s = data["schedules"][0]
        assert s["description"] == "New"
        assert s["prompt"] == "new prompt"
        assert s["cron"] == "30 10 * * 1-5"

    def test_pause_resume(self, sched_running, tmp_path):
        r = sched_running.create_schedule(
            "+10000000000", "S1", "p", "0 9 * * *", timezone="UTC",
        )
        sid = r["schedule"]["id"]

        sched_running.update_schedule("+10000000000", sid, enabled=False)
        data = _read_yaml(tmp_path, "+10000000000")
        assert data["schedules"][0]["enabled"] is False

        sched_running.update_schedule("+10000000000", sid, enabled=True)
        data = _read_yaml(tmp_path, "+10000000000")
        assert data["schedules"][0]["enabled"] is True

    def test_not_found(self, sched_running):
        result = sched_running.update_schedule("+10000000000", "nope", prompt="x")
        assert "error" in result

    def test_invalid_cron(self, sched_running):
        r = sched_running.create_schedule(
            "+10000000000", "S1", "p", "0 9 * * *", timezone="UTC",
        )
        sid = r["schedule"]["id"]
        result = sched_running.update_schedule("+10000000000", sid, cron="bad")
        assert "error" in result

    def test_no_valid_fields(self, sched_running):
        result = sched_running.update_schedule("+10000000000", "x", bogus="y")
        assert "error" in result


# ---------- delete ----------------------------------------------------------

class TestDeleteSchedule:
    def test_deletes(self, sched_running, tmp_path):
        r = sched_running.create_schedule(
            "+10000000000", "Doomed", "p", "0 9 * * *", timezone="UTC",
        )
        sid = r["schedule"]["id"]
        result = sched_running.delete_schedule("+10000000000", sid)
        assert result["status"] == "deleted"

        data = _read_yaml(tmp_path, "+10000000000")
        assert len(data["schedules"]) == 0

    def test_not_found(self, sched_running):
        result = sched_running.delete_schedule("+10000000000", "nope")
        assert "error" in result


# ---------- timezone --------------------------------------------------------

class TestSetUserTimezone:
    def test_sets_default(self, sched_running, tmp_path):
        result = sched_running.set_user_timezone("+10000000000", "America/Denver")
        assert result["status"] == "updated"
        data = _read_yaml(tmp_path, "+10000000000")
        assert data["timezone"] == "America/Denver"

    def test_invalid(self, sched_running):
        result = sched_running.set_user_timezone("+10000000000", "Mars/Olympus")
        assert "error" in result


# ---------- reload ----------------------------------------------------------

class TestReloadSchedules:
    def test_reload_from_manual_edit(self, sched_running, tmp_path):
        # Create via API.
        sched_running.create_schedule(
            "+10000000000", "S1", "p1", "0 9 * * *", timezone="UTC",
        )
        # Manually add a second schedule to the YAML.
        data = _read_yaml(tmp_path, "+10000000000")
        data["schedules"].append({
            "id": "manual-edit-abc",
            "description": "Manually added",
            "prompt": "Test",
            "cron": "0 12 * * *",
            "timezone": "UTC",
            "enabled": True,
            "created_at": "2026-03-20T00:00:00",
            "last_run": None,
        })
        _write_yaml(tmp_path, "+10000000000", data)

        result = sched_running.reload_schedules("+10000000000")
        assert result["count"] == 2

        schedules = sched_running.list_schedules("+10000000000")
        assert len(schedules) == 2


# ---------- on_fire callback ------------------------------------------------

class TestOnFire:
    def test_fires_and_sends_sms(self, sched_running, tmp_path, monkeypatch):
        sched_running.create_schedule(
            "+10000000000", "Test", "Hello", "0 9 * * *", timezone="UTC",
        )
        data = _read_yaml(tmp_path, "+10000000000")
        sid = data["schedules"][0]["id"]

        sent_messages = []

        # _on_fire creates a fresh ConversationAgent + ConversationService
        # per-run (for medium-tier reliability), so we mock at the class
        # level rather than the singleton.
        class FakeConvoService:
            def __init__(self, **_kwargs):
                pass
            def reply(self, user_id, prompt, **_kwargs):
                return f"Reply to: {prompt}"

        class FakeAgent:
            def __init__(self, **_kwargs):
                pass

        monkeypatch.setattr(
            "prax.agent.orchestrator.ConversationAgent",
            FakeAgent,
        )
        monkeypatch.setattr(
            "prax.services.conversation_service.ConversationService",
            FakeConvoService,
        )
        monkeypatch.setattr(
            sched_running, "send_sms",
            lambda msg, to: sent_messages.append({"msg": msg, "to": to}),
        )

        sched_running._on_fire("+10000000000", sid, "Hello")

        assert len(sent_messages) == 1
        assert sent_messages[0]["to"] == "+10000000000"
        assert "Hello" in sent_messages[0]["msg"]

        # Verify last_run was updated.
        data = _read_yaml(tmp_path, "+10000000000")
        assert data["schedules"][0]["last_run"] is not None


# ---------- init / shutdown -------------------------------------------------

class TestLifecycle:
    def test_init_and_shutdown(self, sched_mod):
        sched_mod.init_scheduler()
        assert sched_mod._scheduler is not None
        sched_mod.shutdown_scheduler()
        assert sched_mod._scheduler is None

    def test_double_init_is_safe(self, sched_mod):
        sched_mod.init_scheduler()
        sched1 = sched_mod._scheduler
        sched_mod.init_scheduler()
        assert sched_mod._scheduler is sched1  # same instance
        sched_mod.shutdown_scheduler()

    def test_loads_existing_yaml_on_init(self, sched_mod, tmp_path):
        # Pre-create a schedules.yaml before init.
        _write_yaml(tmp_path, "+10000000000", {
            "timezone": "America/New_York",
            "schedules": [{
                "id": "pre-existing-abc",
                "description": "Pre-existing",
                "prompt": "Hello",
                "cron": "0 9 * * *",
                "timezone": "America/New_York",
                "enabled": True,
                "created_at": "2026-01-01T00:00:00",
                "last_run": None,
            }],
        })
        sched_mod.init_scheduler()
        assert "10000000000" in sched_mod._user_jobs
        assert "pre-existing-abc" in sched_mod._user_jobs["10000000000"]
        sched_mod.shutdown_scheduler()


# ---------- cron parsing ----------------------------------------------------

class TestParseCron:
    def test_valid(self, sched_mod):
        result = sched_mod._parse_cron("0 9,11,13,15,17 * * 1-5")
        assert result == {
            "minute": "0",
            "hour": "9,11,13,15,17",
            "day": "*",
            "month": "*",
            "day_of_week": "1-5",
        }

    def test_invalid_field_count(self, sched_mod):
        with pytest.raises(ValueError, match="5-field"):
            sched_mod._parse_cron("0 9 *")


# ---------- edge cases ------------------------------------------------------

class TestEdgeCases:
    def test_read_nonexistent_user(self, sched_mod):
        data = sched_mod._read_schedules("+19999999999")
        assert data["timezone"] == "UTC"
        assert data["schedules"] == []

    def test_disabled_schedule_not_registered(self, sched_running, tmp_path):
        _write_yaml(tmp_path, "+10000000000", {
            "timezone": "UTC",
            "schedules": [{
                "id": "disabled-one",
                "description": "Disabled",
                "prompt": "Hello",
                "cron": "0 9 * * *",
                "timezone": "UTC",
                "enabled": False,
                "created_at": "2026-01-01T00:00:00",
                "last_run": None,
            }],
        })
        sched_running.reload_schedules("+10000000000")
        # Disabled schedule should not have an APScheduler job.
        assert "disabled-one" not in sched_running._user_jobs.get("+10000000000", {})
