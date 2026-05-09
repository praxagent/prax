"""Tests for plugin.json manifest validation."""
from __future__ import annotations

import json
import textwrap

import pytest

from prax.plugins.manifest import (
    PluginManifestError,
    load_plugin_manifest,
    parse_plugin_manifest,
)


def _valid_manifest() -> dict:
    return {
        "name": "txt2presentation",
        "version": "0.2.0",
        "description": "Create narrated presentations.",
        "tools": [
            {
                "name": "text_to_presentation",
                "description": "Create a narrated video presentation.",
                "route": "artifact",
                "risk": "medium",
                "orchestrator_exposure": "requested",
            }
        ],
    }


def test_parse_valid_manifest():
    manifest = parse_plugin_manifest(_valid_manifest())

    assert manifest.name == "txt2presentation"
    assert manifest.version == "0.2.0"
    assert manifest.tool_map["text_to_presentation"].route == "artifact"
    assert (
        manifest.tool_map["text_to_presentation"].orchestrator_exposure
        == "requested"
    )


def test_manifest_requires_tool_route():
    raw = _valid_manifest()
    del raw["tools"][0]["route"]

    with pytest.raises(PluginManifestError, match="route"):
        parse_plugin_manifest(raw)


def test_manifest_rejects_unknown_route():
    raw = _valid_manifest()
    raw["tools"][0]["route"] = "orchestrator"

    with pytest.raises(PluginManifestError, match="unknown route"):
        parse_plugin_manifest(raw)


def test_load_manifest_missing_optional_returns_none(tmp_path):
    assert load_plugin_manifest(tmp_path, required=False) is None


def test_load_manifest_missing_required_raises(tmp_path):
    with pytest.raises(PluginManifestError, match="Missing required"):
        load_plugin_manifest(tmp_path, required=True)


def test_load_manifest_from_plugin_dir(tmp_path):
    (tmp_path / "plugin.json").write_text(json.dumps(_valid_manifest()))

    manifest = load_plugin_manifest(tmp_path, required=True)

    assert manifest is not None
    assert manifest.name == "txt2presentation"


def _write_simple_plugin(plugin_dir, *, manifest: bool = True, permissions: bool = True):
    plugin_dir.mkdir(parents=True)
    if permissions:
        (plugin_dir / "permissions.md").write_text("""\
# Permissions

## capabilities
""")
    if manifest:
        (plugin_dir / "plugin.json").write_text(json.dumps({
            "name": "simple",
            "version": "1",
            "description": "Simple imported plugin.",
            "tools": [
                {
                    "name": "simple_tool",
                    "description": "Simple tool.",
                    "route": "utility",
                    "risk": "low",
                }
            ],
        }))
    (plugin_dir / "plugin.py").write_text(textwrap.dedent("""\
        from langchain_core.tools import tool

        @tool
        def simple_tool(value: str) -> str:
            \"\"\"Simple tool.\"\"\"
            return value

        def register():
            return [simple_tool]
    """))


def test_workspace_shared_plugins_are_imported_and_require_manifest(tmp_path):
    from prax.plugins.loader import PluginLoader
    from prax.plugins.registry import PluginRegistry, PluginTrust

    plugin_dir = tmp_path / "shared" / "repo" / "simple"
    _write_simple_plugin(plugin_dir, manifest=False)
    loader = PluginLoader(registry=PluginRegistry(str(tmp_path / "reg.json")))
    loader.add_workspace_plugins_dir(tmp_path)

    names = [tool.name for tool in loader.load_all()]
    assert "simple_tool" not in names
    assert "Missing required plugin.json" in loader.get_load_errors()["shared/repo/simple"]

    (plugin_dir / "plugin.json").write_text(json.dumps(_valid_manifest() | {
        "name": "simple",
        "version": "1",
        "description": "Simple imported plugin.",
        "tools": [
            {
                "name": "simple_tool",
                "description": "Simple tool.",
                "route": "utility",
                "risk": "low",
            }
        ],
    }))

    tools = loader.load_all()

    try:
        assert "simple_tool" in [tool.name for tool in tools]
        assert loader.registry.get_trust_tier("shared/repo/simple") == PluginTrust.IMPORTED
        assert loader.get_tool_manifest("simple_tool").route == "utility"
    finally:
        from prax.plugins.bridge import shutdown_bridge
        shutdown_bridge("shared/repo/simple")


def test_imported_plugins_require_permissions_md(tmp_path):
    from prax.plugins.loader import PluginLoader
    from prax.plugins.registry import PluginRegistry

    plugin_dir = tmp_path / "shared" / "repo" / "simple"
    _write_simple_plugin(plugin_dir, permissions=False)
    loader = PluginLoader(registry=PluginRegistry(str(tmp_path / "reg.json")))
    loader.add_workspace_plugins_dir(tmp_path)

    names = [tool.name for tool in loader.load_all()]
    assert "simple_tool" not in names
    assert loader.get_load_errors()["shared/repo/simple"] == "Missing required permissions.md"
