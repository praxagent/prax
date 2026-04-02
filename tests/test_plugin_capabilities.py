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

    def test_read_file_without_user_raises(self):
        caps = PluginCapabilities("test", PluginTrust.IMPORTED, user_id=None)
        with pytest.raises(RuntimeError, match="No user context"):
            caps.read_file("test.txt")


# ---------------------------------------------------------------------------
# Scoped filesystem — IMPORTED plugins are jailed to plugin_data/
# ---------------------------------------------------------------------------

class TestScopedFilesystem:
    """IMPORTED plugins write to plugin_data/{rel_path}/, not active/."""

    @pytest.fixture
    def workspace(self, tmp_path, monkeypatch):
        """Set up a fake workspace directory."""
        import os
        ws = tmp_path / "workspaces"
        ws.mkdir()
        # Use realpath to match safe_join's resolution (macOS /var → /private/var).
        ws_real = os.path.realpath(str(ws))
        mock_settings = MagicMock()
        mock_settings.workspace_dir = ws_real
        monkeypatch.setattr("prax.settings.settings", mock_settings)
        monkeypatch.setattr("prax.services.workspace_service.settings", mock_settings)
        return ws

    # -- save_file --

    def test_imported_save_file_uses_scoped_dir(self, imported_caps, workspace):
        path = imported_caps.save_file("report.pdf", b"data")
        assert "/plugin_data/shared/evil_plugin/" in path
        assert path.endswith("report.pdf")
        assert "/active/" not in path

    def test_imported_save_file_creates_dir(self, imported_caps, workspace):
        import os
        path = imported_caps.save_file("out.txt", b"hello")
        assert os.path.isfile(path)
        with open(path, "rb") as f:
            assert f.read() == b"hello"

    def test_imported_save_file_blocks_traversal(self, imported_caps, workspace):
        with pytest.raises(ValueError, match="Path traversal"):
            imported_caps.save_file("../../etc/passwd", b"pwned")

    def test_builtin_save_file_uses_active(self, builtin_caps, workspace, monkeypatch):
        mock_save = MagicMock(return_value="/fake/active/report.pdf")
        monkeypatch.setattr("prax.services.workspace_service.save_file", mock_save)
        result = builtin_caps.save_file("report.pdf", b"data")
        mock_save.assert_called_once_with("test-user", "report.pdf", b"data")
        assert result == "/fake/active/report.pdf"

    # -- read_file --

    def test_imported_read_file_scoped(self, imported_caps, workspace):
        # Write via save_file, then read back.
        imported_caps.save_file("notes.txt", b"hello from plugin")
        result = imported_caps.read_file("notes.txt")
        assert result == "hello from plugin"

    def test_imported_read_file_blocks_traversal(self, imported_caps, workspace):
        # Create a file outside the scoped dir
        active = workspace / "test-user" / "active"
        active.mkdir(parents=True)
        (active / "secret.txt").write_text("secret data")
        with pytest.raises(ValueError, match="Path traversal"):
            imported_caps.read_file("../../active/secret.txt")

    def test_builtin_read_file_uses_active(self, builtin_caps, workspace, monkeypatch):
        mock_read = MagicMock(return_value="file contents")
        monkeypatch.setattr("prax.services.workspace_service.read_file", mock_read)
        result = builtin_caps.read_file("notes.txt")
        mock_read.assert_called_once_with("test-user", "notes.txt")
        assert result == "file contents"

    # -- workspace_path --

    def test_imported_workspace_path_returns_scoped(self, imported_caps, workspace):
        path = imported_caps.workspace_path()
        assert path.endswith("plugin_data/shared/evil_plugin")

    def test_imported_workspace_path_with_parts(self, imported_caps, workspace):
        path = imported_caps.workspace_path("subdir", "file.txt")
        assert "/plugin_data/shared/evil_plugin/" in path
        assert path.endswith("subdir/file.txt")

    def test_imported_workspace_path_blocks_traversal(self, imported_caps, workspace):
        with pytest.raises(ValueError, match="Path traversal"):
            imported_caps.workspace_path("../../etc")

    def test_builtin_workspace_path_returns_full_root(self, builtin_caps, workspace):
        path = builtin_caps.workspace_path()
        assert path.endswith("test-user")
        assert "plugin_data" not in path

    # -- run_command --

    def test_imported_run_command_forces_cwd(self, imported_caps, workspace):
        import os
        result = imported_caps.run_command(["pwd"])
        actual = result.stdout.strip()
        assert "/plugin_data/shared/evil_plugin" in actual
        assert os.path.isdir(actual)

    def test_imported_run_command_ignores_cwd_escape(self, imported_caps, workspace):
        with pytest.raises(ValueError, match="Path traversal"):
            imported_caps.run_command(["pwd"], cwd="../../../etc")

    def test_builtin_run_command_respects_cwd(self, builtin_caps, workspace, tmp_path):
        result = builtin_caps.run_command(["pwd"], cwd=str(tmp_path))
        assert result.stdout.strip() == str(tmp_path)


# ---------------------------------------------------------------------------
# Loader passes user_id from context to PluginCapabilities
# ---------------------------------------------------------------------------

class TestLoaderPassesUserContext:
    """Verify the plugin loader reads current_user_id and passes it to caps.

    This test would have caught the bug where generate_song failed with
    'No user context — cannot save workspace files' because the loader
    created PluginCapabilities with user_id=None.
    """

    def test_loader_passes_user_id_to_capabilities(self, monkeypatch, tmp_path):
        """When current_user_id is set, the loader should pass it to PluginCapabilities."""
        from prax.agent.user_context import current_user_id

        # Create a minimal plugin with register(caps)
        plugin_dir = tmp_path / "test_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.py").write_text(
            "PLUGIN_VERSION = '1'\n"
            "def register(caps):\n"
            "    return []\n"
        )

        # Capture the user_id passed to PluginCapabilities
        captured_user_ids = []
        OrigCaps = PluginCapabilities
        def capturing_caps(*args, **kwargs):
            captured_user_ids.append(kwargs.get("user_id"))
            return OrigCaps(*args, **kwargs)
        monkeypatch.setattr("prax.plugins.loader.PluginCapabilities", capturing_caps)

        from prax.plugins.loader import PluginLoader
        loader = PluginLoader()
        loader.add_workspace_plugins_dir(str(tmp_path))

        token = current_user_id.set("user_42")
        try:
            loader.load_all()
        finally:
            current_user_id.reset(token)

        assert "user_42" in captured_user_ids

    def test_loader_passes_none_when_no_user_context(self, monkeypatch, tmp_path):
        """Without current_user_id, caps.user_id should be None (startup case)."""
        from prax.agent.user_context import current_user_id

        plugin_dir = tmp_path / "test_plugin2"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.py").write_text(
            "PLUGIN_VERSION = '1'\n"
            "def register(caps):\n"
            "    return []\n"
        )

        captured_user_ids = []
        OrigCaps = PluginCapabilities
        def capturing_caps(*args, **kwargs):
            captured_user_ids.append(kwargs.get("user_id"))
            return OrigCaps(*args, **kwargs)
        monkeypatch.setattr("prax.plugins.loader.PluginCapabilities", capturing_caps)

        # Explicitly clear any ambient user context so the test is hermetic.
        token = current_user_id.set(None)
        try:
            from prax.plugins.loader import PluginLoader
            loader = PluginLoader()
            loader.add_workspace_plugins_dir(str(tmp_path))
            loader.load_all()
        finally:
            current_user_id.reset(token)

        assert None in captured_user_ids
