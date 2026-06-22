"""Tests for prax.agent.vision_tools — local + remote OpenAI-compatible paths."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def _stub_openai_response(reply: str) -> MagicMock:
    """Build a fake openai.OpenAI() client whose chat.completions.create
    returns a single message with ``reply`` content."""
    completion = MagicMock()
    completion.choices = [MagicMock()]
    completion.choices[0].message.content = reply
    client = MagicMock()
    client.chat.completions.create.return_value = completion
    return client


def test_analyze_openai_uses_base_url_and_inlines_remote_image(monkeypatch):
    """Local llama-server path: base_url is set, http(s) URLs become base64
    data URIs (because local servers can't reach Discord/Twilio CDNs)."""
    from prax.agent import vision_tools
    from prax.settings import settings

    monkeypatch.setattr(settings, "vision_provider", "openai", raising=False)
    monkeypatch.setattr(settings, "vision_model", "qwen-vl-local", raising=False)
    monkeypatch.setattr(settings, "vision_base_url", "http://localhost:8083/v1", raising=False)
    monkeypatch.setattr(settings, "vision_api_key", None, raising=False)
    monkeypatch.setattr(settings, "openai_key", None, raising=False)

    monkeypatch.setattr(
        vision_tools,
        "_fetch_image_base64",
        lambda _url: ("ZmFrZS1pbWFnZQ==", "image/jpeg"),
    )

    fake_client = _stub_openai_response("local model said: hello world")

    with patch("openai.OpenAI", return_value=fake_client) as ctor:
        result = vision_tools._analyze_openai(
            "https://cdn.discordapp.com/some/img.jpg", "What's in this image?",
        )

    assert result == "local model said: hello world"
    # Constructor must receive the local base_url and a placeholder key.
    ctor.assert_called_once()
    kwargs = ctor.call_args.kwargs
    assert kwargs["base_url"] == "http://localhost:8083/v1"
    assert kwargs["api_key"]  # any non-empty placeholder is fine

    # The image_url block must be a base64 data URI, not the original CDN URL.
    create_kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert create_kwargs["model"] == "qwen-vl-local"
    content_blocks = create_kwargs["messages"][0]["content"]
    image_block = next(b for b in content_blocks if b["type"] == "image_url")
    assert image_block["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "cdn.discordapp.com" not in image_block["image_url"]["url"]


def test_analyze_openai_remote_inlines_image(monkeypatch):
    """Hosted OpenAI path now ALSO inlines the image as base64.  Passing a raw
    CDN URL through is unreliable — auth'd/expiring Discord/Twilio links fail in
    the provider's own fetcher — so we always download + inline with our UA."""
    from prax.agent import vision_tools
    from prax.settings import settings

    monkeypatch.setattr(settings, "vision_provider", "openai", raising=False)
    monkeypatch.setattr(settings, "vision_model", "gpt-4-vision", raising=False)
    monkeypatch.setattr(settings, "vision_base_url", None, raising=False)
    monkeypatch.setattr(settings, "vision_api_key", None, raising=False)
    monkeypatch.setattr(settings, "openai_key", "sk-real", raising=False)

    fetched = {"called": False}
    def _fetch(_url):
        fetched["called"] = True
        return ("ZmFrZQ==", "image/png")
    monkeypatch.setattr(vision_tools, "_fetch_image_base64", _fetch)

    fake_client = _stub_openai_response("openai said: a cat")
    with patch("openai.OpenAI", return_value=fake_client) as ctor:
        result = vision_tools._analyze_openai(
            "https://cdn.discordapp.com/img.jpg", "describe",
        )

    assert result == "openai said: a cat"
    # No base_url override, real openai_key forwarded.
    ctor.assert_called_once()
    kwargs = ctor.call_args.kwargs
    assert "base_url" not in kwargs
    assert kwargs["api_key"] == "sk-real"

    create_kwargs = fake_client.chat.completions.create.call_args.kwargs
    image_block = next(
        b for b in create_kwargs["messages"][0]["content"] if b["type"] == "image_url"
    )
    # Reliability over bandwidth: the image is inlined, never the raw CDN URL.
    assert image_block["image_url"]["url"].startswith("data:image/png;base64,")
    assert "cdn.discordapp.com" not in image_block["image_url"]["url"]
    assert fetched["called"] is True


def test_fetch_image_base64_reads_local_file(tmp_path):
    """analyze_image must be able to inspect a local file (e.g. a saved
    screenshot), not only a remote URL — read straight off disk, infer the
    media type from the suffix."""
    from prax.agent import vision_tools

    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n fake-image-bytes")
    b64, media = vision_tools._fetch_image_base64(str(png))
    import base64 as _b64
    assert _b64.standard_b64decode(b64).startswith(b"\x89PNG")
    assert media == "image/png"

    # file:// URLs and ~ expansion also resolve to the local file.
    b64b, mediab = vision_tools._fetch_image_base64(f"file://{png}")
    assert b64b == b64 and mediab == "image/png"


def test_openai_payload_inlines_local_path(tmp_path, monkeypatch):
    """A local path (a screenshot) must become a base64 data URI for the model —
    a remote server obviously can't open a host file path."""
    from prax.agent import vision_tools
    from prax.settings import settings

    monkeypatch.setattr(settings, "vision_base_url", None, raising=False)  # hosted path
    jpg = tmp_path / "cdp_screenshot_1.jpg"
    jpg.write_bytes(b"\xff\xd8\xff fake-jpeg")
    block = vision_tools._openai_image_payload(str(jpg))
    assert block["type"] == "image_url"
    assert block["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_analyze_openai_prefers_vision_api_key_over_openai_key(monkeypatch):
    """``VISION_API_KEY`` wins when both are set — lets users send vision
    traffic to a different endpoint without leaking their main OpenAI key."""
    from prax.agent import vision_tools
    from prax.settings import settings

    monkeypatch.setattr(settings, "vision_provider", "openai", raising=False)
    monkeypatch.setattr(settings, "vision_model", "qwen-vl", raising=False)
    monkeypatch.setattr(settings, "vision_base_url", "http://localhost:8083/v1", raising=False)
    monkeypatch.setattr(settings, "vision_api_key", "vision-only-key", raising=False)
    monkeypatch.setattr(settings, "openai_key", "sk-different", raising=False)
    monkeypatch.setattr(
        vision_tools,
        "_fetch_image_base64",
        lambda _url: ("ZmFrZQ==", "image/png"),
    )

    fake_client = _stub_openai_response("ok")
    with patch("openai.OpenAI", return_value=fake_client) as ctor:
        vision_tools._analyze_openai("https://x/img.png", "describe")

    assert ctor.call_args.kwargs["api_key"] == "vision-only-key"


def test_build_vision_tools_accepts_local_server_without_api_key(monkeypatch):
    """Regression: VISION_BASE_URL alone must be enough to register
    ``analyze_image`` — the prior gating dropped the tool when no
    OPENAI_KEY was set, even though local servers don't need one."""
    from prax.agent import vision_tools
    from prax.settings import settings

    monkeypatch.setattr(settings, "vision_provider", "openai", raising=False)
    monkeypatch.setattr(settings, "vision_model", "qwen-vl-local", raising=False)
    monkeypatch.setattr(settings, "vision_base_url", "http://localhost:8083/v1", raising=False)
    monkeypatch.setattr(settings, "vision_api_key", None, raising=False)
    monkeypatch.setattr(settings, "openai_key", None, raising=False)

    tools = vision_tools.build_vision_tools()
    assert [t.name for t in tools] == ["analyze_image"]


def test_build_vision_tools_skips_when_nothing_configured(monkeypatch):
    from prax.agent import vision_tools
    from prax.settings import settings

    monkeypatch.setattr(settings, "vision_provider", "openai", raising=False)
    monkeypatch.setattr(settings, "vision_model", "gpt-4-vision", raising=False)
    monkeypatch.setattr(settings, "vision_base_url", None, raising=False)
    monkeypatch.setattr(settings, "vision_api_key", None, raising=False)
    monkeypatch.setattr(settings, "openai_key", None, raising=False)

    assert vision_tools.build_vision_tools() == []


def test_analyze_image_records_local_provider_in_trace(monkeypatch):
    """Vision calls must show up in the same tier-choice ledger that chat
    LLM calls use, so a trace clearly marks which model handled each call.
    The provider tag should distinguish local from hosted endpoints."""
    from prax.agent import llm_factory, vision_tools
    from prax.settings import settings

    monkeypatch.setattr(settings, "vision_provider", "openai", raising=False)
    monkeypatch.setattr(settings, "vision_model", "qwen35-local", raising=False)
    monkeypatch.setattr(settings, "vision_base_url", "http://127.0.0.1:8083/v1", raising=False)
    monkeypatch.setattr(settings, "vision_api_key", None, raising=False)
    monkeypatch.setattr(settings, "openai_key", None, raising=False)
    monkeypatch.setattr(
        vision_tools,
        "_fetch_image_base64",
        lambda _url: ("ZmFrZQ==", "image/png"),
    )
    monkeypatch.setattr(
        vision_tools,
        "_analyze_openai",
        lambda _url, _prompt: "ok",
    )
    llm_factory.drain_tier_choices()  # clean slate

    vision_tools.analyze_image_impl("http://example/x.png", "describe")

    choices = llm_factory.drain_tier_choices()
    assert len(choices) == 1
    assert choices[0]["model"] == "qwen35-local"
    # Local endpoint must be visible in the provider field — that's how a
    # human reading a trace tells "ran locally" from "ran on api.openai.com".
    assert "127.0.0.1" in choices[0]["provider"]
    assert "openai" in choices[0]["provider"]


def test_analyze_image_records_hosted_provider_plain(monkeypatch):
    """When VISION_BASE_URL is unset, the recorded provider is just the
    plain provider name — no @host suffix to clutter the trace."""
    from prax.agent import llm_factory, vision_tools
    from prax.settings import settings

    monkeypatch.setattr(settings, "vision_provider", "openai", raising=False)
    monkeypatch.setattr(settings, "vision_model", "gpt-4-vision", raising=False)
    monkeypatch.setattr(settings, "vision_base_url", None, raising=False)
    monkeypatch.setattr(settings, "vision_api_key", None, raising=False)
    monkeypatch.setattr(settings, "openai_key", "sk-x", raising=False)
    monkeypatch.setattr(vision_tools, "_analyze_openai", lambda _u, _p: "ok")
    llm_factory.drain_tier_choices()

    vision_tools.analyze_image_impl("http://example/x.png", "describe")

    choices = llm_factory.drain_tier_choices()
    assert choices[0]["provider"] == "openai"
    assert choices[0]["model"] == "gpt-4-vision"


def test_trace_to_dict_surfaces_models_used(monkeypatch):
    """Per-node ``models_used`` in the trace JSON must list each distinct
    (provider, model, tier) tuple used during that span."""
    from prax.agent import trace as trace_mod

    graph = trace_mod.ExecutionGraph(trace_id="t-1")
    node = trace_mod.SpanNode(
        span_id="s-1",
        name="orchestrator",
        parent_id=None,
        trace_id="t-1",
        spoke_or_category="orchestrator",
    )
    graph.add_node(node)

    # Two distinct calls during the span: one chat (OpenAI gpt-5.4-nano) and
    # one vision (local Qwen).  Both must surface in the trace.
    graph.complete_node(
        "s-1",
        status="completed",
        summary="done",
        tier_choices=[
            {
                "provider": "openai",
                "model": "gpt-5.4-nano",
                "tier_requested": "low",
                "tier_resolved": "low",
                "span_id": "s-1",
            },
            {
                "provider": "openai@http://127.0.0.1:8083/v1",
                "model": "qwen35-local",
                "tier_requested": "vision",
                "tier_resolved": "vision",
                "span_id": "s-1",
            },
            # Duplicate of the first — must be deduped.
            {
                "provider": "openai",
                "model": "gpt-5.4-nano",
                "tier_requested": "low",
                "tier_resolved": "low",
                "span_id": "s-1",
            },
        ],
    )

    out = graph.to_dict()
    assert len(out["nodes"]) == 1
    used = out["nodes"][0]["models_used"]
    assert len(used) == 2
    # The chat model
    assert {"provider": "openai", "model": "gpt-5.4-nano", "tier": "low"} in used
    # The local-vision model — provider tagged with the local URL
    assert any(
        u["model"] == "qwen35-local"
        and u["provider"] == "openai@http://127.0.0.1:8083/v1"
        for u in used
    )


def test_trace_to_dict_models_used_empty_when_no_llm_calls():
    from prax.agent import trace as trace_mod

    graph = trace_mod.ExecutionGraph(trace_id="t-2")
    node = trace_mod.SpanNode(
        span_id="s-2", name="tool", parent_id=None, trace_id="t-2",
        spoke_or_category="tool",
    )
    graph.add_node(node)
    graph.complete_node("s-2", status="completed", summary="ok")

    out = graph.to_dict()
    assert out["nodes"][0]["models_used"] == []
