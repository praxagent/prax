"""Tests for discord_service — discord.py is NOT required for these tests."""
import importlib

import pytest


@pytest.fixture()
def disc_mod(monkeypatch):
    """Reload discord_service with test config."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_ALLOWED_USERS", '{"111": "Alice", "222": "Bob"}')
    monkeypatch.setenv("DISCORD_ALLOWED_CHANNELS", "900,901")

    import prax.settings as settings_mod
    importlib.reload(settings_mod)

    module = importlib.reload(
        importlib.import_module("prax.services.discord_service")
    )
    monkeypatch.setattr(module.settings, "discord_bot_token", "test-token")
    monkeypatch.setattr(module.settings, "discord_allowed_users", '{"111": "Alice", "222": "Bob"}')
    monkeypatch.setattr(module.settings, "discord_allowed_channels", "900,901")

    return module


@pytest.fixture()
def disc_disabled(monkeypatch):
    """Reload discord_service with no token."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "")
    import prax.settings as settings_mod
    importlib.reload(settings_mod)

    module = importlib.reload(
        importlib.import_module("prax.services.discord_service")
    )
    monkeypatch.setattr(module.settings, "discord_bot_token", "")
    return module


# ---------- Config loading ------------------------------------------------

class TestConfigLoading:
    def test_load_allowed_users(self, disc_mod):
        users = disc_mod._load_allowed_users()
        assert users == {"111": "Alice", "222": "Bob"}

    def test_load_allowed_users_empty(self, disc_disabled):
        disc_disabled.settings.discord_allowed_users = None
        users = disc_disabled._load_allowed_users()
        assert users == {}

    def test_load_allowed_users_bad_json(self, disc_mod):
        disc_mod.settings.discord_allowed_users = "not json"
        users = disc_mod._load_allowed_users()
        assert users == {}

    def test_load_allowed_channels(self, disc_mod):
        channels = disc_mod._load_allowed_channels()
        assert channels == {900, 901}

    def test_load_allowed_channels_empty(self, disc_disabled):
        disc_disabled.settings.discord_allowed_channels = None
        channels = disc_disabled._load_allowed_channels()
        assert channels == set()

    def test_load_allowed_channels_whitespace(self, disc_mod):
        disc_mod.settings.discord_allowed_channels = " 100 , 200 , "
        channels = disc_mod._load_allowed_channels()
        assert channels == {100, 200}

    def test_load_discord_to_phone(self, disc_mod):
        disc_mod.settings.discord_to_phone_map = '{"111": "+15551234567"}'
        mapping = disc_mod._load_discord_to_phone()
        assert mapping == {"111": "+15551234567"}

    def test_load_discord_to_phone_bad_json(self, disc_mod):
        disc_mod.settings.discord_to_phone_map = "not json"
        mapping = disc_mod._load_discord_to_phone()
        assert mapping == {}

    def test_load_discord_to_phone_opt_out(self, disc_mod):
        disc_mod.settings.discord_to_phone_map = "false"
        mapping = disc_mod._load_discord_to_phone()
        assert mapping == {}

    def test_load_discord_to_phone_opt_out_none(self, disc_mod):
        disc_mod.settings.discord_to_phone_map = "none"
        mapping = disc_mod._load_discord_to_phone()
        assert mapping == {}

    def test_auto_link_single_user(self, disc_mod, monkeypatch):
        """One Discord user + one phone user → auto-link."""
        disc_mod.settings.discord_to_phone_map = None
        disc_mod.settings.discord_allowed_users = '{"111": "Alice"}'
        import prax.helpers_dictionaries as hd
        monkeypatch.setattr(hd, "num_to_names", {"+15559999999": "Alice"})
        mapping = disc_mod._load_discord_to_phone()
        assert mapping == {"111": "+15559999999"}

    def test_no_auto_link_multiple_users(self, disc_mod, monkeypatch):
        """Multiple Discord users → no auto-link."""
        disc_mod.settings.discord_to_phone_map = None
        disc_mod.settings.discord_allowed_users = '{"111": "Alice", "222": "Bob"}'
        import prax.helpers_dictionaries as hd
        monkeypatch.setattr(hd, "num_to_names", {"+15559999999": "Alice"})
        mapping = disc_mod._load_discord_to_phone()
        assert mapping == {}

    def test_no_auto_link_no_phone_users(self, disc_mod, monkeypatch):
        """No phone users configured → no auto-link."""
        disc_mod.settings.discord_to_phone_map = None
        disc_mod.settings.discord_allowed_users = '{"111": "Alice"}'
        import prax.helpers_dictionaries as hd
        monkeypatch.setattr(hd, "num_to_names", {})
        mapping = disc_mod._load_discord_to_phone()
        assert mapping == {}


