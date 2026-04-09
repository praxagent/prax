"""Tests for absolute workspace_dir resolution.

Relative ``WORKSPACE_DIR`` values must be resolved to absolute paths at
settings load time. If they weren't, subprocess calls that change CWD
(git, Hugo, etc.) could cause subsequent workspace lookups to resolve
against the wrong directory, producing nested corruption like
``workspaces/user1/workspaces/user2/``.
"""
from __future__ import annotations

import os
from unittest.mock import patch


def test_relative_workspace_dir_is_resolved_to_absolute():
    """A relative workspace_dir must be converted to absolute at validation."""
    with patch.dict(os.environ, {
        "WORKSPACE_DIR": "./workspaces",
        "FLASK_SECRET_KEY": "test-secret-key-long-enough-to-pass",
    }, clear=False):
        from prax.settings import AppSettings
        s = AppSettings()
        assert os.path.isabs(s.workspace_dir)
        assert s.workspace_dir.endswith("workspaces")


def test_parent_relative_workspace_dir_resolved():
    with patch.dict(os.environ, {
        "WORKSPACE_DIR": "../workspaces",
        "FLASK_SECRET_KEY": "test-secret-key-long-enough-to-pass",
    }, clear=False):
        from prax.settings import AppSettings
        s = AppSettings()
        assert os.path.isabs(s.workspace_dir)


def test_absolute_workspace_dir_unchanged():
    with patch.dict(os.environ, {
        "WORKSPACE_DIR": "/tmp/test-workspaces",
        "FLASK_SECRET_KEY": "test-secret-key-long-enough-to-pass",
    }, clear=False):
        from prax.settings import AppSettings
        s = AppSettings()
        assert s.workspace_dir == "/tmp/test-workspaces"


def test_empty_workspace_dir_not_resolved():
    """Empty string should pass through unchanged."""
    from prax.settings import AppSettings
    # Validator is a classmethod — call via __func__ to access the raw fn.
    raw = AppSettings.__dict__["_absolute_workspace_dir"].__func__
    assert raw(AppSettings, "") == ""


def test_validator_resolves_cwd_independently(tmp_path, monkeypatch):
    """The validator resolves against CWD at validation time — subsequent
    CWD changes don't affect the already-resolved absolute path."""
    # Set up a known CWD.
    first_cwd = tmp_path / "first"
    first_cwd.mkdir()
    monkeypatch.chdir(first_cwd)

    with patch.dict(os.environ, {
        "WORKSPACE_DIR": "./ws",
        "FLASK_SECRET_KEY": "test-secret-key-long-enough-to-pass",
    }, clear=False):
        from prax.settings import AppSettings
        s = AppSettings()
        expected = str(first_cwd / "ws")
        assert s.workspace_dir == expected

        # Change CWD — the already-loaded settings should be unaffected.
        second_cwd = tmp_path / "second"
        second_cwd.mkdir()
        monkeypatch.chdir(second_cwd)
        assert s.workspace_dir == expected  # still the original path
