"""Keyless tests for the builtin image-generation plugin (mocked caps + OpenAI)."""
from __future__ import annotations

import base64
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "image_plugin", Path("prax/plugins/tools/image/plugin.py"))
image_plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(image_plugin)


class FakeCaps:
    def __init__(self, secret="sk-test"):
        self.saved = []
        self._secret = secret

    def get_approved_secret(self, key):
        return self._secret

    def save_file(self, filename, content):
        assert isinstance(content, bytes) and content
        self.saved.append(filename)
        return f"/ws/active/{filename}"


def _install_fake_openai(monkeypatch, b64=True, capture=None):
    import types
    mod = types.ModuleType("openai")

    class _Img:
        b64_json = base64.b64encode(b"PNGDATA").decode() if b64 else None
        url = None if b64 else "http://x/img.png"

    class _Client:
        def __init__(self, api_key=None):
            pass
        class images:
            @staticmethod
            def generate(**kwargs):
                if capture is not None:
                    capture.update(kwargs)
                class _R:
                    data = [_Img()]
                return _R()
    mod.OpenAI = _Client
    monkeypatch.setitem(sys.modules, "openai", mod)


def _tool(caps):
    (tool,) = image_plugin.register(caps)
    return tool


def test_generates_and_saves_png(monkeypatch):
    cap = {}
    _install_fake_openai(monkeypatch, b64=True, capture=cap)
    caps = FakeCaps()
    out = _tool(caps).func("a red fox in snow")
    assert len(caps.saved) == 1 and caps.saved[0].startswith("image-a-red-fox")
    assert caps.saved[0].endswith(".png")
    assert "workspace_send_file" in out
    assert cap["prompt"] == "a red fox in snow" and cap["n"] == 1


def test_url_response_downloads(monkeypatch):
    _install_fake_openai(monkeypatch, b64=False)
    import types
    req = types.ModuleType("requests")
    class _Resp:
        content = b"PNGVIAURL"

        def raise_for_status(self):
            pass
    req.get = lambda *a, **k: _Resp()
    monkeypatch.setitem(sys.modules, "requests", req)
    caps = FakeCaps()
    out = _tool(caps).func("landscape")
    assert caps.saved and "workspace_send_file" in out


def test_missing_key_is_actionable():
    out = _tool(FakeCaps(secret=None)).func("anything")
    assert "OPENAI_KEY" in out and "isn't configured" in out


def test_empty_prompt_rejected():
    assert "No prompt" in _tool(FakeCaps()).func("   ")


def test_bad_model_falls_back_to_dalle(monkeypatch):
    cap = {}
    _install_fake_openai(monkeypatch, b64=True, capture=cap)
    from prax.settings import settings
    monkeypatch.setattr(settings, "image_model", "gpt-5.4-mini", raising=False)  # a chat model
    _tool(FakeCaps()).func("x")
    assert cap["model"] == "dall-e-3"  # non-image name → safe fallback
