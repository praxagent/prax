"""Keyless-client contract — raw OpenAI SDK clients go through ``llm_factory.openai_client()``.

Why this exists (live bug, 2026-09-07): on the keyless daily driver every fire of
an hourly schedule died with ``openai.BadRequestError: invalid model ID`` from
``prax/conversation_memory.py`` — a bare ``OpenAI(api_key=settings.openai_key)``
with no ``base_url`` sent ``BASE_MODEL`` to api.openai.com, bypassing everything
``build_llm`` knows (provider routing, ``OPENAI_BASE_URL`` → the secrets proxy,
``OPENAI_BASE_URL_IS_OPENAI``).  The same pattern existed in four more modules.

Contract enforced here:

1. ``openai.OpenAI(...)`` is constructed in exactly one place — inside
   :func:`prax.agent.llm_factory.openai_client` — plus an explicit grandfathered
   list that is meant only to shrink (a new site fails; a stale entry warns, as
   ``scripts/check_layers.py`` does for its allowlist).  The scan is AST-based,
   so strings, comments and docstrings do not count.
2. ``openai_client()`` derives key and base URL exactly as ``build_llm``'s
   OpenAI branch does (equivalence-tested against the SDK client that
   ``ChatOpenAI`` builds) and refuses ``api_key`` / ``base_url`` overrides.
3. The plain chat-completion call sites (history summariser, LaTeX reader, web
   summary) go through ``build_llm(tier="low")`` with byte-identical prompts;
   the audio call sites use ``openai_client()``.

Everything here runs keyless — fakes only, no network.
"""
from __future__ import annotations

import ast
import warnings
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

REPO_ROOT = Path(__file__).resolve().parents[1]
PRAX_ROOT = REPO_ROOT / "prax"

SANCTIONED_FILE = "prax/agent/llm_factory.py"
SANCTIONED_FUNC = "openai_client"

# Direct constructions that predate ``openai_client()`` and sit outside the
# 2026-09 keyless-clients change.  This list is meant only to SHRINK: route a
# site through ``openai_client()`` (or, for plugin code that may not import
# ``prax.agent``, through a capability) and delete its entry.  A NEW site fails
# the test; a stale entry warns (house pattern: check_layers fails on new
# violations and only warns on stale allowlist entries outside --strict).
GRANDFATHERED: dict[str, str] = {
    "prax/agent/vision_tools.py": "own VISION_BASE_URL / VISION_API_KEY — deliberate, documented",
    "prax/services/memory/embedder.py": "own EMBEDDING_BASE_URL — deliberate, documented",
    "prax/plugins/capabilities.py": "TTS + Whisper capabilities — still bypasses OPENAI_BASE_URL",
    "prax/services/library_service.py": "cover-image generation — still bypasses OPENAI_BASE_URL",
    "prax/plugins/tools/image/plugin.py": "image plugin — needs a capability (plugins can't import prax.agent)",
    "prax/readers/latex/latext_gpt_tools.py": "voice LaTeX reader — still bypasses OPENAI_BASE_URL",
}

_CLIENT_CLASSES = {"OpenAI", "AsyncOpenAI"}


def _walk_calls(node: ast.AST, func: str | None):
    """Yield ``(lineno, enclosing_function)`` for every OpenAI-client constructor call."""
    for child in ast.iter_child_nodes(node):
        child_func = child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else func
        if isinstance(child, ast.Call):
            callee = child.func
            name = None
            if isinstance(callee, ast.Name):
                name = callee.id
            elif isinstance(callee, ast.Attribute):
                name = callee.attr
            if name in _CLIENT_CLASSES:
                yield (child.lineno, func)
        yield from _walk_calls(child, child_func)


