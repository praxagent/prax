"""One source of truth for where the workspace sits inside the sandbox.

The same Prax code must produce correct container paths whether the sandbox
mounts one user's workspace (make run-local-all, compose) or the whole
workspaces/ tree (deploy/update.sh, production). Before, the shell layer
assumed the first and the sandbox agent the second.
"""
from __future__ import annotations

import os

import pytest

import prax.settings as prax_settings
from prax.services import sandbox_mount


@pytest.fixture
def tree(tmp_path, monkeypatch):
    ws = tmp_path / "workspaces"
    (ws / "usr_a" / "active").mkdir(parents=True)
    (ws / "usr_b" / "active").mkdir(parents=True)
    from prax.services import workspace_service
    monkeypatch.setattr(workspace_service, "workspace_root", lambda uid: str(ws / uid))
    monkeypatch.setattr(prax_settings.settings, "workspace_dir", str(ws))
    monkeypatch.setattr(prax_settings.settings, "sandbox_workspace_mount_source", "")
    sandbox_mount.reset_cache()
    yield ws
    sandbox_mount.reset_cache()


def _mount(monkeypatch, path):
    monkeypatch.setattr(prax_settings.settings, "sandbox_workspace_mount_source", str(path))
    sandbox_mount.reset_cache()


# --- the two shapes -----------------------------------------------------------

def test_whole_tree_mount(tree, monkeypatch):
    _mount(monkeypatch, tree)
    assert sandbox_mount.user_root_in_sandbox("usr_a") == "/workspace/usr_a"
    assert sandbox_mount.to_sandbox(str(tree / "usr_a" / "active" / "x.csv")) == "/workspace/usr_a/active/x.csv"
    assert sandbox_mount.from_sandbox("/workspace/usr_a/active/x.csv") == str(tree / "usr_a" / "active" / "x.csv")


def test_per_user_mount(tree, monkeypatch):
    _mount(monkeypatch, tree / "usr_a")
    assert sandbox_mount.user_root_in_sandbox("usr_a") == "/workspace"
    assert sandbox_mount.to_sandbox(str(tree / "usr_a" / "active" / "x.csv")) == "/workspace/active/x.csv"
    assert sandbox_mount.from_sandbox("/workspace/active/x.csv") == str(tree / "usr_a" / "active" / "x.csv")


def test_a_user_outside_a_per_user_mount_gets_no_path(tree, monkeypatch):
    _mount(monkeypatch, tree / "usr_a")
    # Not "/workspace" — that would hand usr_b the other user's directory.
    assert sandbox_mount.user_root_in_sandbox("usr_b") is None
    assert sandbox_mount.to_sandbox(str(tree / "usr_b" / "active" / "x")) is None


def test_symlinked_user_dir_resolves_to_its_real_directory(tree, monkeypatch):
    # workspaces/usr_alias -> usr_a, as on the dev box.
    os.symlink(tree / "usr_a", tree / "usr_alias")
    _mount(monkeypatch, tree)
    assert sandbox_mount.to_sandbox(str(tree / "usr_alias" / "active")) == "/workspace/usr_a/active"


def test_from_sandbox_cannot_climb_out_of_the_mount(tree, monkeypatch):
    _mount(monkeypatch, tree / "usr_a")
    assert sandbox_mount.from_sandbox("/workspace/../usr_b/active/x") is None
    assert sandbox_mount.from_sandbox("/etc/passwd") is None


# --- where the answer comes from -------------------------------------------

def test_detected_from_the_running_container(tree, monkeypatch):
    class Container:
        attrs = {"Mounts": [
            {"Destination": "/tmp/.X11-unix", "Source": "/tmp/.X11-unix"},
            {"Destination": "/workspace", "Source": str(tree / "usr_b")},
        ]}

    monkeypatch.setattr(prax_settings.settings, "running_in_docker", False)
    monkeypatch.setattr(prax_settings.settings, "sandbox_daemon_url", "")
    monkeypatch.setattr(prax_settings.settings, "sandbox_enabled", True)
    monkeypatch.setattr("prax_sandbox.exec.find_sandbox_container", lambda config=None: Container())
    assert sandbox_mount.mount_source() == str(tree / "usr_b")


def test_host_install_falls_back_to_the_whole_tree(tree, monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "running_in_docker", False)
    monkeypatch.setattr(sandbox_mount, "_detect_from_docker", lambda: None)
    assert sandbox_mount.mount_source() == str(tree)


