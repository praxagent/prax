"""Vision tools — provider-agnostic image understanding.

Gives the agent the ability to analyze images via a configurable vision
model.  Supports OpenAI, Anthropic, and Google out of the box.  The
provider and model are set via VISION_PROVIDER and VISION_MODEL env vars.
"""
from __future__ import annotations

import base64
import logging
import re
import tempfile
from pathlib import Path

from langchain_core.tools import tool

from prax.settings import settings
from prax.utils.ssrf import safe_request

logger = logging.getLogger(__name__)

# Maximum image size to download for base64 encoding (10 MB).
_MAX_IMAGE_BYTES = 10 * 1024 * 1024

# The only files OUTSIDE the user's workspace that analyze_image may read: the
# temp-dir screenshots the harness's own tools write, matched by exact
# directory + basename pattern per writer. Nothing else on the host filesystem.
#   prax_sandbox.cdp_service.screenshot          -> <tempdir>/cdp_screenshot_<epoch>.jpg
#   browser_service.screenshot(save_to_workspace=False) -> <tempdir>/browser_<mkstemp>.png
#   sandbox_tools.desktop_screenshot             -> /tmp/screenshot_<epoch>.png (literal /tmp)
_CDP_SCREENSHOT_RE = re.compile(r"cdp_screenshot_\d+\.jpg")
_BROWSER_SCREENSHOT_RE = re.compile(r"browser_[A-Za-z0-9_]+\.png")
_DESKTOP_SCREENSHOT_RE = re.compile(r"screenshot_\d+\.png")


def _screenshot_allowlist() -> list[tuple[Path, re.Pattern[str]]]:
    tmp = Path(tempfile.gettempdir()).resolve()
    return [
        (tmp, _CDP_SCREENSHOT_RE),
        (tmp, _BROWSER_SCREENSHOT_RE),
        (Path("/tmp").resolve(), _DESKTOP_SCREENSHOT_RE),
    ]


def _is_harness_screenshot(resolved: Path) -> bool:
    return any(
        resolved.parent == d and rx.fullmatch(resolved.name) is not None
        for d, rx in _screenshot_allowlist()
    )


def _resolve_local_image_path(ref: str) -> Path:
    """Map a local image reference to a readable path, or raise.

    Allowed: a bare/relative name, resolved inside the current user's
    workspace (``active/`` first — where browser_screenshot saves — then the
    root); an absolute path inside that workspace; or one of the harness's
    own temp-dir screenshot files (``_screenshot_allowlist``). Everything
    else is refused with ``PermissionError``: this tool used to read any path
    the model named (``/etc/hostname``, or ``.env`` relative to the process
    CWD) and ship the bytes to the vision provider.
    """
    p = Path(ref).expanduser()
    ws_root: Path | None = None
    try:
        from prax.agent.user_context import current_user_id
        from prax.services.workspace_service import workspace_root
        uid = current_user_id.get()
        if uid:
            ws_root = Path(workspace_root(uid)).resolve()
    except Exception:
        logger.debug("workspace path resolution failed for %s", ref, exc_info=True)

    if not p.is_absolute():
        if ws_root is None:
            raise PermissionError(
                f"analyze_image: cannot resolve relative path {ref!r} without a user workspace"
            )
        for candidate in (ws_root / "active" / p, ws_root / p):
            if candidate.is_file():
                p = candidate
                break
        else:
            raise FileNotFoundError(f"analyze_image: {ref!r} not found in the workspace")

    resolved = p.resolve()
    if ws_root is not None and resolved.is_relative_to(ws_root):
        return resolved
    if _is_harness_screenshot(resolved):
        return resolved
    raise PermissionError(
        f"analyze_image: refusing to read {ref!r} — a local path must be inside "
        "your workspace or be a harness screenshot file"
    )


