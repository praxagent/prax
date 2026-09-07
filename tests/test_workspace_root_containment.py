"""workspace_root / ensure_workspace containment.

Review 2026-09-05: the legacy fallback in ``workspace_root`` joined the raw id,
so ``workspace_root("../prax")`` returned the source checkout next to the
workspaces dir; ``ensure_workspace`` then rewrote that checkout's .gitignore
and ran ``git add -A`` in it. Scratch layout here: ``<tmp>/workspaces`` as
WORKSPACE_DIR and a sibling git repo ``<tmp>/prax`` standing in for the
checkout — every refusal is asserted by the sibling being untouched.
"""
from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest

from prax.services import workspace_service as ws

IGNORE_TEXT = "# the checkout's own ignore rules\n"


def _git(cwd, *args) -> str:
    r = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    )
    return r.stdout


@pytest.fixture
def layout(tmp_path, monkeypatch):
    wsdir = tmp_path / "workspaces"
    wsdir.mkdir()
    sibling = tmp_path / "prax"
    sibling.mkdir()
    (sibling / "README.md").write_text("source checkout\n", encoding="utf-8")
    (sibling / ".gitignore").write_text(IGNORE_TEXT, encoding="utf-8")
    _git(sibling, "init", "-q")
    _git(sibling, "add", "-A")
    _git(sibling, "commit", "-q", "-m", "init")
    monkeypatch.setattr(ws.settings, "workspace_dir", str(wsdir))
    # No identity row → the legacy branch runs.
    monkeypatch.setattr("prax.services.identity_service.get_user", lambda uid: None)
    return wsdir, sibling


def _identity(monkeypatch, workspace_dir: str) -> None:
    monkeypatch.setattr(
        "prax.services.identity_service.get_user",
        lambda uid: SimpleNamespace(workspace_dir=workspace_dir),
    )


def _assert_sibling_untouched(sibling) -> None:
    assert (sibling / ".gitignore").read_text(encoding="utf-8") == IGNORE_TEXT
    assert _git(sibling, "rev-list", "--count", "HEAD").strip() == "1"
    assert _git(sibling, "status", "--porcelain").strip() == ""
    assert not (sibling / "active").exists()


# --------------------------------------------------------------------------- #
# workspace_root — legacy branch
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("bad", ["../prax", "..", ".", "a/b", "+1/../prax", "usr x", ""])
def test_legacy_workspace_root_refuses_non_component_ids(layout, bad):
    with pytest.raises(ValueError):
        ws.workspace_root(bad)


def test_legacy_workspace_root_single_component(layout):
    wsdir, _ = layout
    assert ws.workspace_root("+15551234567") == os.path.join(str(wsdir), "15551234567")
    assert ws.workspace_root("D123456789") == os.path.join(str(wsdir), "D123456789")
    assert ws.workspace_root("usr_ab12cd34") == os.path.join(str(wsdir), "usr_ab12cd34")


# --------------------------------------------------------------------------- #
# ensure_workspace — never adopt a directory outside WORKSPACE_DIR
# --------------------------------------------------------------------------- #

def test_ensure_workspace_refuses_sibling_checkout_via_legacy_id(layout):
    _, sibling = layout
    with pytest.raises(ValueError):
        ws.ensure_workspace("../prax")
    _assert_sibling_untouched(sibling)


def test_ensure_workspace_refuses_sibling_checkout_via_identity_row(layout, monkeypatch):
    """Even a resolved user whose stored workspace_dir escapes must be refused."""
    _, sibling = layout
    _identity(monkeypatch, "../prax")
    with pytest.raises(ValueError):
        ws.ensure_workspace("usr_x")
    _assert_sibling_untouched(sibling)


def test_ensure_workspace_refuses_symlink_pointing_out(layout, monkeypatch):
    """A usr_* entry may be a symlink — but only to something inside WORKSPACE_DIR."""
    wsdir, sibling = layout
    os.symlink(sibling, wsdir / "usr_link", target_is_directory=True)
    _identity(monkeypatch, "usr_link")
    with pytest.raises(ValueError):
        ws.ensure_workspace("usr_x")
    _assert_sibling_untouched(sibling)


def test_ensure_workspace_refuses_nesting_inside_another_workspace(layout, monkeypatch):
    wsdir, _ = layout
    _identity(monkeypatch, "usr_a")
    root_a = ws.ensure_workspace("usr_a")
    assert os.path.isdir(os.path.join(root_a, ".git"))
    _identity(monkeypatch, "usr_a/active")
    with pytest.raises(ValueError):
        ws.ensure_workspace("usr_b")
    assert not os.path.exists(os.path.join(root_a, "active", ".git"))


def test_ensure_workspace_happy_paths(layout, monkeypatch):
    wsdir, _ = layout
    _identity(monkeypatch, "usr_ok")
    root = ws.ensure_workspace("usr_ok")
    assert os.path.realpath(root) == os.path.realpath(wsdir / "usr_ok")
    assert os.path.isdir(os.path.join(root, ".git"))
    assert ".sandbox/" in open(os.path.join(root, ".gitignore"), encoding="utf-8").read()
    # A symlinked usr_* entry that stays inside WORKSPACE_DIR is fine.
    os.symlink(wsdir / "usr_ok", wsdir / "usr_alias", target_is_directory=True)
    _identity(monkeypatch, "usr_alias")
    assert ws.ensure_workspace("usr_alias").endswith("usr_alias")
    # Legacy pre-existing directory still works.
    (wsdir / "15551234567").mkdir()
    monkeypatch.setattr("prax.services.identity_service.get_user", lambda uid: None)
    assert os.path.isdir(os.path.join(ws.ensure_workspace("+15551234567"), ".git"))
