"""Tests for the hardened plugin-activation gate (security gap #2):
- plugin_import / plugin_import_activate are HIGH-risk (confirmation-gated)
- the registry tracks requires-acknowledgement
- the loader refuses to activate a flagged IMPORTED plugin until acknowledged
- discover_shared_keys finds the loader rel-keys for an imported plugin
"""
from __future__ import annotations

from types import SimpleNamespace

from prax.plugins.loader import PluginLoader
from prax.plugins.registry import PluginRegistry

# --------------------------------------------------------------------------- #
# HIGH-risk classification
# --------------------------------------------------------------------------- #

def test_import_tools_are_high_risk():
    from prax.agent.action_policy import RiskLevel, get_risk_level
    assert get_risk_level("plugin_import") == RiskLevel.HIGH
    assert get_risk_level("plugin_import_activate") == RiskLevel.HIGH


# --------------------------------------------------------------------------- #
# Registry requires-acknowledgement flag
# --------------------------------------------------------------------------- #

def test_registry_requires_ack_flag(tmp_path):
    reg = PluginRegistry(registry_path=str(tmp_path / "registry.json"))
    assert reg.requires_acknowledgement("shared/evil") is False
    reg.flag_requires_acknowledgement("shared/evil")
    assert reg.requires_acknowledgement("shared/evil") is True
    assert reg.is_warnings_acknowledged("shared/evil") is False
    reg.acknowledge_warnings("shared/evil")
    assert reg.is_warnings_acknowledged("shared/evil") is True


# --------------------------------------------------------------------------- #
# discover_shared_keys
# --------------------------------------------------------------------------- #

def test_discover_shared_keys(tmp_path):
    # Lay out a fake imported plugin: <ws>/shared/foo/plugin.py
    (tmp_path / "shared" / "foo").mkdir(parents=True)
    (tmp_path / "shared" / "foo" / "plugin.py").write_text("# plugin\n")
    reg = PluginRegistry(registry_path=str(tmp_path / "registry.json"))
    loader = PluginLoader(registry=reg)
    loader.add_workspace_plugins_dir(tmp_path)
    assert "shared/foo" in loader.discover_shared_keys("foo")
    assert loader.discover_shared_keys("nonexistent") == []


# --------------------------------------------------------------------------- #
# Loader gate: flagged-but-unacknowledged IMPORTED plugin is blocked
# --------------------------------------------------------------------------- #

def _isolated_loader(tmp_path, monkeypatch):
    """A loader whose discovery yields exactly one fake IMPORTED plugin."""
    reg = PluginRegistry(registry_path=str(tmp_path / "registry.json"))
    loader = PluginLoader(registry=reg)
    loader.add_workspace_plugins_dir(tmp_path)
    fake = (tmp_path / "shared" / "evil" / "plugin.py")

    def fake_discover(root):
        return [(fake, "shared/evil")] if str(root) == str(tmp_path) else []

    monkeypatch.setattr(loader, "_discover_plugins", fake_discover)
    # Manifest present (truthy) with an empty tool_map so the IMPORTED path runs.
    monkeypatch.setattr(loader, "_load_manifest",
                        lambda *a, **k: SimpleNamespace(tool_map={}))
    # Avoid pulling in the legacy plugin repo.
    monkeypatch.setattr("prax.plugins.repo.get_plugin_repo", lambda: None)
    bridge_calls = []
    monkeypatch.setattr(loader, "_load_imported_via_bridge",
                        lambda *a, **k: bridge_calls.append(a) or [])
    return loader, reg, bridge_calls


def test_loader_blocks_unacknowledged_plugin(tmp_path, monkeypatch):
    loader, reg, bridge_calls = _isolated_loader(tmp_path, monkeypatch)
    reg.flag_requires_acknowledgement("shared/evil")

    loader.load_all()

    assert bridge_calls == []  # never loaded
    assert "shared/evil" in loader.get_load_errors()
    assert "acknowledg" in loader.get_load_errors()["shared/evil"].lower()


def test_loader_loads_after_acknowledgement(tmp_path, monkeypatch):
    loader, reg, bridge_calls = _isolated_loader(tmp_path, monkeypatch)
    reg.flag_requires_acknowledgement("shared/evil")
    reg.acknowledge_warnings("shared/evil")  # the explicit confirmation

    loader.load_all()

    assert len(bridge_calls) == 1  # now activated
    assert "shared/evil" not in loader.get_load_errors()


def test_loader_unflagged_plugin_loads_normally(tmp_path, monkeypatch):
    # A plugin that was never flagged (no warnings) is unaffected by the gate.
    loader, reg, bridge_calls = _isolated_loader(tmp_path, monkeypatch)
    loader.load_all()
    assert len(bridge_calls) == 1
