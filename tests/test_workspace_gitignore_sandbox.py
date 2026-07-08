"""The workspace .gitignore excludes sandbox/service runtime state so a
root-owned .sandbox/ can't abort `git add -A`."""
from __future__ import annotations

import pytest

from prax.services import workspace_service as ws

USER = "+10000000000"


@pytest.fixture()
def ws_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ws.settings, "workspace_dir", str(tmp_path))
    return tmp_path


def test_template_ignores_sandbox_and_services():
    assert ".sandbox/" in ws._WORKSPACE_GITIGNORE
    assert ".services/" in ws._WORKSPACE_GITIGNORE


def test_fresh_workspace_gitignore_has_sandbox(ws_dir):
    root = ws.ensure_workspace(USER)
    assert ".sandbox/" in open(f"{root}/.gitignore").read()


def test_stale_gitignore_gets_refreshed(ws_dir):
    root = ws.ensure_workspace(USER)
    # Simulate a pre-existing workspace whose .gitignore predates the fix.
    with open(f"{root}/.gitignore", "w") as f:
        f.write("*.log\n")
    ws.ensure_workspace(USER)  # should detect stale + refresh
    assert ".sandbox/" in open(f"{root}/.gitignore").read()


def test_git_add_skips_unreadable_sandbox_dir(ws_dir):
    import os
    import subprocess
    root = ws.ensure_workspace(USER)
    # A .sandbox/ dir with a file — stands in for the root-owned sandbox tree.
    os.makedirs(f"{root}/.sandbox/home", exist_ok=True)
    with open(f"{root}/.sandbox/home/state", "w") as f:
        f.write("runtime")
    r = subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, text=True)
    assert r.returncode == 0
    tracked = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                             capture_output=True, text=True).stdout
    assert ".sandbox" not in tracked  # ignored, not staged
