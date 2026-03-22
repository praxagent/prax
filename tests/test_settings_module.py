import importlib


def test_settings_loads_env_values():
    import prax.settings as settings_mod

    reloaded = importlib.reload(settings_mod)
    cfg = reloaded.get_settings()

    assert cfg.flask_secret_key == "test-secret"
    assert cfg.port == 5000
    assert cfg.twilio_account_sid.startswith("AC")
    assert cfg.base_model == "gpt-test"

    # Subsequent calls reuse the cached instance
    assert reloaded.get_settings() is cfg
