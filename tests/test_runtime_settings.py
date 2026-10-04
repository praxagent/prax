"""Settings an admin changes from TeamWork's Settings page (runtime_settings).

An allowlist of settings Prax reads at call time; written to
.env-teamwork-override, which wins over .env and applies without a restart.
Nothing outside the list is written or honoured.
"""
from __future__ import annotations

import pytest
from flask import Flask

from prax.services import runtime_settings as rs


def _live():
    import prax.settings
    return prax.settings.settings


@pytest.fixture
def overrides(tmp_path, monkeypatch):
    path = tmp_path / ".env-teamwork-override"
    monkeypatch.setattr(_live(), "teamwork_overrides_path", str(path))
    monkeypatch.setattr(_live(), "desktop_screenshots_enabled", True)
    monkeypatch.setattr(_live(), "artifacts_enabled", False)
    monkeypatch.setattr(rs, "_env_value", lambda spec: bool(
        type(_live()).model_fields[spec.field].default))
    return path


def test_setting_one_applies_at_once_and_persists(overrides):
    out = rs.set_override("DESKTOP_SCREENSHOTS_ENABLED", False)
    assert out["value"] is False and out["source"] == "teamwork"
    assert _live().desktop_screenshots_enabled is False          # live, no restart
    assert "DESKTOP_SCREENSHOTS_ENABLED=false" in overrides.read_text()


def test_the_file_is_applied_at_startup(overrides):
    overrides.write_text("ARTIFACTS_ENABLED=true\n")
    assert rs.apply_overrides() == {"ARTIFACTS_ENABLED": True}
    assert _live().artifacts_enabled is True


def test_only_listed_settings_can_be_written(overrides):
    with pytest.raises(KeyError):
        rs.set_override("HARD_FLOORS_ENABLED", False)
    with pytest.raises(KeyError):
        rs.set_override("PRAX_API_KEY", "x")
    assert not overrides.exists()


def test_a_hand_edited_key_outside_the_list_is_ignored(overrides, caplog):
    overrides.write_text("HARD_FLOORS_ENABLED=false\nOUT_OF_BAND_APPROVALS_ENABLED=false\n"
                         "DESKTOP_SCREENSHOTS_ENABLED=false\n")
    assert rs.apply_overrides() == {"DESKTOP_SCREENSHOTS_ENABLED": False}
    assert "Ignoring HARD_FLOORS_ENABLED" in caplog.text


def test_nothing_that_loosens_protection_is_on_the_list():
    protective = ("HARD_FLOOR", "APPROVAL", "EXPOSURE", "SHARE", "TUNNEL", "PROXY",
                  "KEY", "TOKEN", "SECRET", "EGRESS", "GOVERN", "RISK")
    for key in rs.REGISTRY:
        assert not any(word in key for word in protective), key
    for spec in rs.REGISTRY.values():
        assert spec.field in type(_live()).model_fields


def test_reset_goes_back_to_env(overrides):
    rs.set_override("DESKTOP_SCREENSHOTS_ENABLED", False)
    out = rs.clear_override("DESKTOP_SCREENSHOTS_ENABLED")
    assert out["source"] == "env" and out["value"] is True
    assert "DESKTOP_SCREENSHOTS_ENABLED" not in overrides.read_text()


def test_values_must_be_true_or_false(overrides):
    with pytest.raises(ValueError):
        rs.set_override("ARTIFACTS_ENABLED", "maybe")


@pytest.fixture
def client(overrides, monkeypatch):
    from prax.blueprints import teamwork_routes as tr
    monkeypatch.setattr(_live(), "prax_api_key", "")
    app = Flask(__name__)
    app.register_blueprint(tr.teamwork_routes)
    return app.test_client()


def test_the_routes(client):
    listed = client.get("/teamwork/runtime-settings").get_json()["settings"]
    shot = next(s for s in listed if s["key"] == "DESKTOP_SCREENSHOTS_ENABLED")
    assert shot["value"] is True and shot["source"] == "env" and "billed" in shot["help"]
    resp = client.put("/teamwork/runtime-settings/DESKTOP_SCREENSHOTS_ENABLED", json={"value": False})
    assert resp.status_code == 200 and resp.get_json()["value"] is False
    assert client.put("/teamwork/runtime-settings/HARD_FLOORS_ENABLED", json={"value": False}).status_code == 404
    assert client.put("/teamwork/runtime-settings/ARTIFACTS_ENABLED", json={}).status_code == 400
    assert client.delete("/teamwork/runtime-settings/DESKTOP_SCREENSHOTS_ENABLED").get_json()["source"] == "env"


def test_screenshots_off_means_prax_does_not_look(overrides, monkeypatch):
    from prax.agent import sandbox_tools as st
    monkeypatch.setattr(_live(), "sandbox_enabled", True)
    rs.set_override("DESKTOP_SCREENSHOTS_ENABLED", False)

    def no_capture():
        raise AssertionError("captured the screen with screenshots off")
    monkeypatch.setattr(st, "get_client", no_capture)
    out = st.desktop_screenshot.invoke({"question": "what's on screen?"})
    assert out.startswith("Screenshots are turned off")
