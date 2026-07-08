"""Keyless tests for the builtin tts plugin (mocked caps)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "tts_plugin", Path("prax/plugins/tools/tts/plugin.py"))
tts_plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tts_plugin)


class FakeCaps:
    def __init__(self, fail=()):
        self.calls = []
        self.saved = []
        self._fail = set(fail)

    def tts_synthesize(self, text, output_path, voice="nova", provider="openai"):
        if provider in self._fail:
            raise RuntimeError(f"{provider} unavailable")
        self.calls.append({"provider": provider, "voice": voice, "path": output_path})
        # Real backends write bytes to output_path; mimic that so the plugin's
        # read-back + save_file path is exercised.
        with open(output_path, "wb") as f:
            f.write(b"ID3fake-mp3-bytes")
        return output_path

    def save_file(self, filename, content):
        assert isinstance(content, bytes) and content  # bytes reach active/
        self.saved.append(filename)
        return f"/ws/active/{filename}"


def _tool(caps):
    (tool,) = tts_plugin.register(caps)
    return tool


def test_auto_uses_openai_first():
    caps = FakeCaps()
    out = _tool(caps).func("Hello there, this is a test.")
    assert caps.calls[0]["provider"] == "openai"
    assert caps.calls[0]["voice"] == "nova"
    assert len(caps.saved) == 1 and caps.saved[0].startswith("tts-hello-there")
    assert "workspace_send_file" in out and "tts-hello-there" in out


def test_auto_falls_back_to_elevenlabs():
    caps = FakeCaps(fail={"openai"})
    out = _tool(caps).func("Fallback please.")
    assert caps.calls[0]["provider"] == "elevenlabs"
    assert caps.calls[0]["voice"] == tts_plugin._DEFAULT_VOICES["elevenlabs"]
    assert "provider=elevenlabs" in out


def test_explicit_provider_and_voice():
    caps = FakeCaps()
    out = _tool(caps).func("Specific voice.", provider="elevenlabs", voice="xyz123")
    assert caps.calls[0]["provider"] == "elevenlabs"
    assert caps.calls[0]["voice"] == "xyz123"
    assert caps.saved == [out.split("**")[1]]  # persisted via save_file
    assert "voice=xyz123" in out


def test_all_providers_failing_is_actionable():
    caps = FakeCaps(fail={"openai", "elevenlabs"})
    out = _tool(caps).func("Doomed.")
    assert "TTS failed" in out and "OPENAI_KEY" in out
    assert caps.calls == []


def test_overlong_text_rejected_with_guidance():
    caps = FakeCaps()
    out = _tool(caps).func("x" * 5000)
    assert "over the 4000-char" in out and caps.calls == []


def test_empty_text_rejected():
    assert _tool(FakeCaps()).func("   ") == "No text provided."