# ---------- User ID conversion --------------------------------------------

class TestUserIdConversion:
    def test_user_id_prefix_no_mapping(self, disc_mod):
        disc_mod._discord_to_phone = {}
        assert disc_mod._user_id_for_service(123456789) == "D123456789"

    def test_user_id_string_input(self, disc_mod):
        disc_mod._discord_to_phone = {}
        assert disc_mod._user_id_for_service("999") == "D999"

    def test_user_id_works_with_int_conversion(self, disc_mod):
        """The D-prefix pattern must work with conversation_service's int(id[1:])."""
        disc_mod._discord_to_phone = {}
        uid = disc_mod._user_id_for_service("123456789012345678")
        assert uid == "D123456789012345678"
        # Simulates what conversation_service.reply() does:
        assert int(uid[1:]) == 123456789012345678

    def test_user_id_linked_to_phone(self, disc_mod):
        disc_mod._discord_to_phone = {"111": "+15551234567"}
        assert disc_mod._user_id_for_service("111") == "+15551234567"

    def test_user_id_linked_integer_input(self, disc_mod):
        disc_mod._discord_to_phone = {"111": "+15551234567"}
        assert disc_mod._user_id_for_service(111) == "+15551234567"

    def test_user_id_unlinked_falls_back(self, disc_mod):
        disc_mod._discord_to_phone = {"111": "+15551234567"}
        assert disc_mod._user_id_for_service("999") == "D999"


# ---------- Message chunking ----------------------------------------------

class TestMessageChunking:
    def test_short_message(self, disc_mod):
        chunks = disc_mod._chunk_message("Hello!")
        assert chunks == ["Hello!"]

    def test_exact_limit(self, disc_mod):
        msg = "x" * 2000
        chunks = disc_mod._chunk_message(msg)
        assert len(chunks) == 1

    def test_over_limit_splits(self, disc_mod):
        msg = "x" * 3000
        chunks = disc_mod._chunk_message(msg)
        assert len(chunks) == 2
        assert "".join(chunks) == msg

    def test_splits_at_newline(self, disc_mod):
        # Build a message with a newline near the 2000-char boundary.
        line1 = "a" * 1900 + "\n"
        line2 = "b" * 500
        msg = line1 + line2
        chunks = disc_mod._chunk_message(msg)
        assert len(chunks) == 2
        assert chunks[0] == "a" * 1900
        assert chunks[1] == "b" * 500

    def test_empty_message(self, disc_mod):
        chunks = disc_mod._chunk_message("")
        assert chunks == [""]


# ---------- Bot startup ---------------------------------------------------

class TestBotStartup:
    def test_start_bot_no_token(self, disc_disabled):
        """start_bot() should be a no-op when token is empty."""
        disc_disabled.start_bot()
        assert disc_disabled._bot_thread is None

    def test_start_bot_with_token_sets_users(self, disc_mod, monkeypatch):
        """start_bot() should load allowed users even if we mock the actual bot."""
        # Prevent actually starting the bot thread.
        monkeypatch.setattr(disc_mod.threading, "Thread", lambda **kw: type("T", (), {"start": lambda s: None})())

        disc_mod.start_bot()
        assert disc_mod._allowed_users == {"111": "Alice", "222": "Bob"}
        assert disc_mod._allowed_channels == {900, 901}

    def test_stop_bot_no_loop(self, disc_mod):
        """stop_bot() should not crash when no loop is running."""
        disc_mod._loop = None
        disc_mod.stop_bot()  # Should not raise.
