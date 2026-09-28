"""WORKSPACE_PLUGIN_INTEGRITY_ENABLED: only plugin code Prax's own tools wrote runs.

The workspace is writable by the sandbox container and TeamWork's file API.
Each planted plugin here writes a marker file at import time, so the tests
check that its code never *executed* — not merely that its tools were hidden.
"""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from prax.plugins import integrity
from prax.plugins.loader import PluginLoader
from prax.plugins.registry import PluginRegistry


def _plugin(plugin_dir: Path, marker: Path, tool_name: str = "planted_tool") -> Path:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.py").write_text(textwrap.dedent(f"""\
        from pathlib import Path
        from langchain_core.tools import tool

        Path({str(marker)!r}).write_text("executed")

        @tool
        def {tool_name}(value: str) -> str:
            \"\"\"Echo.\"\"\"
            return value

        def register():
            return [{tool_name}]
    """))
    return plugin_dir / "plugin.py"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    led = integrity.TrustLedger(str(tmp_path / "ledger.json"))
    monkeypatch.setattr(integrity, "_ledger", led)
    return led


@pytest.fixture
def enforced(monkeypatch):
    import prax.settings as prax_settings
    monkeypatch.setattr(prax_settings.settings, "workspace_plugin_integrity_enabled", True)


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws" / "plugins"
    ws.mkdir(parents=True)
    loader = PluginLoader(registry=PluginRegistry(str(tmp_path / "reg.json")))
    loader.add_workspace_plugins_dir(ws)
    return ws, loader


# --- digest ---------------------------------------------------------------

def test_digest_covers_code_and_capability_files_but_not_readme_or_backups(tmp_path):
    unit = tmp_path / "p"
    _plugin(unit, tmp_path / "m")
    base = integrity.digest(unit)

    (unit / "README.md").write_text("docs")
    (unit / "plugin.py.prev").write_text("old")
    (unit / "__pycache__").mkdir()
    (unit / "__pycache__" / "plugin.cpython-313.pyc").write_bytes(b"x")
    assert integrity.digest(unit) == base

    (unit / "permissions.md").write_text("## capabilities\n- commands\n")
    assert integrity.digest(unit) != base


def test_digest_hashes_symlink_targets_without_following(tmp_path):
    unit = tmp_path / "p"
    _plugin(unit, tmp_path / "m")
    outside = tmp_path / "outside.txt"
    outside.write_text("a")
    os.symlink(outside, unit / "link")
    before = integrity.digest(unit)
    outside.write_text("b")  # the link's target content is not part of the plugin
    assert integrity.digest(unit) == before


# --- ledger -----------------------------------------------------------------

def test_unrecorded_and_modified_units_are_untrusted(tmp_path, ledger):
    unit = tmp_path / "p"
    plugin = _plugin(unit, tmp_path / "m")
    assert ledger.check(unit) == (False, "not recorded by any Prax plugin tool")
    ledger.record(unit, "test")
    assert ledger.check(unit) == (True, "")
    plugin.write_text(plugin.read_text() + "\n# tampered\n")
    trusted, reason = ledger.check(unit)
    assert not trusted and "changed" in reason


def test_rollback_retrusts_only_a_version_seen_before(tmp_path, ledger):
    unit = tmp_path / "p"
    plugin = _plugin(unit, tmp_path / "m")
    v1 = plugin.read_text()
    ledger.record(unit, "write-v1")
    plugin.write_text(v1 + "\n# v2\n")
    ledger.record(unit, "write-v2")

    plugin.write_text(v1)  # a genuine rollback to v1
    assert ledger.record_if_previously_trusted(unit, "rollback") is True
    assert ledger.check(unit)[0]

    plugin.write_text("import os  # planted backup content\n")
    assert ledger.record_if_previously_trusted(unit, "rollback") is False
    assert not ledger.check(unit)[0]


# --- loader -----------------------------------------------------------------

@pytest.mark.parametrize("subdir", ["custom/planted", "shared/repo/planted"])
def test_planted_workspace_plugin_never_executes(tmp_path, ledger, enforced, workspace, subdir):
    ws, loader = workspace
    marker = tmp_path / "marker"
    _plugin(ws / subdir, marker)

    names = [t.name for t in loader.load_all()]

    assert "planted_tool" not in names
    assert not marker.exists(), "planted plugin code ran"
    assert "blocked" in loader.get_load_errors()[subdir]


def test_recorded_workspace_plugin_loads(tmp_path, ledger, enforced, workspace):
    ws, loader = workspace
    marker = tmp_path / "marker"
    plugin = _plugin(ws / "custom" / "mine", marker, tool_name="mine_tool")
    ledger.record(integrity.plugin_unit(plugin), "plugin_write")

    assert "mine_tool" in [t.name for t in loader.load_all()]
    assert marker.exists()


def test_flag_off_keeps_prior_behaviour(tmp_path, ledger, workspace):
    ws, loader = workspace
    marker = tmp_path / "marker"
    _plugin(ws / "custom" / "planted", marker)

    assert "planted_tool" in [t.name for t in loader.load_all()]


def test_hot_swap_refuses_before_the_test_subprocess_runs(tmp_path, ledger, enforced, workspace):
    ws, loader = workspace
    marker = tmp_path / "marker"
    plugin = _plugin(ws / "custom" / "planted", marker)

    result = loader.hot_swap(str(plugin))

    assert result["error"] == "Untrusted plugin files"
    assert not marker.exists(), "sandbox_test_plugin executed planted code"


def test_builtin_plugins_need_no_record(ledger, enforced):
    from prax.plugins.loader import _PLUGINS_ROOT
    assert PluginLoader.needs_trust_record(_PLUGINS_ROOT / "anything" / "plugin.py") is False


# --- the trusted write path -----------------------------------------------

def test_plugin_write_records_what_it_wrote(tmp_path, ledger, enforced, monkeypatch):
    from prax.agent import plugin_tools

    base = tmp_path / "ws" / "plugins" / "custom"
    base.mkdir(parents=True)
    monkeypatch.setattr(plugin_tools, "_get_plugin_base_dir", lambda: base)
    _plugin(tmp_path / "src", tmp_path / "m", tool_name="written_tool")
    code = (tmp_path / "src" / "plugin.py").read_text()

    out = plugin_tools.plugin_write.func(name="written", code=code, description="d")

    assert "PASSED" in out
    assert ledger.check(base / "written") == (True, "")


# --- review follow-ups ------------------------------------------------------

def test_a_unit_with_a_symlink_is_never_trusted(tmp_path, ledger):
    unit = tmp_path / "p"
    _plugin(unit, tmp_path / "m")
    target = tmp_path / "writable_elsewhere.md"
    target.write_text("## capabilities\n")
    os.symlink(target, unit / "permissions.md")
    ledger.record(unit, "test")
    trusted, reason = ledger.check(unit)
    assert not trusted and "symbolic links" in reason


def test_plugin_write_refuses_a_folder_with_planted_files(tmp_path, ledger, enforced, monkeypatch):
    from prax.agent import plugin_tools

    base = tmp_path / "ws" / "plugins" / "custom"
    (base / "written").mkdir(parents=True)
    marker = tmp_path / "helper_ran"
    (base / "written" / "helper.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n")
    monkeypatch.setattr(plugin_tools, "_get_plugin_base_dir", lambda: base)
    _plugin(tmp_path / "src", tmp_path / "m", tool_name="written_tool")
    code = (tmp_path / "src" / "plugin.py").read_text().replace(
        "from pathlib import Path", "from pathlib import Path\nimport helper  # noqa")

    out = plugin_tools.plugin_write.func(name="written", code=code, description="d")

    assert "refusing" in out and "helper.py" in out
    assert not marker.exists(), "the planted helper ran in the test subprocess"
    assert not ledger.check(base / "written")[0]


def test_imported_checkout_with_untracked_files_is_not_trusted(tmp_path):
    import subprocess

    from prax.agent.plugin_tools import _checkout_problem

    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], check=True)
    (repo / "plugin.py").write_text("x = 1\n")
    subprocess.run([*git, "add", "plugin.py"], check=True)
    subprocess.run([*git, "commit", "-qm", "c"], check=True)
    assert _checkout_problem(repo) == ""

    (repo / "planted.py").write_text("import os\n")
    assert "planted.py" in _checkout_problem(repo)


def test_untrusted_rollback_is_reported_not_silent(tmp_path, ledger, enforced, workspace):
    ws, loader = workspace
    plugin = _plugin(ws / "custom" / "rb", tmp_path / "m", tool_name="rb_tool")
    ledger.record(integrity.plugin_unit(plugin), "plugin_write")
    (plugin.parent / "plugin.py.prev").write_text("import os  # planted backup\n")
    loader._abs_path_for = lambda rel_key: plugin

    result = loader.rollback("custom/rb")

    assert result["status"] == "rolled_back"
    assert "stays blocked" in result["warning"]
