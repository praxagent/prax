import importlib

import pytest


@pytest.fixture
def flask_client(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv('DATABASE_NAME', str(db_path))
    # Disable Twilio signature validation for route tests.
    monkeypatch.setenv('TWILIO_AUTH_TOKEN', '')

    import prax.settings as settings_mod
    importlib.reload(settings_mod)

    # Ensure the reloaded settings propagate to twilio_auth.
    import prax.blueprints.twilio_auth as twilio_auth_mod
    monkeypatch.setattr(twilio_auth_mod, 'settings', settings_mod.settings)

    import config as config_mod
    importlib.reload(config_mod)

    import prax.helpers_dictionaries as hd
    importlib.reload(hd)

    import app as app_mod
    importlib.reload(app_mod)

    app = app_mod.create_app()
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


def test_transcribe_accepts_known_number(flask_client):
    response = flask_client.post('/transcribe', data={
        'CallSid': 'CS123',
        'From': '+10000000000',
        'To': '+19999999999',
    })
    assert response.status_code == 200
    assert b"Hello" in response.data


def test_transcribe_rejects_unknown_number(flask_client):
    response = flask_client.post('/transcribe', data={
        'CallSid': 'CS123',
        'From': '+19999999999',
        'To': '+19999999999',
    })
    assert response.status_code == 404


def test_sms_rejects_unknown_number(flask_client):
    response = flask_client.post('/sms', data={
        'MessageSid': 'SM123',
        'From': '+19999999999',
        'Body': 'hello',
    })
    assert response.status_code == 404


def test_authorisation_reads_the_current_caller_map(monkeypatch):
    """Authorisation must read the current map, not one captured at import.

    voice_service used to do `from helpers_dictionaries import num_to_names`,
    which binds whichever dict existed when *that module* was first imported.
    Reloading the configuration then built a new dict the service never saw, so
    it went on authorising callers from a map nobody could observe — and which
    map it was depended on nothing more than import order.

    Patching the attribute rather than reloading the module is deliberate. The
    first version of this test reloaded `helpers_dictionaries` and restored it
    in a `finally`, which runs *before* monkeypatch undoes the environment — so
    it rebuilt the map from the still-patched env and left a fake phone map
    installed for every later test. A monkeypatched attribute is undone after
    the test, in the right order, by machinery that already exists.
    """
    from prax.services import voice_service as vs

    monkeypatch.setattr("prax.helpers_dictionaries.num_to_names",
                        {"+15550000001": "Late Arrival"})
    assert "+15550000001" in vs._known_callers(), (
        "the service is still reading a map captured at import time")
