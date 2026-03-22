import importlib


def test_phone_mappings_loaded(monkeypatch):
    import prax.settings as settings_mod

    settings_mod.settings.phone_to_name_map = '{"+12223334444": "Example"}'
    settings_mod.settings.phone_to_email_map = '{"+12223334444": "user@example.com"}'
    settings_mod.settings.phone_to_greeting_map = '{"+12223334444": "greeting.mp3"}'

    module = importlib.reload(importlib.import_module('prax.helpers_dictionaries'))

    assert module.num_to_names['+12223334444'] == 'Example'
    assert module.email_map['+12223334444'] == 'user@example.com'
    assert module.num_to_greetings['+12223334444'] == 'greeting.mp3'


def test_invalid_json_maps_to_empty(monkeypatch):
    module = importlib.import_module('prax.helpers_dictionaries')

    assert module._load_mapping('not-json') == {}
    assert module._load_mapping('') == {}
    assert module._load_mapping(None) == {}
