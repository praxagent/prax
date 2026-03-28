"""Tests for the plugin environment restriction layer."""
from __future__ import annotations

import os

import pytest

from prax.plugins.restricted_env import (
    SanitizedEnviron,
    _is_sensitive,
    restricted_import_env,
)


# ---------------------------------------------------------------------------
# _is_sensitive
# ---------------------------------------------------------------------------

class TestIsSensitive:
    @pytest.mark.parametrize("key", [
        "OPENAI_KEY", "OPENAI_API_KEY", "openai_key",
        "ANTHROPIC_KEY", "AWS_SECRET_ACCESS_KEY",
        "AZURE_CLIENT_SECRET", "DB_PASSWORD",
        "AUTH_TOKEN", "MY_CREDENTIAL", "TWILIO_AUTH_TOKEN",
        "ELEVENLABS_KEY", "AMADEUS_SECRET",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ])
    def test_sensitive_keys_detected(self, key):
        assert _is_sensitive(key) is True

    @pytest.mark.parametrize("key", [
        "PATH", "HOME", "LANG", "USER", "SHELL", "TERM",
        "LC_ALL", "EDITOR", "PYTHONPATH",
    ])
    def test_safe_keys_allowed(self, key):
        assert _is_sensitive(key) is False


# ---------------------------------------------------------------------------
# SanitizedEnviron
# ---------------------------------------------------------------------------

class TestSanitizedEnviron:
    def test_blocks_getitem_for_sensitive(self):
        env = SanitizedEnviron({"OPENAI_KEY": "sk-xxx", "PATH": "/usr/bin"})
        with pytest.raises(KeyError):
            env["OPENAI_KEY"]

    def test_allows_getitem_for_safe(self):
        env = SanitizedEnviron({"PATH": "/usr/bin"})
        assert env["PATH"] == "/usr/bin"

    def test_get_returns_default_for_sensitive(self):
        env = SanitizedEnviron({"OPENAI_KEY": "sk-xxx"})
        assert env.get("OPENAI_KEY", "default") == "default"

    def test_get_returns_value_for_safe(self):
        env = SanitizedEnviron({"HOME": "/home/user"})
        assert env.get("HOME") == "/home/user"

    def test_contains_false_for_sensitive(self):
        env = SanitizedEnviron({"ANTHROPIC_KEY": "ak-xxx"})
        assert "ANTHROPIC_KEY" not in env

    def test_contains_true_for_safe(self):
        env = SanitizedEnviron({"PATH": "/usr/bin"})
        assert "PATH" in env

    def test_keys_excludes_sensitive(self):
        env = SanitizedEnviron({"OPENAI_KEY": "sk-xxx", "PATH": "/usr/bin"})
        assert "OPENAI_KEY" not in env.keys()
        assert "PATH" in env.keys()

    def test_values_excludes_sensitive(self):
        env = SanitizedEnviron({"OPENAI_KEY": "sk-xxx", "PATH": "/usr/bin"})
        assert "sk-xxx" not in env.values()
        assert "/usr/bin" in env.values()

    def test_items_excludes_sensitive(self):
        env = SanitizedEnviron({"OPENAI_KEY": "sk-xxx", "PATH": "/usr/bin"})
        items = dict(env.items())
        assert "OPENAI_KEY" not in items
        assert items["PATH"] == "/usr/bin"

    def test_iter_excludes_sensitive(self):
        env = SanitizedEnviron({"OPENAI_KEY": "sk-xxx", "PATH": "/usr/bin"})
        keys = list(env)
        assert "OPENAI_KEY" not in keys
        assert "PATH" in keys

    def test_copy_excludes_sensitive(self):
        env = SanitizedEnviron({"OPENAI_KEY": "sk-xxx", "HOME": "/home"})
        c = env.copy()
        assert "OPENAI_KEY" not in c
        assert c["HOME"] == "/home"

    def test_sensitive_not_stored_in_dict(self):
        """Sensitive keys are not even stored in the underlying dict."""
        env = SanitizedEnviron({"OPENAI_KEY": "sk-xxx", "PATH": "/usr/bin"})
        # The constructor should have filtered sensitive keys out.
        assert "OPENAI_KEY" not in dict.keys(env)


# ---------------------------------------------------------------------------
# restricted_import_env context manager
# ---------------------------------------------------------------------------

class TestRestrictedImportEnv:
    def test_os_environ_is_restricted_inside(self):
        os.environ["TEST_OPENAI_TOKEN"] = "secret_value"
        try:
            with restricted_import_env("test_plugin"):
                # Inside the context, os.environ should block sensitive keys.
                assert os.environ.get("TEST_OPENAI_TOKEN") is None
                with pytest.raises(KeyError):
                    _ = os.environ["TEST_OPENAI_TOKEN"]
        finally:
            del os.environ["TEST_OPENAI_TOKEN"]

    def test_os_environ_restored_after(self):
        original = os.environ
        with restricted_import_env("test_plugin"):
            assert os.environ is not original
        assert os.environ is original

    def test_os_getenv_restricted_inside(self):
        os.environ["TEST_SECRET_KEY"] = "my_secret"
        try:
            with restricted_import_env("test_plugin"):
                result = os.getenv("TEST_SECRET_KEY", "fallback")
                assert result == "fallback"
        finally:
            del os.environ["TEST_SECRET_KEY"]

    def test_os_getenv_restored_after(self):
        original_getenv = os.getenv
        with restricted_import_env("test_plugin"):
            assert os.getenv is not original_getenv
        assert os.getenv is original_getenv

    def test_safe_keys_still_accessible(self):
        os.environ["MY_SAFE_VAR"] = "hello"
        try:
            with restricted_import_env("test_plugin"):
                assert os.environ.get("MY_SAFE_VAR") == "hello"
        finally:
            del os.environ["MY_SAFE_VAR"]
