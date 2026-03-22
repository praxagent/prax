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

from prax.conversation_memory import add_dict_to_list
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
            response = conversation_service.reply(from_number, text)
            send_sms(response, from_number)
        except Exception:
            logger.exception("Agent reply failed for %s", from_number)
            send_sms(APOLOGY_MSG, from_number)

    def _handle_pdf(self, from_number: str, pdf_url: str, original_text: str) -> None:
        """Download PDF, save to workspace, send through agent for summarization. Runs in a thread."""
        try:
            markdown, pdf_path = process_pdf_url_with_paths(pdf_url)

            # Derive filenames and save to workspace
            filename_base = _derive_filename(pdf_url)
            md_filename = f"{filename_base}.md"
            pdf_filename = f"{filename_base}.pdf"

            workspace_markdown = (
                f"---\nsource: {pdf_url}\noriginal_pdf: archive/{pdf_filename}\n---\n\n"
                + markdown
            )
            save_file(from_number, md_filename, workspace_markdown)
            save_binary(from_number, pdf_filename, pdf_path)
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

            response = conversation_service.reply(from_number, prompt)
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

            # Image attachment
            phone_int = int(from_number[1:])
            if self.client and media_url:
                add_dict_to_list(self.database_name, phone_int, {'role': 'user', 'content': "I'm sending you an image to describe."})
                response = self.client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Describe this image"},
                                {"type": "image_url", "image_url": {"url": media_url}},
                            ],
                        }
                    ],
                    max_tokens=1000,
                )
                add_dict_to_list(
                    self.database_name,
                    phone_int,
                    {'role': 'assistant', 'content': f"Vision result: {response.choices[0].message.content}"},
                )
                send_sms(response.choices[0].message.content, from_number)
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
