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
