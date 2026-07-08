"""analyze_image must resolve bare filenames against the user's workspace —
the contract browser_screenshot's output implies."""
from __future__ import annotations

import base64

import prax.agent.vision_tools as vt


def test_bare_filename_resolves_via_workspace(monkeypatch, tmp_path):
    img = tmp_path / "screenshot-test-x.com.png"
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABh6FO1AAAAABJRU5ErkJggg==")
    img.write_bytes(png)

    from prax.agent.user_context import current_user_id
    token = current_user_id.set("usr_test")
    try:
        import prax.services.workspace_service as ws
        monkeypatch.setattr(ws, "workspace_root", lambda uid: str(tmp_path))
        b64, media = vt._fetch_image_base64("screenshot-test-x.com.png")
        assert media == "image/png"
        assert base64.b64decode(b64) == png
    finally:
        current_user_id.reset(token)


def test_absolute_path_still_works(tmp_path):
    img = tmp_path / "direct.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n123")
    b64, media = vt._fetch_image_base64(str(img))
    assert media == "image/png"


def test_missing_bare_filename_raises(monkeypatch, tmp_path):
    from prax.agent.user_context import current_user_id
    token = current_user_id.set("usr_test")
    try:
        import prax.services.workspace_service as ws
        monkeypatch.setattr(ws, "workspace_root", lambda uid: str(tmp_path))
        import pytest
        with pytest.raises(FileNotFoundError):
            vt._fetch_image_base64("nope.png")
    finally:
        current_user_id.reset(token)
