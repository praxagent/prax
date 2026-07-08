"""workspace_send_file resolves sandbox-written files (root + /workspace prefix)."""
from __future__ import annotations

import prax.agent.workspace_tools as wt

_captured: dict = {}


def _setup(monkeypatch, tmp_path):
    (tmp_path / "active").mkdir(exist_ok=True)
    monkeypatch.setattr(wt.workspace_service, "_workspace_root", lambda uid: str(tmp_path))
    monkeypatch.setattr(wt, "_get_user_id", lambda: "u1")
    _captured.clear()
    # Capture the resolved path, then abort delivery so the test stays offline.
    orig = wt.os.path.getsize

    def _cap(p):
        _captured["p"] = p
        raise RuntimeError("stop-after-resolve")
    monkeypatch.setattr(wt.os.path, "getsize", _cap)
    return orig


def _resolve(monkeypatch, tmp_path, filename):
    _setup(monkeypatch, tmp_path)
    try:
        wt.workspace_send_file.func(filename)
    except RuntimeError:
        pass
    return _captured.get("p")


def test_resolves_from_active(monkeypatch, tmp_path):
    (tmp_path / "active").mkdir(exist_ok=True)
    (tmp_path / "active" / "a.mp3").write_bytes(b"x")
    assert _resolve(monkeypatch, tmp_path, "a.mp3") == str(tmp_path / "active" / "a.mp3")


def test_resolves_from_workspace_root(monkeypatch, tmp_path):
    (tmp_path / "b.mp3").write_bytes(b"x")  # sandbox wrote to /workspace/b.mp3
    assert _resolve(monkeypatch, tmp_path, "b.mp3") == str(tmp_path / "b.mp3")


def test_strips_sandbox_absolute_prefix(monkeypatch, tmp_path):
    (tmp_path / "c.mp3").write_bytes(b"x")
    assert _resolve(monkeypatch, tmp_path, "/workspace/c.mp3") == str(tmp_path / "c.mp3")


def test_missing_reports_both_locations(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    out = wt.workspace_send_file.func("nope.mp3")
    assert "checked active/ and workspace root" in out


def test_path_traversal_blocked(monkeypatch, tmp_path):
    secret = tmp_path.parent / "secret_xyz"
    secret.mkdir(exist_ok=True)
    (secret / "s.txt").write_bytes(b"x")
    _setup(monkeypatch, tmp_path)
    out = wt.workspace_send_file.func("../secret_xyz/s.txt")
    assert "not found" in out and _captured.get("p") is None
