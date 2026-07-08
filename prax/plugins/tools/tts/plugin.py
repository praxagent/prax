"""Text-to-speech — turn text into a deliverable audio file (multi-provider)."""
import re
import time

from langchain_core.tools import tool

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "Convert text to a speech audio file (OpenAI or ElevenLabs voices)"

# Cost/latency bound: ~4-5 minutes of speech. Longer inputs are rejected with
# guidance instead of silently truncated.
_MAX_CHARS = 4000

_DEFAULT_VOICES = {
    "openai": "nova",
    "elevenlabs": "21m00Tcm4TlvDq8ikWAM",  # "Rachel", ElevenLabs' stock voice
}


def register(caps):
    @tool
    def text_to_speech(text: str, provider: str = "auto", voice: str = "") -> str:
        """Convert text into a speech audio file (mp3) the user can listen to.

        THE tool for "make this an audio file", "read this to me", or
        "narrate this" — do NOT improvise TTS in the sandbox (gtts, pydub,
        ffmpeg/flite): this tool uses a real speech model and saves a
        deliverable file. The mp3 lands in the user's workspace; deliver it
        with workspace_send_file(filename).

        Args:
            text: The text to speak (up to 4000 chars — trim or summarize
                longer input first, or split it into parts).
            provider: 'openai', 'elevenlabs', or 'auto' (default — tries
                openai, falls back to elevenlabs).
            voice: Optional voice override (openai: alloy/echo/fable/onyx/
                nova/shimmer; elevenlabs: a voice ID).
        """
        text = (text or "").strip()
        if not text:
            return "No text provided."
        if len(text) > _MAX_CHARS:
            return (
                f"Text is {len(text)} chars — over the {_MAX_CHARS}-char TTS "
                "bound. Trim or summarize it first, or split it into parts."
            )

        slug = re.sub(r"[^a-z0-9]+", "-", text[:32].lower()).strip("-") or "speech"
        filename = f"tts-{slug}-{int(time.time())}.mp3"

        providers = ["openai", "elevenlabs"] if provider == "auto" else [provider]
        errors = []
        for p in providers:
            chosen_voice = voice or _DEFAULT_VOICES.get(p, "nova")
            try:
                # Synthesize to a scratch path, then persist through the
                # capability gateway's save_file — which writes to the user's
                # ``active/`` dir (git-committed), the ONLY location
                # workspace_send_file can deliver from. Writing straight to
                # workspace_path() lands in the workspace root, which delivery
                # cannot see.
                import os
                import tempfile
                fd, tmp = tempfile.mkstemp(suffix=".mp3")
                os.close(fd)
                try:
                    caps.tts_synthesize(text, tmp, voice=chosen_voice, provider=p)
                    with open(tmp, "rb") as fh:
                        caps.save_file(filename, fh.read())
                finally:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
            except Exception as e:  # missing key, provider outage, bad voice
                errors.append(f"{p}: {e}")
                continue
            return (
                f"Audio saved to your workspace as **{filename}** "
                f"({len(text)} chars, provider={p}, voice={chosen_voice}).\n"
                f"Deliver it with `workspace_send_file('{filename}')`."
            )
        return (
            "TTS failed — " + "; ".join(errors)
            + ". Check OPENAI_KEY / ELEVENLABS_API_KEY configuration."
        )

    return [text_to_speech]
