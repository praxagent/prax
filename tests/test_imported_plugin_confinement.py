"""IMPORTED plugins: host-side capabilities stay inside the plugin's own box.

Fixing the bridge's off-main-thread timeout (#19) is what lets IMPORTED
plugins load in the threads Prax serves from; these are the limits that make
that safe. Each one runs in the Prax process or on the host, so a path or a
command left unconfined is host access for third-party code.
"""
from __future__ import annotations

import subprocess

import pytest

from prax.plugins.capabilities import PluginCapabilities
from prax.plugins.registry import PluginTrust


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setattr(PluginCapabilities, "_plugin_data_root", lambda self: str(root))
    return root


def _imported():
    return PluginCapabilities("shared/thirdparty", PluginTrust.IMPORTED, user_id="u1")


def _workspace():
    return PluginCapabilities("custom/mine", PluginTrust.WORKSPACE, user_id="u1")


# --- run_command --------------------------------------------------------------

def test_imported_command_refused_when_it_would_run_on_the_host(monkeypatch, data_root):
    monkeypatch.setattr("prax.utils.shell.routes_to_sandbox", lambda: False)
    ran = []
    monkeypatch.setattr("prax.utils.shell.run_command", lambda *a, **k: ran.append(a))
    with pytest.raises(PermissionError, match="only inside the sandbox"):
        _imported().run_command(["id"])
    assert ran == []


def test_imported_command_runs_when_routed_to_the_sandbox(monkeypatch, data_root):
    monkeypatch.setattr("prax.utils.shell.routes_to_sandbox", lambda: True)
    calls = []

    def fake_run(cmd, *, cwd=None, timeout=30):
        calls.append((cmd, cwd))
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr("prax.utils.shell.run_command", fake_run)
    assert _imported().run_command(["echo", "hi"]).stdout == "ok"
    assert calls == [(["echo", "hi"], str(data_root))]  # cwd forced to its data dir


def test_workspace_tier_keeps_prior_host_behaviour(monkeypatch):
    monkeypatch.setattr("prax.utils.shell.routes_to_sandbox", lambda: False)
    monkeypatch.setattr(
        "prax.utils.shell.run_command",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "host", ""),
    )
    assert _workspace().run_command(["echo"]).stdout == "host"


# --- tts / transcription paths ---------------------------------------------------

@pytest.mark.parametrize("path", ["/etc/cron.d/x", "../../prax/app.py", "/tmp/elsewhere.mp3"])
def test_imported_tts_cannot_write_outside_its_data_dir(data_root, path, monkeypatch):
    monkeypatch.setattr(PluginCapabilities, "_check_permission", lambda self, cap: None)
    with pytest.raises(PermissionError, match="outside it"):
        _imported().tts_synthesize("hello", path)


@pytest.mark.parametrize("path", ["/data/secret.env", "../../.env"])
def test_imported_transcription_cannot_read_outside_its_data_dir(data_root, path, monkeypatch):
    monkeypatch.setattr(PluginCapabilities, "_check_permission", lambda self, cap: None)
    with pytest.raises(PermissionError, match="outside it"):
        _imported().transcribe_audio(path)


def test_scoped_path_accepts_relative_and_in_root_absolute(data_root):
    caps = _imported()
    assert caps._scoped_path("out/a.mp3") == str(data_root / "out" / "a.mp3")
    inside = str(data_root / "b.mp3")
    assert caps._scoped_path(inside) == inside


def test_scoped_path_leaves_workspace_tier_alone():
    assert _workspace()._scoped_path("/anywhere/x.mp3") == "/anywhere/x.mp3"