def _media_type_for_suffix(suffix: str) -> str:
    s = suffix.lower().lstrip(".")
    if s == "png":
        return "image/png"
    if s == "gif":
        return "image/gif"
    if s == "webp":
        return "image/webp"
    return "image/jpeg"


def _fetch_image_base64(url: str) -> tuple[str, str]:
    """Return ``(base64_data, media_type)`` for an image.

    Accepts an http(s) URL **or a local file path** (also a ``file://`` URL), so
    the agent can inspect a screenshot/file it produced — not only remote URLs.
    Sends an explicit ``User-Agent`` for http(s) because several image hosts
    (Wikimedia, some news CDNs) reject ``python-requests``'s default UA with 403.
    """
    # Local file path (or file:// URL): read off disk, but only from the user's
    # workspace or a harness screenshot file (see _resolve_local_image_path).
    if url.startswith("file://"):
        url = url[len("file://"):]
    if not url.startswith(("http://", "https://")):
        p = _resolve_local_image_path(url)
        data = p.read_bytes()[:_MAX_IMAGE_BYTES]
        return base64.standard_b64encode(data).decode("ascii"), _media_type_for_suffix(p.suffix)
    headers = {
        "User-Agent": (
            "PraxAssistant/1.0 (+https://github.com/PraxAssistant/prax) "
            "image-fetch/vision-tools"
        ),
        "Accept": "image/*,*/*;q=0.8",
    }
    # SSRF guard with per-hop redirect revalidation — the URL is model-controlled.
    resp = safe_request("get", url, timeout=30, stream=True, headers=headers)
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "image/jpeg")
    # Normalize content type.
    if "png" in content_type:
        media_type = "image/png"
    elif "gif" in content_type:
        media_type = "image/gif"
    elif "webp" in content_type:
        media_type = "image/webp"
    else:
        media_type = "image/jpeg"
    data = resp.content[:_MAX_IMAGE_BYTES]
    return base64.standard_b64encode(data).decode("ascii"), media_type


def _openai_image_payload(image_url: str) -> dict:
    """Return the ``image_url`` content block, always inlined as a base64 ``data:`` URI.

    We inline (download/read + base64) rather than passing a raw URL to the model
    server because (a) local model servers have no outbound network, (b) hosted
    fetchers (incl. OpenAI) intermittently fail on auth'd/expiring CDN links
    (Discord/Twilio), and (c) a local file path obviously can't be fetched by a
    remote server.  Inlining with our own browser-y UA is reliable across every
    provider and source — at the cost of a few encoded bytes, which we accept.
    """
    b64_data, media_type = _fetch_image_base64(image_url)
    data_uri = f"data:{media_type};base64,{b64_data}"
    return {"type": "image_url", "image_url": {"url": data_uri}}


def _analyze_openai(image_url: str, prompt: str) -> str:
    """Analyze an image via the OpenAI API or any OpenAI-compatible server.

    Honors ``VISION_BASE_URL`` (e.g. ``http://localhost:8083/v1`` for a local
    llama.cpp ``llama-server`` or vLLM/Ollama endpoint) and ``VISION_API_KEY``
    (falls back to ``OPENAI_KEY``; local servers usually accept any string).
    """
    from openai import OpenAI

    api_key = settings.vision_api_key or settings.openai_key or "local-no-key"
    client_kwargs: dict = {"api_key": api_key}
    if settings.vision_base_url:
        client_kwargs["base_url"] = settings.vision_base_url

    client = OpenAI(**client_kwargs)
    response = client.chat.completions.create(
        model=settings.vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    _openai_image_payload(image_url),
                ],
            }
        ],
        # Modern OpenAI chat models reject the legacy `max_tokens` param
        # ("use 'max_completion_tokens'"), which broke every vision call.
        max_completion_tokens=2000,
    )
    return response.choices[0].message.content


