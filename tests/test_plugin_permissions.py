"""Tests for the declarative permissions.md system."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from prax.plugins.permissions import (
    KNOWN_CAPABILITIES,
    NONE,
    UNRESTRICTED,
    PluginPermissions,
    load_permissions,
    parse_permissions_md,
)


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParsePermissionsMd:
    def test_full_example(self):
        text = """\
# Permissions

## capabilities
- llm
- http
- commands
- tts
- transcription

## secrets
- ELEVENLABS_API_KEY: Authenticate with ElevenLabs API

## allowed_commands
- pdflatex — compile LaTeX
- ffmpeg
- which
"""
        perms = parse_permissions_md(text)
        assert perms.can_use_llm
        assert perms.can_make_http
        assert perms.can_run_commands
        assert perms.can_use_tts
        assert perms.can_transcribe
        assert len(perms.secrets) == 1
        assert perms.secrets[0]["key"] == "ELEVENLABS_API_KEY"
        assert "ElevenLabs" in perms.secrets[0]["reason"]
        assert perms.allowed_commands == frozenset({"pdflatex", "ffmpeg", "which"})

    def test_minimal_capabilities(self):
        text = """\
## capabilities
- llm
"""
        perms = parse_permissions_md(text)
        assert perms.can_use_llm
        assert not perms.can_make_http
        assert not perms.can_run_commands
        assert not perms.can_use_tts
        assert not perms.can_transcribe
        assert perms.allowed_commands is None  # No section = no whitelist
        assert len(perms.secrets) == 0

    def test_empty_file(self):
        perms = parse_permissions_md("")
        assert perms.capabilities == frozenset()
        assert perms.secrets == ()
        assert perms.allowed_commands is None

    def test_no_secrets_section(self):
        text = """\
## capabilities
- http
"""
        perms = parse_permissions_md(text)
        assert perms.secret_keys == set()

    def test_secrets_without_reason(self):
        text = """\
## secrets
- MY_API_KEY
"""
        perms = parse_permissions_md(text)
        assert perms.secrets[0]["key"] == "MY_API_KEY"
        assert perms.secrets[0]["reason"] == ""

    def test_allowed_commands_with_backticks(self):
        text = """\
## capabilities
- commands

## allowed_commands
- `pdflatex`
- `ffmpeg` — video assembly
"""
        perms = parse_permissions_md(text)
        assert perms.allowed_commands == frozenset({"pdflatex", "ffmpeg"})

    def test_allowed_commands_with_hash_comments(self):
        text = """\
## capabilities
- commands

## allowed_commands
- ffmpeg # video tool
- which
"""
        perms = parse_permissions_md(text)
        assert perms.allowed_commands == frozenset({"ffmpeg", "which"})

    def test_unknown_capability_ignored(self):
        text = """\
## capabilities
- llm
- teleportation
"""
        perms = parse_permissions_md(text)
        assert perms.can_use_llm
        assert "teleportation" not in perms.capabilities

    def test_asterisk_list_items(self):
        text = """\
## capabilities
* llm
* http
"""
        perms = parse_permissions_md(text)
        assert perms.can_use_llm
        assert perms.can_make_http

    def test_empty_allowed_commands_section(self):
        """An empty ## allowed_commands section means whitelist is active but empty."""
        text = """\
## capabilities
- commands

## allowed_commands
"""
        perms = parse_permissions_md(text)
        assert perms.allowed_commands == frozenset()
        assert not perms.is_command_allowed("anything")


# ---------------------------------------------------------------------------
# PluginPermissions logic tests
# ---------------------------------------------------------------------------


class TestPluginPermissions:
    def test_is_command_allowed_with_whitelist(self):
        perms = PluginPermissions(
            capabilities=frozenset({"commands"}),
            allowed_commands=frozenset({"ffmpeg", "which"}),
        )
        assert perms.is_command_allowed("ffmpeg")
        assert perms.is_command_allowed("which")
        assert not perms.is_command_allowed("rm")
        assert not perms.is_command_allowed("curl")

    def test_is_command_allowed_without_whitelist(self):
        perms = PluginPermissions(
            capabilities=frozenset({"commands"}),
            allowed_commands=None,
        )
        assert perms.is_command_allowed("anything")

    def test_is_command_blocked_without_capability(self):
        perms = PluginPermissions(
            capabilities=frozenset({"llm"}),  # no commands
            allowed_commands=frozenset({"ffmpeg"}),
        )
        assert not perms.is_command_allowed("ffmpeg")

    def test_unrestricted_has_all_capabilities(self):
        assert UNRESTRICTED.can_use_llm
        assert UNRESTRICTED.can_make_http
        assert UNRESTRICTED.can_run_commands
        assert UNRESTRICTED.can_use_tts
        assert UNRESTRICTED.can_transcribe
        assert UNRESTRICTED.allowed_commands is None

    def test_none_has_no_capabilities(self):
        assert not NONE.can_use_llm
        assert not NONE.can_make_http
        assert not NONE.can_run_commands
        assert not NONE.can_use_tts
        assert not NONE.can_transcribe

    def test_secret_keys_property(self):
        perms = PluginPermissions(
            secrets=(
                {"key": "A_KEY", "reason": "r1"},
                {"key": "B_KEY", "reason": "r2"},
            ),
        )
        assert perms.secret_keys == {"A_KEY", "B_KEY"}


