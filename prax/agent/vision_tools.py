"""Vision tools — provider-agnostic image understanding.

Gives the agent the ability to analyze images via a configurable vision
model.  Supports OpenAI, Anthropic, and Google out of the box.  The
provider and model are set via VISION_PROVIDER and VISION_MODEL env vars.
"""
from __future__ import annotations

import base64
import logging

import requests
from langchain_core.tools import tool

from prax.settings import settings

logger = logging.getLogger(__name__)

# Maximum image size to download for base64 encoding (10 MB).
_MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _fetch_image_base64(url: str) -> tuple[str, str]:
    """Download an image and return (base64_data, media_type)."""
    resp = requests.get(url, timeout=30, stream=True)
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


def _analyze_openai(image_url: str, prompt: str) -> str:
    """Analyze an image using the OpenAI API."""
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_key)
    response = client.chat.completions.create(
        model=settings.vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        max_tokens=2000,
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

    if provider == "openai":
        return _analyze_openai(image_url, prompt)
    if provider == "anthropic":
        return _analyze_anthropic(image_url, prompt)
    if provider in {"google", "google-vertex"}:
        return _analyze_google(image_url, prompt)

    raise ValueError(f"Unsupported vision provider: {provider}")


@tool
def analyze_image(image_url: str, prompt: str = "Describe this image in detail.") -> str:
    """Analyze an image from a URL using the configured vision model.

    Use this tool when the user sends an image (via SMS, Discord, or web)
    or when you need to understand the contents of an image at a URL.

    Args:
        image_url: Direct URL to the image (JPEG, PNG, GIF, WebP).
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
    """Return vision tools if a vision provider is configured."""
    if not settings.vision_model:
        return []
    # Check that the required API key exists for the configured provider.
    provider = settings.vision_provider.lower()
    if provider == "openai" and not settings.openai_key:
        return []
    if provider == "anthropic" and not settings.anthropic_key:
        return []
    if provider in {"google", "google-vertex"} and not settings.google_api_key:
        return []
    return [analyze_image]
