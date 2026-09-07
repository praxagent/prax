"""Inbound API-key check on ``/teamwork/*``, ``/plugins/*`` and ``/api/users/*``.

Gate: ``PRAX_API_KEY`` (``settings.prax_api_key``).  Empty — the default — and
every request is handled exactly as before; set, and the three blueprints
answer 401 unless the request carries the key (``X-API-Key`` or
``Authorization: Bearer``).  See ``prax/blueprints/inbound_auth.py``.

On the pre-fix code every "401" assertion below fails: the routes answered
200/400 to anonymous callers regardless of the setting.
"""
from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest
from flask import Flask

KEY = "k" * 32
WRONG_SAME_LENGTH = "j" * 32

# One route per guarded blueprint whose view answers deterministically once
# the check lets it through: (method, path, status the view itself returns).
ROUTES = [
    ("GET", "/teamwork/observability", 200),   # reads settings only
    ("POST", "/plugins/import", 400),          # "repo_url is required"
    ("GET", "/api/users", 200),                # list_users patched to []
]


@pytest.fixture()
def app():
    from prax.blueprints.plugin_routes import plugin_routes
    from prax.blueprints.teamwork_routes import teamwork_routes
    from prax.blueprints.user_routes import user_routes

    app = Flask(__name__)
    app.config["TESTING"] = True
    for bp in (teamwork_routes, plugin_routes, user_routes):
        app.register_blueprint(bp)
    return app


@pytest.fixture()
def client(app):
    with patch("prax.blueprints.user_routes.list_users", return_value=[]):
        with app.test_client() as c:
            yield c


def _set_key(monkeypatch, value: str) -> None:
    import prax.settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "prax_api_key", value)


def _call(client, method, path, **kw):
    # An empty JSON body so POST views that ``get_json(force=True)`` reach
    # their own validation instead of failing on a missing body.
    return client.open(path, method=method, json={} if method == "POST" else None, **kw)


# ---------------------------------------------------------------------------
# Default-off
# ---------------------------------------------------------------------------

def test_unset_key_leaves_every_route_open(client, monkeypatch):
    """Empty setting = prior behaviour: no header needed, no 401 anywhere."""
    _set_key(monkeypatch, "")
    for method, path, expected in ROUTES:
        resp = _call(client, method, path)
        assert resp.status_code == expected, path


# ---------------------------------------------------------------------------
# Enforced once the key is set
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("method", "path", "_"), ROUTES)
def test_set_key_rejects_request_without_credential(client, monkeypatch, method, path, _):
    _set_key(monkeypatch, KEY)
    resp = _call(client, method, path)
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "unauthorized"}


@pytest.mark.parametrize(("method", "path", "expected"), ROUTES)
def test_set_key_passes_with_x_api_key(client, monkeypatch, method, path, expected):
    _set_key(monkeypatch, KEY)
    resp = _call(client, method, path, headers={"X-API-Key": KEY})
    assert resp.status_code != 401
    assert resp.status_code == expected  # the view itself ran


def test_bearer_authorization_is_accepted(client, monkeypatch):
    _set_key(monkeypatch, KEY)
    resp = client.get("/teamwork/observability", headers={"Authorization": f"Bearer {KEY}"})
    assert resp.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"X-API-Key": WRONG_SAME_LENGTH},
        {"X-API-Key": KEY[:-1]},                          # proper prefix
        {"X-API-Key": KEY + "x"},                         # longer
        {"X-API-Key": ""},
        {"Authorization": f"Bearer {WRONG_SAME_LENGTH}"},
        {"Authorization": f"Basic {KEY}"},                # wrong scheme
        {"Authorization": KEY},                           # no scheme
        {"X-API-Key-2": KEY},                             # wrong header
    ],
    ids=[
        "wrong-same-length", "prefix", "longer", "empty",
        "bearer-wrong", "basic-scheme", "bare-token", "wrong-header",
    ],
)
def test_wrong_or_malformed_credential_is_rejected(client, monkeypatch, headers):
    _set_key(monkeypatch, KEY)
    for method, path, _ in ROUTES:
        resp = _call(client, method, path, headers=headers)
        assert resp.status_code == 401, (path, headers)
        assert resp.get_json() == {"error": "unauthorized"}


def test_rejection_carries_no_detail_and_never_logs_the_value(client, monkeypatch, caplog):
    _set_key(monkeypatch, KEY)
    probe = "presented-value-that-must-not-leak-0001"
    with caplog.at_level("DEBUG"):
        resp = client.get("/teamwork/observability", headers={"X-API-Key": probe})
    assert resp.status_code == 401
    body = resp.get_data(as_text=True)
    assert probe not in body and KEY not in body
    assert probe not in caplog.text and KEY not in caplog.text


def test_non_ascii_credential_is_a_401_not_a_500(client, monkeypatch):
    """``hmac.compare_digest`` raises TypeError on non-ASCII ``str``; the check
    compares bytes so a hostile header cannot turn a 401 into a 500."""
    _set_key(monkeypatch, KEY)
    resp = client.get("/teamwork/observability", headers={"X-API-Key": "kéy-" * 8})
    assert resp.status_code == 401


def test_check_reads_settings_at_request_time(client, monkeypatch):
    """``tests/conftest.py`` reloads ``prax.settings`` before every test.  A
    hook that bound ``settings`` at import would keep checking the stale
    instance — and silently never enforce a key set after import."""
    import prax.settings as settings_mod

    monkeypatch.setenv("PRAX_API_KEY", KEY)
    importlib.reload(settings_mod)
    assert settings_mod.settings.prax_api_key == KEY

    assert client.get("/teamwork/observability").status_code == 401
    assert client.get("/teamwork/observability", headers={"X-API-Key": KEY}).status_code == 200


# ---------------------------------------------------------------------------
# The real app: guarded blueprints only, probes and Twilio untouched
# ---------------------------------------------------------------------------

@pytest.fixture()
def real_app_client(monkeypatch, tmp_path):
    """The real ``create_app()`` with the key set (mirrors tests/test_routes.py)."""
    monkeypatch.setenv("DATABASE_NAME", str(tmp_path / "test.db"))
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "")
    monkeypatch.setenv("PRAX_API_KEY", KEY)

    import prax.settings as settings_mod

    importlib.reload(settings_mod)

    import prax.blueprints.twilio_auth as twilio_auth_mod

    monkeypatch.setattr(twilio_auth_mod, "settings", settings_mod.settings)

    import config as config_mod

    importlib.reload(config_mod)

    import prax.helpers_dictionaries as hd

    importlib.reload(hd)

    import app as app_mod

    importlib.reload(app_mod)

    app = app_mod.create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_real_app_guards_exactly_the_three_blueprints(real_app_client):
    c = real_app_client
    # Guarded — the hook is live on the app ``app.py`` builds, not only on a
    # bare Flask in this file.
    assert c.get("/teamwork/observability").status_code == 401
    assert c.get("/plugins").status_code == 401
    assert c.get("/api/users").status_code == 401
    assert c.get("/teamwork/observability", headers={"X-API-Key": KEY}).status_code == 200
    # Untouched — liveness probes stay open; Twilio keeps its own control
    # (signature validation) and its own answer to an unknown number.
    assert c.get("/health").status_code == 200
    assert c.get("/healthz/live").status_code == 200
    assert c.post("/sms", data={"MessageSid": "SM1", "From": "+19999999999", "Body": "hi"}).status_code == 404