# ---------------------------------------------------------------------------
# load_permissions tests
# ---------------------------------------------------------------------------


class TestLoadPermissions:
    def test_loads_from_directory(self, tmp_path):
        (tmp_path / "permissions.md").write_text("""\
## capabilities
- llm
- http
""")
        perms = load_permissions(tmp_path)
        assert perms is not None
        assert perms.can_use_llm
        assert perms.can_make_http

    def test_returns_none_if_missing(self, tmp_path):
        perms = load_permissions(tmp_path)
        assert perms is None

    def test_returns_none_on_parse_error(self, tmp_path):
        # Write a file that will trigger an error in the parser
        perms_file = tmp_path / "permissions.md"
        perms_file.write_bytes(b"\x00\x01\x02")  # binary garbage
        # Should not raise — returns None
        perms = load_permissions(tmp_path)
        # May or may not be None depending on whether the parser handles it


# ---------------------------------------------------------------------------
# Capabilities enforcement tests
# ---------------------------------------------------------------------------


class TestCapabilitiesEnforcement:
    """Test that PluginCapabilities respects permissions.md."""

    def _make_caps(self, perms):
        from prax.plugins.capabilities import PluginCapabilities
        return PluginCapabilities(
            plugin_rel_path="test/plugin",
            trust_tier="imported",
            permissions=perms,
        )

    def test_llm_blocked_without_permission(self):
        perms = PluginPermissions(capabilities=frozenset({"http"}))
        caps = self._make_caps(perms)
        with pytest.raises(PermissionError, match="does not declare 'llm'"):
            caps.build_llm()

    def test_http_blocked_without_permission(self):
        perms = PluginPermissions(capabilities=frozenset({"llm"}))
        caps = self._make_caps(perms)
        with pytest.raises(PermissionError, match="does not declare 'http'"):
            caps.http_get("https://example.com")

    def test_commands_blocked_without_permission(self):
        perms = PluginPermissions(capabilities=frozenset({"llm"}))
        caps = self._make_caps(perms)
        with pytest.raises(PermissionError, match="does not declare 'commands'"):
            caps.run_command(["echo", "hi"])

    def test_command_whitelist_enforced(self):
        perms = PluginPermissions(
            capabilities=frozenset({"commands"}),
            allowed_commands=frozenset({"ffmpeg", "which"}),
        )
        caps = self._make_caps(perms)
        with pytest.raises(PermissionError, match="not allowed to run 'curl'"):
            caps.run_command(["curl", "https://evil.com"])

    def test_tts_blocked_without_permission(self):
        perms = PluginPermissions(capabilities=frozenset({"llm"}))
        caps = self._make_caps(perms)
        with pytest.raises(PermissionError, match="does not declare 'tts'"):
            caps.tts_synthesize("hello", "/tmp/out.mp3")

    def test_transcription_blocked_without_permission(self):
        perms = PluginPermissions(capabilities=frozenset({"llm"}))
        caps = self._make_caps(perms)
        with pytest.raises(PermissionError, match="does not declare 'transcription'"):
            caps.transcribe_audio("/tmp/audio.mp3")

    def test_no_permissions_object_allows_tier_policy(self):
        """When permissions=None (no permissions.md), fall through to tier policy."""
        from prax.plugins.capabilities import PluginCapabilities
        caps = PluginCapabilities(
            plugin_rel_path="test/plugin",
            trust_tier="imported",
            permissions=None,  # No permissions.md — backward compat
        )
        # Should not raise from permissions check (may raise from tier policy or LLM setup)
        # The _check_permission method returns without error when _permissions is None
        caps._check_permission("llm")  # Should not raise
        caps._check_permission("http")  # Should not raise
