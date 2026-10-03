"""Artifacts: Prax's pages for a person, shown in TeamWork, public only by a
person's decision (an always-on hard floor — a public link has no password)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from flask import Flask

import prax.agent.governed_tool as gov
from prax.agent import artifact_tools, hard_floors, human_approval
from prax.agent.user_context import current_user_id, current_user_message
from prax.services import artifact_service as arts
from prax.services import share_registry

USER = "usr_test_artifacts"
PAGE = "<!doctype html><html><body><h1>Retention</h1></body></html>"


def _live():
    """conftest reloads prax.settings per test: always reach the live object."""
    import prax.settings
    return prax.settings.settings


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    from prax.services import workspace_service
    settings = _live()
    monkeypatch.setattr(workspace_service.settings, "workspace_dir", str(tmp_path))
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "artifacts_enabled", True)
    monkeypatch.setattr(settings, "artifact_public_hours", 24)
    monkeypatch.setattr(settings, "hard_floors_enabled", False)   # the exposure floor holds anyway
    monkeypatch.setattr(settings, "out_of_band_approvals_enabled", False)
    token = current_user_id.set(USER)
    gov.begin_turn()
    yield tmp_path
    gov.drain_audit_log()
    current_user_id.reset(token)


@pytest.fixture
def said():
    tokens = []

    def _say(text):
        tokens.append(current_user_message.set(text))
    yield _say
    for t in reversed(tokens):
        current_user_message.reset(t)


@pytest.fixture
def ngrok(monkeypatch):
    import prax.utils.ngrok as ng
    monkeypatch.setattr(ng, "get_ngrok_url", lambda: "https://prax-test.ngrok.example")


def _governed(t):
    return gov.wrap_with_governance(t)


# --- the store ------------------------------------------------------------------

def test_create_then_update_makes_versions():
    a = arts.publish(USER, "Retention chart", PAGE)
    assert a["version"] == 1 and a["id"].startswith("retention-chart-")
    b = arts.publish(USER, "", PAGE.replace("Retention", "Retention v2"), artifact_id=a["id"])
    assert b["version"] == 2 and b["title"] == "Retention chart"
    got = arts.get(USER, a["id"])
    assert "Retention v2" in got["html"]
    assert [m["id"] for m in arts.list_all(USER)] == [a["id"]]


@pytest.mark.parametrize("html, why", [("", "empty"), ("just text", "must be HTML")])
def test_bad_pages_are_refused(html, why):
    with pytest.raises(arts.ArtifactError, match=why):
        arts.publish(USER, "x", html)


def test_oversized_pages_are_refused(monkeypatch):
    monkeypatch.setattr(_live(), "artifact_max_bytes", 100)
    with pytest.raises(arts.ArtifactError, match="limit is 100"):
        arts.publish(USER, "big", "<p>" + "x" * 200 + "</p>")


@pytest.mark.parametrize("bad", ["../escape", "a/b", "UPPER..", "x" * 80])
def test_ids_cannot_escape_the_artifacts_folder(bad):
    assert arts.get(USER, bad) is None
    with pytest.raises(arts.ArtifactError):
        arts.publish(USER, "t", PAGE, artifact_id=bad)


def test_an_empty_id_reads_nothing():
    assert arts.get(USER, "") is None


def test_updating_an_unknown_artifact_says_so():
    with pytest.raises(arts.ArtifactError, match="no artifact"):
        arts.publish(USER, "t", PAGE, artifact_id="nope-123456")


def test_each_write_is_a_git_commit(_ws):
    import subprocess

    from prax.services.workspace_service import workspace_root
    a = arts.publish(USER, "Plan", PAGE)
    arts.publish(USER, "", PAGE + "<p>2</p>", artifact_id=a["id"])
    log = subprocess.run(["git", "log", "--oneline"], cwd=workspace_root(USER),
                         capture_output=True, text=True, check=True).stdout
    assert f"Artifact {a['id']} v1" in log and f"Artifact {a['id']} v2" in log


# --- the tools ------------------------------------------------------------------

def test_tools_are_absent_when_disabled(monkeypatch):
    monkeypatch.setattr(_live(), "artifacts_enabled", False)
    assert artifact_tools.build_artifact_tools() == []


def test_publish_tells_the_model_how_to_show_it():
    out = artifact_tools.artifact_publish.invoke({"title": "Plan", "html": PAGE})
    item = arts.list_all(USER)[0]
    assert f"[artifact:{item['id']}]" in out


# --- going public: always a person's decision -----------------------------------

def test_sharing_is_a_floor_even_with_floors_off():
    assert not hard_floors.enabled()
    assert hard_floors.is_floor("artifact_share_public")


def test_one_decision_is_enough(said, ngrok, monkeypatch):
    """The floor gate and the HIGH-risk gate must recognise the same call: a
    person decides once, the share happens on the first call (2026-10-03: the
    two gates keyed the call differently and asked again)."""
    asked = []
    monkeypatch.setattr(_live(), "out_of_band_approvals_enabled", True)
    monkeypatch.setattr(human_approval, "request", lambda *a, **k: (
        asked.append(a[0]), human_approval.Decision(True, "", "ap3", "person:tj"))[1])
    a = arts.publish(USER, "Retention chart", PAGE)
    out = _governed(artifact_tools.artifact_share_public).invoke(
        {"artifact_id": a["id"], "expected_observation": "a public link"})
    assert "Public link" in out
    assert asked == ["artifact_share_public"]             # asked exactly once


def test_public_share_is_refused_without_a_person(said, ngrok):
    a = arts.publish(USER, "Retention chart", PAGE)
    said("make me a retention chart")
    out = _governed(artifact_tools.artifact_share_public).invoke({"artifact_id": a["id"]})
    assert out.startswith("⛔ Not done")
    assert share_registry.list_all(USER) == []


def test_a_generic_yes_is_not_enough(said, ngrok):
    a = arts.publish(USER, "Retention chart", PAGE)
    said("yes go ahead")
    out = _governed(artifact_tools.artifact_share_public).invoke({"artifact_id": a["id"]})
    assert out.startswith("⛔ Not done") and share_registry.list_all(USER) == []


def test_the_users_own_words_naming_it_allow_that_share(said, ngrok):
    a = arts.publish(USER, "Retention chart", PAGE)
    said("please share the retention chart publicly so I can send it to Ana")
    out = _governed(artifact_tools.artifact_share_public).invoke({"artifact_id": a["id"], "hours": 2})
    shares = share_registry.list_all(USER)
    assert len(shares) == 1 and shares[0]["url"] in out
    expires = datetime.fromisoformat(shares[0]["expires_at"])
    assert 1.9 * 3600 < (expires - datetime.now(UTC)).total_seconds() <= 2 * 3600
    assert "anyone with it can open it" in out


def test_an_approval_in_teamwork_allows_it(monkeypatch, said, ngrok):
    monkeypatch.setattr(_live(), "out_of_band_approvals_enabled", True)
    monkeypatch.setattr(human_approval, "request", lambda *a, **k: human_approval.Decision(
        True, "", "ap1", "person:tj"))
    a = arts.publish(USER, "Retention chart", PAGE)
    said("ok")
    out = _governed(artifact_tools.artifact_share_public).invoke({"artifact_id": a["id"]})
    assert "Public link" in out and len(share_registry.list_all(USER)) == 1


def test_a_timed_grant_cannot_make_it_public(monkeypatch, said, ngrok):
    monkeypatch.setattr(_live(), "out_of_band_approvals_enabled", True)
    monkeypatch.setattr(human_approval, "request", lambda *a, **k: human_approval.Decision(
        True, "", "ap2", "grant:hour"))
    a = arts.publish(USER, "Retention chart", PAGE)
    out = _governed(artifact_tools.artifact_share_public).invoke({"artifact_id": a["id"]})
    assert out.startswith("⛔ Not done") and share_registry.list_all(USER) == []


def test_hours_are_capped_and_unshare_revokes(said, ngrok):
    a = arts.publish(USER, "Retention chart", PAGE)
    said("share the retention chart publicly")
    _governed(artifact_tools.artifact_share_public).invoke({"artifact_id": a["id"], "hours": 9999})
    expires = datetime.fromisoformat(share_registry.list_all(USER)[0]["expires_at"])
    assert (expires - datetime.now(UTC)).total_seconds() <= 168 * 3600
    out = artifact_tools.artifact_unshare.invoke({"artifact_id": a["id"]})
    assert "Revoked 1" in out and share_registry.list_all(USER) == []


def test_without_a_tunnel_there_is_no_public_link(said, monkeypatch):
    import prax.utils.ngrok as ng
    monkeypatch.setattr(ng, "get_ngrok_url", lambda: None)
    a = arts.publish(USER, "Retention chart", PAGE)
    said("share the retention chart publicly")
    out = _governed(artifact_tools.artifact_share_public).invoke({"artifact_id": a["id"]})
    assert "No public tunnel" in out and share_registry.list_all(USER) == []


# --- the routes -----------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    from prax.blueprints import teamwork_routes as tr
    from prax.blueprints.main_routes import main_routes
    monkeypatch.setattr(tr, "_get_teamwork_user_id", lambda: USER)
    app = Flask(__name__)
    app.register_blueprint(tr.teamwork_routes)
    app.register_blueprint(main_routes)
    return app.test_client()


def test_teamwork_reads_the_list_and_one_artifact(client):
    a = arts.publish(USER, "Plan", PAGE)
    listing = client.get("/teamwork/artifacts").get_json()
    assert [m["id"] for m in listing["artifacts"]] == [a["id"]]
    full = client.get(f"/teamwork/artifacts/{a['id']}").get_json()
    assert full["html"] == PAGE and full["version"] == 1
    meta = client.get(f"/teamwork/artifacts/{a['id']}?meta=1").get_json()
    assert "html" not in meta and meta["version"] == 1
    assert client.get("/teamwork/artifacts/nope-000000").status_code == 404


def test_routes_are_closed_when_disabled(client, monkeypatch):
    monkeypatch.setattr(_live(), "artifacts_enabled", False)
    assert client.get("/teamwork/artifacts").status_code == 404


def test_the_public_page_runs_sandboxed_with_no_network(client, said, ngrok):
    a = arts.publish(USER, "Retention chart", PAGE)
    said("share the retention chart publicly")
    _governed(artifact_tools.artifact_share_public).invoke({"artifact_id": a["id"]})
    entry = share_registry.list_all(USER)[0]
    resp = client.get(f"/shared/{entry['token']}/{entry['public_name']}")
    assert resp.status_code == 200 and b"Retention" in resp.data
    csp = resp.headers["Content-Security-Policy"]
    assert csp.startswith("sandbox allow-scripts") and "connect-src 'none'" in csp
    assert "allow-same-origin" not in csp
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_an_expired_link_is_gone(client, said, ngrok, monkeypatch):
    a = arts.publish(USER, "Retention chart", PAGE)
    said("share the retention chart publicly")
    _governed(artifact_tools.artifact_share_public).invoke({"artifact_id": a["id"], "hours": 1})
    entry = share_registry.list_all(USER)[0]
    entries = share_registry._load(USER)
    entries[entry["token"]]["expires_at"] = "2000-01-01T00:00:00+00:00"
    share_registry._save(USER, entries)
    assert client.get(f"/shared/{entry['token']}/{entry['public_name']}").status_code == 404