def _raw_openai_constructions() -> dict[str, list[tuple[int, str | None]]]:
    """Map repo-relative path → hits for every ``OpenAI(...)`` / ``AsyncOpenAI(...)``
    / ``openai.OpenAI(...)`` call under ``prax/`` (source only, not tests)."""
    found: dict[str, list[tuple[int, str | None]]] = {}
    for path in sorted(PRAX_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = list(_walk_calls(tree, None))
        if hits:
            found[path.relative_to(REPO_ROOT).as_posix()] = hits
    return found


# ---------------------------------------------------------------------------
# 1. Single construction site
# ---------------------------------------------------------------------------


class TestSingleConstructionSite:
    def test_scanner_sees_the_sanctioned_site(self):
        # Guards the scan itself: a scanner that finds nothing would pass the
        # next two tests vacuously.
        found = _raw_openai_constructions()
        assert SANCTIONED_FILE in found, "no OpenAI() call found in llm_factory — scan broken or helper gone"

    def test_no_raw_client_outside_openai_client_or_grandfathered(self):
        found = _raw_openai_constructions()
        allowed = set(GRANDFATHERED) | {SANCTIONED_FILE}
        rogue = {p: hits for p, hits in found.items() if p not in allowed}
        assert not rogue, (
            "Raw OpenAI(...) constructed outside llm_factory.openai_client(): "
            f"{rogue}. Use prax.agent.llm_factory.openai_client() (audio/images) or "
            "build_llm() (chat) so OPENAI_BASE_URL / keyless routing applies."
        )

    def test_grandfathered_entries_are_still_live(self):
        found = _raw_openai_constructions()
        stale = set(GRANDFATHERED) - set(found)
        if stale:
            warnings.warn(
                f"GRANDFATHERED entries no longer construct a raw OpenAI client — delete them: {sorted(stale)}",
                stacklevel=1,
            )

    def test_llm_factory_constructs_only_inside_openai_client(self):
        hits = _raw_openai_constructions()[SANCTIONED_FILE]
        assert [func for _, func in hits] == [SANCTIONED_FUNC], hits


# ---------------------------------------------------------------------------
# 2. openai_client() semantics == build_llm's OpenAI branch
# ---------------------------------------------------------------------------


@pytest.fixture
def factory(monkeypatch):
    """``prax.agent.llm_factory`` with the SDK's own env fallbacks neutralised."""
    from prax.agent import llm_factory

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    return llm_factory


class TestOpenaiClient:
    def test_uses_proxy_base_url_and_token(self, factory, monkeypatch):
        monkeypatch.setattr(factory.settings, "openai_base_url", "https://proxy.test/v1")
        monkeypatch.setattr(factory.settings, "openai_key", "proxy-access-token")
        client = factory.openai_client()
        assert str(client.base_url) == "https://proxy.test/v1/"
        assert client.api_key == "proxy-access-token"

    def test_defaults_to_openai_when_no_base_url(self, factory, monkeypatch):
        monkeypatch.setattr(factory.settings, "openai_base_url", None)
        client = factory.openai_client()
        assert str(client.base_url) == "https://api.openai.com/v1/"
        assert client.api_key == "sk-test"  # conftest TEST_ENV

    def test_requires_key_exactly_like_build_llm(self, factory, monkeypatch):
        monkeypatch.setattr(factory.settings, "openai_key", None)
        with pytest.raises(ValueError, match="OPENAI_KEY"):
            factory.openai_client()
        with pytest.raises(ValueError, match="OPENAI_KEY"):
            factory.build_llm(provider="openai", model="gpt-test")

    @pytest.mark.parametrize("pinned", ["api_key", "base_url"])
    def test_cannot_override_routing(self, factory, pinned):
        with pytest.raises(TypeError, match=pinned):
            factory.openai_client(**{pinned: "https://elsewhere.test/v1"})

    def test_other_sdk_kwargs_pass_through(self, factory):
        client = factory.openai_client(timeout=7.5, max_retries=0)
        assert client.timeout == 7.5
        assert client.max_retries == 0

    @pytest.mark.parametrize(
        "base_url,is_openai",
        [
            (None, False),                                # direct api.openai.com
            ("https://proxy.test/v1", True),             # keyless secrets proxy
            ("https://openrouter.test/api/v1", False),   # third-party provider
        ],
    )
    def test_equivalent_to_build_llm_openai_branch(self, factory, monkeypatch, base_url, is_openai):
        # Equivalence: the raw client must land on the same host with the same
        # credential as the SDK client ChatOpenAI builds inside build_llm().
        monkeypatch.setattr(factory.settings, "openai_base_url", base_url)
        monkeypatch.setattr(factory.settings, "openai_base_url_is_openai", is_openai)
        monkeypatch.setattr(factory.settings, "openai_key", "k-equiv")
        chat = factory.build_llm(provider="openai", model="gpt-test")
        raw = factory.openai_client()
        assert raw.base_url == chat.root_client.base_url
        assert raw.api_key == chat.root_client.api_key == "k-equiv"


# ---------------------------------------------------------------------------
# 3. Call sites route through build_llm / openai_client
# ---------------------------------------------------------------------------


class _FakeLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[tuple[list, dict]] = []

    def invoke(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return AIMessage(content=self.reply)


def _capture_build_llm(monkeypatch, fake: _FakeLLM) -> dict:
    from prax.agent import llm_factory

    seen: dict = {}

    def fake_build_llm(**kwargs):
        seen.update(kwargs)
        return fake

    monkeypatch.setattr(llm_factory, "build_llm", fake_build_llm)
    return seen


class TestHistorySummariser:
    HISTORY = [
        {"role": role, "content": f"m{i}", "date": f"d{i}"}
        for i, role in enumerate(["system", "user", "assistant", "user", "assistant", "user"])
    ]

    def test_summarize_and_replace_routes_through_build_llm(self, monkeypatch):
        from prax import conversation_memory as cm

        fake = _FakeLLM("condensed")
        seen = _capture_build_llm(monkeypatch, fake)
        # Force the summarisation branch without depending on tiktoken.
        monkeypatch.setattr(cm, "num_tokens_from_string", lambda *_a, **_k: 10**9)

        out = cm.summarize_and_replace(list(self.HISTORY), max_size=100)

        assert seen == {"tier": "low", "temperature": 0.3}
        [(messages, kwargs)] = fake.calls
        assert kwargs == {}
        # Prompt preserved byte-for-byte (sic: the legacy text has no space
        # before "what" — fixing that is a prompt change, not this fix).
        joined = "; ".join(f"role: {e['role']}, content: {e['content']}" for e in self.HISTORY[1:4])
        assert messages == [{
            "role": "assistant",
            "content": "Please succinctly summarize the following text, including both"
                       f"what the user and system said: {joined}",
        }]
        assert out == [
            self.HISTORY[0],
            {"date": "d3", "role": "assistant", "content": "Summary of prior chats: condensed"},
            *self.HISTORY[4:],
        ]
        assert not hasattr(cm, "OpenAI") and not hasattr(cm, "_openai_client")

    def test_below_threshold_is_a_pure_passthrough(self, monkeypatch):
        # Equivalence for the untouched branch: no LLM built, list unchanged.
        from prax import conversation_memory as cm
        from prax.agent import llm_factory

        monkeypatch.setattr(llm_factory, "build_llm", lambda **_k: pytest.fail("build_llm must not be called"))
        monkeypatch.setattr(cm, "num_tokens_from_string", lambda *_a, **_k: 0)
        assert cm.summarize_and_replace(list(self.HISTORY), max_size=100) == self.HISTORY


class TestLatexReader:
    def test_chunk_to_english_routes_through_build_llm(self, monkeypatch):
        from prax.readers.latex import latex_functions as lf

        fake = _FakeLLM("x squared")
        seen = _capture_build_llm(monkeypatch, fake)

        assert lf.latex_chunk_to_english("$x^2$") == ["x squared"]

        assert seen == {"tier": "low"}
        [(messages, kwargs)] = fake.calls
        assert kwargs == {}
        assert [m["role"] for m in messages] == ["system", "user", "user"]
        assert messages[0]["content"] == "You are a helpful assistant."
        assert messages[-1] == {"role": "user", "content": "$x^2$"}
        assert not hasattr(lf, "client") and not hasattr(lf, "OpenAI")

    def test_latex_to_english_uses_twilio_for_call_redirects(self, monkeypatch):
        # The redirects were issued on the OpenAI client object (no ``.calls``);
        # they always meant Twilio, as in the sibling latext_gpt_tools.
        from prax.readers.latex import latex_functions as lf

        fake = _FakeLLM("x sub one")
        _capture_build_llm(monkeypatch, fake)
        redirects: list[tuple[str, str]] = []
        twilio = SimpleNamespace(
            calls=lambda sid: SimpleNamespace(update=lambda url, method: redirects.append((sid, url)))
        )
        monkeypatch.setattr(lf, "get_twilio_client", lambda: twilio)
        monkeypatch.setattr(lf.settings, "ngrok_url", "https://ngrok.test")
        monkeypatch.setitem(lf.convo_states, "CA123", {})

        lf.latex_to_english({"abstract": "$x_1$"}, "CA123")

        assert lf.convo_states["CA123"]["read_buffer"] == ["x sub one"]
        assert lf.convo_states["CA123"]["buffer_redirect"] == "https://ngrok.test/reader"
        assert redirects == [("CA123", "https://ngrok.test/conference"), ("CA123", "https://ngrok.test/read")]
        [(messages, _)] = fake.calls
        assert messages[-1] == {"role": "user", "content": "$x_1$"}


class TestAudioSites:
    def test_transcribe_audio_uses_openai_client(self, monkeypatch, tmp_path):
        from prax.agent import llm_factory
        from prax.services import youtube_service

        transcriptions: list[dict] = []

        def create(**kwargs):
            transcriptions.append(kwargs)
            return SimpleNamespace(text="hello")

        fake_client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
        constructed: list[dict] = []

        def fake_openai_client(**kwargs):
            constructed.append(kwargs)
            return fake_client

        monkeypatch.setattr(llm_factory, "openai_client", fake_openai_client)
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"\x00")

        assert youtube_service.transcribe_audio(str(audio)) == "hello"

        assert constructed == [{}]
        assert transcriptions[0]["model"] == "whisper-1"
        assert not hasattr(youtube_service, "OpenAI") and not hasattr(youtube_service, "_openai_client")

    def test_web_summary_routes_chat_and_tts(self, monkeypatch, tmp_path):
        from prax.agent import llm_factory
        from prax.readers.web import web2mp3

        # --- fake page fetch -------------------------------------------------
        page = SimpleNamespace(
            set_default_timeout=lambda ms: None,
            goto=lambda url: None,
            text_content=lambda selector: "Body text of the page",
        )
        browser = SimpleNamespace(new_page=lambda: page, close=lambda: None)

        @contextmanager
        def fake_sync_playwright():
            yield SimpleNamespace(chromium=SimpleNamespace(launch=lambda headless=True: browser))

        monkeypatch.setattr(web2mp3, "sync_playwright", fake_sync_playwright)

        # --- fake mutagen (a real MP3 frame is out of scope) ---------------------
        class _FakeMP3(dict):
            def __init__(self, path, ID3=None):
                super().__init__()
                self.path = path

            def save(self):
                pass

        monkeypatch.setattr(web2mp3, "MP3", _FakeMP3)
        monkeypatch.chdir(tmp_path)  # output goes to ./static/temp/<user>/

        # --- chat via build_llm, TTS via openai_client -------------------------
        fake = _FakeLLM("A short summary.")
        seen = _capture_build_llm(monkeypatch, fake)
        tts_inputs: list[dict] = []

        def speech_create(**kwargs):
            tts_inputs.append(kwargs)
            return SimpleNamespace(stream_to_file=lambda path: Path(path).write_bytes(b"mp3"))

        fake_client = SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(create=speech_create)))
        monkeypatch.setattr(llm_factory, "openai_client", lambda **_k: fake_client)

        link, summary = web2mp3.convert_web_to_mp3("https://example.test/article", "u1")

        assert summary == "A short summary."
        assert link.startswith(f"{web2mp3.NGROK_URL}/static/temp/u1/") and link.endswith(".mp3")
        assert (tmp_path / "static" / "temp" / "u1").exists()
        assert seen == {"tier": "low"}
        [(messages, kwargs)] = fake.calls
        assert kwargs == {}
        assert len(messages) == 1 and messages[0]["role"] == "system"
        assert "Body text of the page" in messages[0]["content"]
        assert tts_inputs == [{"model": "tts-1", "voice": "shimmer", "input": "A short summary."}]
        assert not hasattr(web2mp3, "OpenAI") and not hasattr(web2mp3, "_openai_client")
