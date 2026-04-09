"""Encapsulates SMS workflow logic for Twilio webhook."""
from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from collections.abc import Mapping
from urllib.parse import urlparse

from openai import OpenAI

from prax.helpers_dictionaries import num_to_names
from prax.services.conversation_service import conversation_service
from prax.services.pdf_service import detect_pdf_url, process_pdf_url_with_paths
from prax.services.workspace_service import save_binary, save_file
from prax.sms import send_sms

try:
    from pydub import AudioSegment
except ImportError:  # pragma: no cover
    AudioSegment = None

logger = logging.getLogger(__name__)

APOLOGY_MSG = "Sorry, something went wrong processing your message. Please try again."

_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")

# Very permissive URL matcher for auto-capture of shared links in SMS.
_URL_RE = re.compile(r"https?://[^\s<>()\[\]]+")


def _fetch_url_as_markdown(url: str, timeout: int = 20) -> str | None:
    """Fetch a URL through the Jina reader and return clean markdown.

    Thin wrapper around :func:`prax.services.url_reader.try_fetch_markdown`.
    Used by :func:`_maybe_auto_capture_raw` so the library/raw/ entry
    actually contains the article body rather than just the URL and the
    user's message.  Returns ``None`` on any failure so callers can
    degrade gracefully.  Honors ``JINA_API_KEY`` when set for paid quota.
    """
    from prax.services.url_reader import try_fetch_markdown

    return try_fetch_markdown(url, timeout=timeout)


def _maybe_auto_capture_raw(user_id: str, text: str) -> str | None:
    """Detect shared URLs in an inbound message and drop them into
    library/raw/ so they're tracked in the user's knowledge base.

    Returns the slug of the captured raw item on success, or ``None``
    if there was nothing URL-like to capture or the capture failed.

    We capture on any message that contains at least one URL, EXCEPT
    PDFs — those already have their own dedicated flow via
    ``_handle_pdf`` and get saved to the workspace as notes.

    The captured body includes both the user's original message AND
    the fetched page content (via Jina reader), so the raw entry is
    actually usable for "promote to notebook" later.  If the fetch
    fails, the raw entry still gets the user's message so nothing is
    lost.
    """
    urls = _URL_RE.findall(text or "")
    if not urls:
        return None
    # PDFs get handled by the dedicated PDF flow, don't double-capture.
    if any(detect_pdf_url(u) for u in urls):
        return None
    try:
        from prax.services.library_service import raw_capture
        # Title comes from the first URL (domain + path) for a readable slug.
        primary = urls[0]
        parsed = urlparse(primary)
        title = (parsed.netloc + parsed.path).rstrip("/") or primary
        # Trim the title so the slug stays reasonable.
        title = title[:80]

        # Fetch the page content so the raw entry is actually useful.
        fetched = _fetch_url_as_markdown(primary)
        if fetched:
            body = (
                f"> User message:\n> {text.strip()}\n\n"
                f"---\n\n"
                f"## Fetched page content\n\n{fetched}"
            )
        else:
            body = (
                f"> User message:\n> {text.strip()}\n\n"
                f"---\n\n"
                f"*[Could not fetch page content automatically — promote "
                f"to a notebook and use note_from_url for a full pull.]*"
            )

        result = raw_capture(
            user_id,
            title=title,
            content=body,
            source_url=primary,
        )
        if "error" in result:
            return None
        return result["raw"]["slug"]
    except Exception:
        logger.exception("Auto-capture of raw URL failed")
        return None


def _derive_filename(url: str) -> str:
    """Extract a human-readable base filename from a PDF URL."""
    m = _ARXIV_ID_RE.search(url)
    if m:
        return m.group(1) + (m.group(2) or "")
    path = urlparse(url).path
    base = os.path.splitext(os.path.basename(path))[0]
    return base or "document"


class SmsAccessError(Exception):
    pass


