"""Image generation — turn a text prompt into a deliverable image file."""
import re
import time

from langchain_core.tools import tool

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "Generate an image from a text prompt (OpenAI Images API)"

_VALID_SIZES = {"1024x1024", "1536x1024", "1024x1536", "auto"}
_VALID_QUALITIES = {"low", "medium", "high", "auto"}


def register(caps):
    @tool
    def generate_image(prompt: str, size: str = "1024x1024", quality: str = "auto") -> str:
        """Generate an image from a text description and save it to the workspace.

        THE tool for "make me an image", "draw / generate a picture of…",
        "create a logo/illustration" — do NOT improvise image generation in
        the sandbox. Uses a real image model and saves a deliverable PNG to
        the user's workspace; deliver it with workspace_send_file(filename).

        Args:
            prompt: Detailed description of the image — be specific about
                subject, style, composition, and lighting.
            size: "1024x1024" (square), "1536x1024" (landscape),
                "1024x1536" (portrait), or "auto".
            quality: "low", "medium", "high", or "auto".
        """
        prompt = (prompt or "").strip()
        if not prompt:
            return "No prompt provided — describe the image you want."
        if size not in _VALID_SIZES:
            size = "1024x1024"
        if quality not in _VALID_QUALITIES:
            quality = "auto"

        api_key = caps.get_approved_secret("OPENAI_KEY")
        if not api_key:
            return (
                "Image generation needs OPENAI_KEY — it isn't configured. "
                "Set it in your Prax environment."
            )

        from prax.settings import settings
        model = settings.image_model or "gpt-image-1"
        # gpt-image-* and dall-e-* are generation models; anything else (e.g. a
        # misconfigured chat model) falls back to a known-good image model.
        if "image" not in model and "dall" not in model:
            model = "dall-e-3"

        slug = re.sub(r"[^a-z0-9]+", "-", prompt[:32].lower()).strip("-") or "image"
        filename = f"image-{slug}-{int(time.time())}.png"

        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            kwargs = {"model": model, "prompt": prompt, "n": 1, "size": size}
            if quality != "auto":
                kwargs["quality"] = quality
            resp = client.images.generate(**kwargs)
            item = resp.data[0]
            if getattr(item, "b64_json", None):
                import base64
                image_bytes = base64.b64decode(item.b64_json)
            elif getattr(item, "url", None):
                import requests
                r = requests.get(item.url, timeout=60)
                r.raise_for_status()
                image_bytes = r.content
            else:
                return "Image generation returned no image data."
            caps.save_file(filename, image_bytes)
        except Exception as e:
            return (
                f"Image generation failed ({model}): {e}. "
                "Check OPENAI_KEY and that the image model is available."
            )

        return (
            f"Image saved to your workspace as **{filename}** "
            f"(model={model}, size={size}).\n"
            f"Deliver it with `workspace_send_file('{filename}')`."
        )

    return [generate_image]
