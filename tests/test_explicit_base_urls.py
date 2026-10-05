"""SDK clients get their base URL from settings, never from the environment.

On keyless Prax, OPENAI_KEY is the secrets proxy's access token and
OPENAI_BASE_URL points at the proxy. A client built without ``base_url`` asks
the SDK, which reads OPENAI_BASE_URL from the process environment — present
only because Flask's app.run() used to copy all of .env there, and hidden again
whenever an imported plugin loaded (the plugin loader replaced os.environ for
the whole process). Without it the token went to api.openai.com and failed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    """The live settings (conftest reloads them for every test)."""
    import prax.settings
    settings = prax.settings.settings
    for name in ("OPENAI_BASE_URL", "OPENAI_API_BASE", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "openai_base_url", "https://proxy.test/v1")
    monkeypatch.setattr(settings, "anthropic_base_url", "https://proxy.test/anthropic")
    monkeypatch.setattr(settings, "vision_base_url", None)
    monkeypatch.setattr(settings, "vision_api_key", None)
    return settings


class _Recorder:
    def __init__(self):
        self.kwargs: list[dict] = []

    def __call__(self, **kwargs):
        self.kwargs.append(kwargs)
        chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **k: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="a cat"))])))
        embeddings = SimpleNamespace(create=lambda **k: SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1, 0.2]) for _ in k["input"]]))
        messages = SimpleNamespace(create=lambda **k: SimpleNamespace(content=[SimpleNamespace(text="a dog")]))
        return SimpleNamespace(chat=chat, embeddings=embeddings, messages=messages)


@pytest.fixture
def no_fetch(monkeypatch):
    from prax.agent import vision_tools
    monkeypatch.setattr(vision_tools, "_fetch_image_base64", lambda url: ("aGk=", "image/png"))


def test_vision_with_openai_key_goes_where_openai_key_goes(monkeypatch, no_fetch):
    from prax.agent import vision_tools
    rec = _Recorder()
    monkeypatch.setattr("openai.OpenAI", rec)
    assert vision_tools._analyze_openai("https://x/y.png", "what is it?") == "a cat"
    assert rec.kwargs[0]["base_url"] == "https://proxy.test/v1"


def test_vision_with_its_own_server_or_key(monkeypatch, no_fetch, settings):
    from prax.agent import vision_tools
    rec = _Recorder()
    monkeypatch.setattr("openai.OpenAI", rec)
    monkeypatch.setattr(settings, "vision_base_url", "http://localhost:8083/v1")
    vision_tools._analyze_openai("https://x/y.png", "?")
    assert rec.kwargs[-1]["base_url"] == "http://localhost:8083/v1"
    monkeypatch.setattr(settings, "vision_base_url", None)
    monkeypatch.setattr(settings, "vision_api_key", "sk-vision-direct")
    vision_tools._analyze_openai("https://x/y.png", "?")
    assert "base_url" not in rec.kwargs[-1]          # its own key, OpenAI's own endpoint


def test_vision_anthropic(monkeypatch, no_fetch):
    import anthropic

    from prax.agent import vision_tools
    rec = _Recorder()
    monkeypatch.setattr(anthropic, "Anthropic", rec)
    vision_tools._analyze_anthropic("https://x/y.png", "?")
    assert rec.kwargs[0]["base_url"] == "https://proxy.test/anthropic"


def test_embeddings(monkeypatch, settings):
    from prax.services.memory import embedder
    rec = _Recorder()
    monkeypatch.setattr("openai.OpenAI", rec)
    monkeypatch.setattr(settings, "embedding_base_url", None, raising=False)
    assert embedder._embed_openai(["a", "b"], "text-embedding-3-small") == [[0.1, 0.2], [0.1, 0.2]]
    assert rec.kwargs[0]["base_url"] == "https://proxy.test/v1"
    monkeypatch.setattr(settings, "embedding_base_url", "http://localhost:9000/v1", raising=False)
    embedder._embed_openai(["a"], "m")
    assert rec.kwargs[-1]["base_url"] == "http://localhost:9000/v1"


@pytest.mark.parametrize("site", ["tts", "whisper", "cover", "latex"])
def test_raw_clients_go_through_openai_client(site, monkeypatch, tmp_path):
    """These built OpenAI(api_key=...) with no base URL."""
    from prax.agent import llm_factory
    built = []

    def fake_openai_client(**kwargs):
        built.append(kwargs)
        raise RuntimeError("stop here: only the construction is under test")

    monkeypatch.setattr(llm_factory, "openai_client", fake_openai_client)
    if site in ("tts", "whisper"):
        from prax.plugins.capabilities import PluginCapabilities
        caps = PluginCapabilities.__new__(PluginCapabilities)
        monkeypatch.setattr(caps, "_check_permission", lambda *_: None, raising=False)
        monkeypatch.setattr(caps, "_scoped_path", lambda p: p, raising=False)
        caps.plugin_rel_path = "test"
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"\x00")
        with pytest.raises(RuntimeError, match="stop here"):
            if site == "tts":
                caps.tts_synthesize("hello", str(tmp_path / "out.mp3"), provider="openai")
            else:
                caps.transcribe_audio(str(audio))
    elif site == "cover":
        from prax.services import library_service
        monkeypatch.setattr(library_service, "workspace_root", lambda _u: str(tmp_path))
        library_service.create_space("u", "Cover Test")
        library_service.generate_space_cover("u", "cover-test")   # logs and returns on error
    else:
        from prax.readers.latex import latext_gpt_tools as lg
        monkeypatch.setattr(lg, "get_twilio_client", lambda: SimpleNamespace(
            calls=lambda sid: SimpleNamespace(update=lambda **k: None)))
        try:
            lg.latex_to_english({"abstract": "$x$"}, "CA1", redirect=False)
        except RuntimeError:
            pass
    assert built == [{}]
