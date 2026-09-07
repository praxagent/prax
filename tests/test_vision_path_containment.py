"""analyze_image local-path containment + SSRF guard on the http(s) fetch.

Review 2026-09-05: ``_fetch_image_base64`` read ANY local path the model named
(``/etc/hostname``, ``file:///…``, or ``.env`` relative to the process CWD)
and shipped the bytes to the vision provider; the http(s) branch used a bare
``requests.get`` with no SSRF guard. The rule now: inside the user's
workspace, or exactly the temp-dir screenshot files the harness's own tools
write (``cdp_screenshot_<n>.jpg``, ``browser_<x>.png`` in ``tempfile.gettempdir()``;
``screenshot_<n>.png`` in ``/tmp``) — nothing else.
"""
from __future__ import annotations

import base64
import os
import tempfile

import pytest
import requests

import prax.agent.vision_tools as vt
from prax.agent.user_context import current_user_id
from prax.utils import ssrf

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABh6FO1AAAAABJRU5ErkJggg=="
)


@pytest.fixture
def user_ws(tmp_path, monkeypatch):
    ws = tmp_path / "ws" / "usr_v"
    (ws / "active").mkdir(parents=True)
    import prax.services.workspace_service as wss
    monkeypatch.setattr(wss, "workspace_root", lambda uid: str(ws))
    token = current_user_id.set("usr_v")
    yield ws
    current_user_id.reset(token)


@pytest.fixture
def scratch_tmp(tmp_path, monkeypatch):
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmpdir))
    return tmpdir


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("ref", ["/etc/hostname", "file:///etc/hostname", "~/.bashrc"])
def test_host_paths_refused(user_ws, ref):
    with pytest.raises(PermissionError):
        vt._fetch_image_base64(ref)


def test_absolute_path_outside_workspace_refused(user_ws, tmp_path):
    stray = tmp_path / "stray.png"
    stray.write_bytes(PNG)
    with pytest.raises(PermissionError):
        vt._fetch_image_base64(str(stray))


def test_relative_path_never_reads_process_cwd(user_ws, tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "secret.png").write_bytes(PNG)
    monkeypatch.chdir(cwd)
    with pytest.raises(FileNotFoundError):
        vt._fetch_image_base64("secret.png")


def test_relative_traversal_out_of_workspace_refused(user_ws, tmp_path):
    (tmp_path / "outside.png").write_bytes(PNG)
    with pytest.raises(PermissionError):
        vt._fetch_image_base64("../../outside.png")


def test_relative_path_without_user_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.png").write_bytes(PNG)
    with pytest.raises(PermissionError):
        vt._fetch_image_base64("x.png")


def test_screenshot_dir_is_name_pattern_exact(scratch_tmp):
    (scratch_tmp / "other.jpg").write_bytes(PNG)
    (scratch_tmp / "cdp_screenshot_abc.jpg").write_bytes(PNG)
    (scratch_tmp / "screenshot_1.png").write_bytes(PNG)  # desktop pattern lives in /tmp, not gettempdir()
    for name in ("other.jpg", "cdp_screenshot_abc.jpg", "screenshot_1.png"):
        with pytest.raises(PermissionError):
            vt._fetch_image_base64(str(scratch_tmp / name))
    # Right name, wrong directory.
    elsewhere = scratch_tmp.parent / "cdp_screenshot_1.jpg"
    elsewhere.write_bytes(PNG)
    with pytest.raises(PermissionError):
        vt._fetch_image_base64(str(elsewhere))


def test_symlinked_screenshot_name_refused(scratch_tmp):
    link = scratch_tmp / "cdp_screenshot_99.jpg"
    os.symlink("/etc/hostname", link)
    with pytest.raises(PermissionError):
        vt._fetch_image_base64(str(link))


# --------------------------------------------------------------------------- #
# Allowed
# --------------------------------------------------------------------------- #

def test_harness_screenshots_allowed_without_user(scratch_tmp):
    for name in ("cdp_screenshot_1700000000.jpg", "browser_k3jd8f2.png"):
        (scratch_tmp / name).write_bytes(PNG)
        b64, _ = vt._fetch_image_base64(str(scratch_tmp / name))
        assert base64.b64decode(b64) == PNG


def test_workspace_paths_allowed(user_ws):
    (user_ws / "active" / "shot.png").write_bytes(PNG)
    (user_ws / "root.png").write_bytes(PNG)
    for ref in ("shot.png", "root.png", str(user_ws / "active" / "shot.png"),
                f"file://{user_ws / 'root.png'}"):
        b64, media = vt._fetch_image_base64(ref)
        assert media == "image/png" and base64.b64decode(b64) == PNG


# --------------------------------------------------------------------------- #
# http(s) — through the SSRF guard
# --------------------------------------------------------------------------- #

def test_http_fetch_refuses_internal_targets_without_network(monkeypatch):
    monkeypatch.setattr(ssrf.settings, "ssrf_protection_enabled", True)
    monkeypatch.setattr(ssrf.settings, "ssrf_allowed_hosts", "")
    calls = []

    def _no_network(*a, **k):
        calls.append(a)
        raise AssertionError("network call made")

    monkeypatch.setattr(requests, "get", _no_network)
    for url in ("http://169.254.169.254/latest/x.jpg", "http://127.0.0.1:6333/x.png"):
        with pytest.raises(ssrf.SSRFError):
            vt._fetch_image_base64(url)
    assert calls == []