class SmsService:
    def __init__(self, database_name: str, openai_key: str, base_model: str) -> None:
        self.database_name = database_name
        self.client = OpenAI(api_key=openai_key) if openai_key else None
        self.base_model = base_model

    def _ensure_authorized(self, from_number: str) -> None:
        if from_number not in num_to_names:
            raise SmsAccessError()

    def _reply_via_agent(self, from_number: str, text: str) -> None:
        """Call conversation_service and SMS the result. Meant to run in a thread."""
        try:
            from prax.services.identity_service import resolve_user
            user = resolve_user("sms", from_number)

            # Mirror incoming SMS to TeamWork's #sms channel.
            from prax.services.teamwork_hooks import forward_to_channel
            forward_to_channel("sms", user.display_name, text)

            # Auto-capture bare URLs into library/raw/.  The agent still
            # sees the original message and can reason about it, but now
            # it knows the content is safely saved in raw/ and can offer
            # to promote it to a notebook.
            captured = _maybe_auto_capture_raw(user.id, text)
            prompt = text
            if captured:
                prompt = (
                    f"{text}\n\n"
                    f"[SYSTEM: captured to library/raw/ as `{captured}` — "
                    f"offer to promote it to a notebook if relevant.]"
                )

            response = conversation_service.reply(user.id, prompt)
            send_sms(response, from_number)

            # Mirror the agent response to #sms.
            forward_to_channel("sms", "Prax", response, agent_name="Prax")
        except Exception:
            logger.exception("Agent reply failed for %s", from_number)
            send_sms(APOLOGY_MSG, from_number)

    def _handle_pdf(self, from_number: str, pdf_url: str, original_text: str) -> None:
        """Download PDF, save to workspace, send through agent for summarization. Runs in a thread."""
        try:
            from prax.services.identity_service import resolve_user
            user = resolve_user("sms", from_number)

            markdown, pdf_path = process_pdf_url_with_paths(pdf_url)

            # Derive filenames and save to workspace
            filename_base = _derive_filename(pdf_url)
            md_filename = f"{filename_base}.md"
            pdf_filename = f"{filename_base}.pdf"

            workspace_markdown = (
                f"---\nsource: {pdf_url}\noriginal_pdf: archive/{pdf_filename}\n---\n\n"
                + markdown
            )
            save_file(user.id, md_filename, workspace_markdown)
            save_binary(user.id, pdf_filename, pdf_path)
            os.unlink(pdf_path)

            # Truncate for inline prompt (workspace has the full version)
            display_markdown = markdown
            if len(display_markdown) > 50_000:
                display_markdown = display_markdown[:50_000] + "\n\n[Content truncated — full version saved to workspace]"

            prompt = (
                f"The user sent a PDF from: {pdf_url}\n"
                f"I've saved the extracted markdown to your workspace as {md_filename}. "
                f"The original PDF is archived as {pdf_filename}.\n\n"
                f"Here is the extracted content:\n\n{display_markdown}\n\n"
                "Please provide a clear, concise summary of this document."
            )
            stripped = original_text.strip()
            if stripped and stripped != pdf_url.strip():
                prompt += f"\n\nThe user also said: {stripped}"

            response = conversation_service.reply(user.id, prompt)
            send_sms(response, from_number)
        except Exception:
            logger.exception("PDF processing failed for %s", from_number)
            send_sms(APOLOGY_MSG, from_number)

    def _handle_merge(self, from_number: str, ngrok_url: str):
        if AudioSegment is None:
            send_sms("Audio merging is currently unavailable on this server.", from_number)
            return True
        files_to_delete = []
        mp3_dir = f"./static/temp/{from_number}"
        os.makedirs(mp3_dir, exist_ok=True)
        merge_output = f"{mp3_dir}/merged_{uuid.uuid4()}.mp3"
        merged_audio = AudioSegment.empty()
        silence = AudioSegment.silent(duration=2000)
        beep_wait = AudioSegment.from_mp3("./static/mp3/beep_wait.mp3")

        for file in os.listdir(mp3_dir):
            if file.endswith('.mp3'):
                current_audio = AudioSegment.from_mp3(os.path.join(mp3_dir, file))
                merged_audio += current_audio + silence + beep_wait + silence
                files_to_delete.append(os.path.join(mp3_dir, file))
        merged_audio.export(merge_output, format='mp3')
        send_sms(f"{ngrok_url}/{merge_output}", from_number)
        for file_path in files_to_delete:
            os.remove(file_path)
        return True

    def process(self, payload: Mapping[str, str], ngrok_url: str) -> tuple[str, int]:
        from_number = payload.get('From', '')
        text_input = payload.get('Body', '').strip()
        num_media = payload.get('NumMedia', '0')

        self._ensure_authorized(from_number)

        # --- Media attachments ---
        if num_media != '0':
            media_type = payload.get('MediaContentType0', '')
            media_url = payload.get('MediaUrl0')

            if media_type == 'application/pdf' and media_url:
                send_sms("Got your PDF! Processing it now, this may take a moment...", from_number)
                threading.Thread(
                    target=self._handle_pdf,
                    args=(from_number, media_url, text_input),
                ).start()
                return '', 200

            # Image attachment — route through the agent so it can use
            # the analyze_image tool with the configured vision model.
            if media_url:
                from prax.services.identity_service import resolve_user
                user = resolve_user("sms", from_number)
                user_text = text_input or "I'm sending you an image."
                image_msg = f"{user_text}\n[Image attachment: {media_type}, URL: {media_url}]"
                response = conversation_service.reply(user.id, image_msg)
                send_sms(response, from_number)
                return '', 200

        # --- Text messages ---
        if text_input:
            if text_input.lower() in {'help', 'menu'}:
                send_sms(
                    "How we can interact:\n"
                    "1. Text me a question, and I will answer it.\n"
                    "2. Text me an image, and I will describe it.\n"
                    "3. Ask me to search the web for something.\n"
                    "4. Ask for the latest NPR podcast.\n"
                    "5. Send a webpage link for a summary.\n"
                    "6. Send a PDF file, PDF link, or arxiv link for a summary.\n"
                    "7. Text 'merge' to combine your audio summaries.",
                    from_number,
                )
                return '', 200

            if text_input.lower() == 'merge':
                return ('', 200) if self._handle_merge(from_number, ngrok_url) else ('', 500)

            # Check for PDF URL or arxiv link
            pdf_url = detect_pdf_url(text_input)
            if pdf_url:
                send_sms("Found a PDF link! Processing it now, this may take a moment...", from_number)
                threading.Thread(
                    target=self._handle_pdf,
                    args=(from_number, pdf_url, text_input),
                ).start()
                return '', 200

            # All other text goes through the agent
            threading.Thread(
                target=self._reply_via_agent,
                args=(from_number, text_input),
            ).start()
            return '', 200

        return '', 200


from prax.settings import settings as _settings

sms_service = SmsService(
    database_name=_settings.database_name,
    openai_key=_settings.openai_key or '',
    base_model=_settings.base_model,
)