def test_compose_falls_back_to_the_prax_user_workspace(tree, monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "running_in_docker", True)
    monkeypatch.setattr(prax_settings.settings, "prax_user_id", "usr_a")
    assert sandbox_mount.mount_source() == str(tree / "usr_a")


# --- the shell layer uses it ---------------------------------------------------

def test_shell_translation_follows_the_mount(tree, monkeypatch):
    from prax.utils.shell import to_sandbox_path

    _mount(monkeypatch, tree)
    assert to_sandbox_path(str(tree / "usr_a" / "f.pdf")) == "/workspace/usr_a/f.pdf"
    _mount(monkeypatch, tree / "usr_a")
    assert to_sandbox_path(str(tree / "usr_a" / "f.pdf")) == "/workspace/f.pdf"
    assert to_sandbox_path("/tmp/elsewhere") == "/tmp/elsewhere"  # outside: unchanged
    assert to_sandbox_path("-t") == "-t"


def test_shared_tempdir_is_visible_to_a_per_user_sandbox(tree, monkeypatch):
    from prax.utils import shell

    _mount(monkeypatch, tree / "usr_a")
    monkeypatch.setattr(shell, "routes_to_sandbox", lambda: True)
    d = shell.shared_tempdir()
    assert sandbox_mount.to_sandbox(d) is not None


# --- SANDBOX_ROUTE_COMMANDS ------------------------------------------------------

def test_host_install_runs_on_the_host_by_default(monkeypatch):
    from prax.utils.shell import routes_to_sandbox

    monkeypatch.setattr(prax_settings.settings, "running_in_docker", False)
    monkeypatch.setattr(prax_settings.settings, "sandbox_route_commands", False)
    assert routes_to_sandbox() is False


def test_route_commands_sends_host_installs_to_the_sandbox(monkeypatch):
    from prax.utils.shell import routes_to_sandbox

    monkeypatch.setattr(prax_settings.settings, "running_in_docker", False)
    monkeypatch.setattr(prax_settings.settings, "sandbox_route_commands", True)
    monkeypatch.setattr(prax_settings.settings, "sandbox_enabled", True)
    assert routes_to_sandbox() is True
    monkeypatch.setattr(prax_settings.settings, "sandbox_enabled", False)
    assert routes_to_sandbox() is False  # no sandbox, nothing to route to


def test_route_commands_is_off_by_default():
    field = type(prax_settings.settings).model_fields["sandbox_route_commands"]
    assert field.default is False


# --- the agent layer on a per-user mount (the shape it used to get wrong) ----

def test_sandbox_agent_is_told_its_directory_on_a_per_user_mount(tree, monkeypatch):
    from prax.agent.spokes.sandbox import agent as sandbox_agent

    _mount(monkeypatch, tree / "usr_a")
    assert sandbox_agent._container_user_workspace("usr_a") == "/workspace"
    assert "not mounted" in sandbox_agent._container_user_workspace("usr_b")


def test_delivery_hint_on_a_per_user_mount(tree, monkeypatch):
    from prax.agent.spokes.sandbox import agent as sandbox_agent

    _mount(monkeypatch, tree / "usr_a")
    (tree / "usr_a" / "active" / "a.mp3").write_bytes(b"x")
    out = sandbox_agent._append_delivery_hint("Made /workspace/active/a.mp3", "usr_a")
    assert "workspace_send_file('active/a.mp3')" in out


def test_send_file_maps_a_per_user_container_path(tree, monkeypatch):
    import prax.agent.workspace_tools as wt

    _mount(monkeypatch, tree / "usr_a")
    (tree / "usr_a" / "active" / "a.mp3").write_bytes(b"x")
    monkeypatch.setattr(wt.workspace_service, "_workspace_root", lambda uid: str(tree / "usr_a"))
    monkeypatch.setattr(wt, "_get_user_id", lambda: "usr_a")
    seen = {}

    def stop(p):
        seen["p"] = p
        raise RuntimeError("stop-after-resolve")

    monkeypatch.setattr(wt.os.path, "getsize", stop)
    with pytest.raises(RuntimeError):
        wt.workspace_send_file.func("/workspace/active/a.mp3")
    assert seen["p"] == str(tree / "usr_a" / "active" / "a.mp3")
