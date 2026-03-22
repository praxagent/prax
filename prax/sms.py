"""SMS sending utilities."""
from __future__ import annotations

import logging
import time

from prax.clients import get_twilio_client
from prax.settings import settings

logger = logging.getLogger(__name__)


def split_text_into_chunks(text: str) -> list[str]:
    """Split text into chunks that Twilio can handle, ensuring words and newlines are preserved."""
    max_length = 1600

    lines = text.replace("\n\n", "\n").splitlines(keepends=True)
    chunks: list[str] = []
    current_chunk = ""

    for line in lines:
        words = line.split(" ")
        for word in words:
            if len(current_chunk) + len(word) + 1 > max_length:
                chunks.append(current_chunk.strip())
                current_chunk = word
            else:
                current_chunk += " " + word

        if len(current_chunk) + 1 <= max_length:
            current_chunk += "\n"

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks


def send_sms(message: str, to_number: str) -> None:
    """Send an SMS in chunks if the message is too large."""
    logger.info("Prepping to send")
    chunks = split_text_into_chunks(message)

    for chunk in chunks:
        logger.info("Sending chunk: %s", chunk)
        get_twilio_client().messages.create(
            body=chunk,
            from_=settings.root_phone_number,
            to=to_number,
        )
        time.sleep(2)