def _analyze_anthropic(image_url: str, prompt: str) -> str:
    """Analyze an image using the Anthropic API."""
    import anthropic

    b64_data, media_type = _fetch_image_base64(image_url)
    client = anthropic.Anthropic(api_key=settings.anthropic_key)
    response = client.messages.create(
        model=settings.vision_model,
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    return response.content[0].text


def _analyze_google(image_url: str, prompt: str) -> str:
    """Analyze an image using the Google Generative AI API."""
    import google.generativeai as genai

    genai.configure(api_key=settings.google_api_key)
    model = genai.GenerativeModel(settings.vision_model)
    b64_data, media_type = _fetch_image_base64(image_url)
    image_part = {"mime_type": media_type, "data": base64.standard_b64decode(b64_data)}
    response = model.generate_content([prompt, image_part])
    return response.text


def analyze_image_impl(image_url: str, prompt: str) -> str:
    """Route to the correct provider for image analysis."""
    provider = settings.vision_provider.lower()
    logger.info(
        "analyze_image → provider=%s model=%s url=%s prompt=%s",
        provider, settings.vision_model, image_url[:80], prompt[:80],
    )

    # Record this choice in the trace so the user can see whether vision went
    # to OpenAI, a local llama-server, etc. — same mechanism the chat path
    # uses, so model attribution is uniform across the trace.
    try:
        from prax.agent.llm_factory import _record_tier_choice
        from prax.agent.trace import get_current_trace
        ctx = get_current_trace()
        # Mark local endpoints distinctly so reading a trace makes it obvious
        # which calls were on-prem vs hosted.
        provider_tag = (
            f"{provider}@{settings.vision_base_url}"
            if settings.vision_base_url
            else provider
        )
        _record_tier_choice(
            tier_requested="vision",
            tier_resolved="vision",
            model=settings.vision_model,
            provider=provider_tag,
            span_id=ctx.span_id if ctx else None,
            span_name=ctx.origin if ctx else None,
        )
    except Exception:
        pass  # tracing failure must never block image analysis

    if provider == "openai":
        return _analyze_openai(image_url, prompt)
    if provider == "anthropic":
        return _analyze_anthropic(image_url, prompt)
    if provider in {"google", "google-vertex"}:
        return _analyze_google(image_url, prompt)

    raise ValueError(f"Unsupported vision provider: {provider}")


@tool
def analyze_image(image_url: str, prompt: str = "Describe this image in detail.") -> str:
    """Analyze an image with the configured vision model.

    Use this whenever you need to understand the contents of ANY image you can
    reference: an inbound attachment, an image at an http(s) URL (CDN links —
    Discord/Twilio/etc. — work directly), or a local file you produced (e.g. a
    saved browser/desktop screenshot). You do NOT need a "data URL" or an
    "upload"; pass the URL or path you have and call this tool.

    Args:
        image_url: A direct http(s) URL to the image, OR a local file path
                   (e.g. /tmp/cdp_screenshot_123.jpg) — JPEG, PNG, GIF, WebP.
        prompt: What to analyze — e.g. "Describe this image",
                "What text is in this image?", "Extract the data from this chart",
                "What's wrong with this code screenshot?".
    """
    try:
        return analyze_image_impl(image_url, prompt)
    except Exception as exc:
        logger.exception("Vision analysis failed for %s", image_url[:80])
        return f"Failed to analyze image: {exc}"


def build_vision_tools() -> list:
    """Return vision tools if a vision provider is configured.

    For the ``openai`` provider, a configured ``VISION_BASE_URL`` (a local
    OpenAI-compatible server) is treated as sufficient — the API key check is
    skipped, since local servers typically don't enforce keys.
    """
    if not settings.vision_model:
        return []
    provider = settings.vision_provider.lower()
    if provider == "openai":
        if not (settings.vision_base_url or settings.vision_api_key or settings.openai_key):
            return []
    elif provider == "anthropic" and not settings.anthropic_key:
        return []
    elif provider in {"google", "google-vertex"} and not settings.google_api_key:
        return []
    return [analyze_image]
