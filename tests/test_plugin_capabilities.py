"""Tests for the PluginCapabilities service gateway."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from prax.plugins.capabilities import PluginCapabilities
from prax.plugins.registry import PluginTrust

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def imported_caps():
    return PluginCapabilities(
        plugin_rel_path="shared/evil_plugin",
        trust_tier=PluginTrust.IMPORTED,
        user_id="test-user",
    )


@pytest.fixture
def builtin_caps():
    return PluginCapabilities(
        plugin_rel_path="pdf_reader",
        trust_tier=PluginTrust.BUILTIN,
        user_id="test-user",
    )


# ---------------------------------------------------------------------------
# get_config — secret blocking
# ---------------------------------------------------------------------------

class TestGetConfig:
    def test_imported_blocks_secret_key(self, imported_caps):
        with pytest.raises(PermissionError, match="secret config key"):
            imported_caps.get_config("openai_key")

    def test_imported_blocks_key_containing_secret(self, imported_caps):
        with pytest.raises(PermissionError, match="secret config key"):
            imported_caps.get_config("api_secret")

    def test_imported_blocks_key_containing_token(self, imported_caps):
        with pytest.raises(PermissionError, match="secret config key"):
            imported_caps.get_config("auth_token")

    def test_imported_blocks_key_containing_password(self, imported_caps):
        with pytest.raises(PermissionError, match="secret config key"):
            imported_caps.get_config("db_password")

    def test_imported_blocks_key_containing_credential(self, imported_caps):
        with pytest.raises(PermissionError, match="secret config key"):
            imported_caps.get_config("user_credential")

    def test_imported_allows_safe_key(self, imported_caps, monkeypatch):
        mock_settings = MagicMock()
        mock_settings.workspace_dir = "/tmp/workspace"
        monkeypatch.setattr("prax.settings.settings", mock_settings)
        result = imported_caps.get_config("workspace_dir")
        assert result == "/tmp/workspace"

    def test_imported_returns_none_for_missing(self, imported_caps, monkeypatch):
        mock_settings = MagicMock(spec=[])  # empty spec = no attributes
        monkeypatch.setattr("prax.settings.settings", mock_settings)
        result = imported_caps.get_config("nonexistent_thing")
        assert result is None

    def test_builtin_blocks_secret_key(self, builtin_caps):
        """BUILTIN plugins still can't read keys matching secret patterns via get_config."""
        with pytest.raises(PermissionError, match="secret config key"):
            builtin_caps.get_config("openai_key")


# ---------------------------------------------------------------------------
# build_llm
# ---------------------------------------------------------------------------

class TestBuildLLM:
    def test_build_llm_proxies_through(self, imported_caps, monkeypatch):
        mock_build = MagicMock(return_value=MagicMock())
        monkeypatch.setattr("prax.agent.llm_factory.build_llm", mock_build)
        result = imported_caps.build_llm(tier="low")
        mock_build.assert_called_once_with(tier="low")
        assert result is mock_build.return_value

    def test_build_llm_denied_when_policy_forbids(self):
        caps = PluginCapabilities("test", PluginTrust.IMPORTED)
        caps.policy = caps.policy.__class__(can_use_llm=False)
        with pytest.raises(PermissionError, match="not permitted to use LLM"):
            caps.build_llm()


# ---------------------------------------------------------------------------
# HTTP — rate limiting
# ---------------------------------------------------------------------------

class TestHTTP:
    def test_http_get_is_audited(self, imported_caps, monkeypatch):
        mock_requests = MagicMock()
        mock_requests.get.return_value = MagicMock(status_code=200)
        monkeypatch.setattr("requests.get", mock_requests.get)
        # The import inside http_get will pick up the real requests module,
        # so we mock the module-level import.
        import requests as req_mod
        monkeypatch.setattr(req_mod, "get", mock_requests.get)
        imported_caps.http_get("https://example.com")
        mock_requests.get.assert_called_once()
        assert imported_caps._http_request_count == 1

    def test_http_rate_limit(self, imported_caps):
        imported_caps._http_request_count = imported_caps.policy.max_http_requests_per_invocation
        with pytest.raises(PermissionError, match="exceeded HTTP request limit"):
            imported_caps.http_get("https://example.com")


# ---------------------------------------------------------------------------
# Workspace — requires user context
# ---------------------------------------------------------------------------

class TestWorkspace:
    def test_save_file_without_user_raises(self):
        caps = PluginCapabilities("test", PluginTrust.IMPORTED, user_id=None)
        with pytest.raises(RuntimeError, match="No user context"):
            caps.save_file("test.txt", b"content")

    def test_workspace_path_without_user_raises(self):
        caps = PluginCapabilities("test", PluginTrust.IMPORTED, user_id=None)
        with pytest.raises(RuntimeError, match="No user context"):
            caps.workspace_path("subdir")

    def test_get_user_id(self, imported_caps):
        assert imported_caps.get_user_id() == "test-user"
