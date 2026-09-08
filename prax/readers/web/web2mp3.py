import base64
import logging
import os
import uuid

import requests
from mutagen.id3 import ID3, TIT2
from mutagen.mp3 import MP3
from playwright.sync_api import sync_playwright

from prax.settings import settings

logger = logging.getLogger(__name__)
NGROK_URL = settings.ngrok_url or ""


def convert_web_to_mp3(url, user_id):
    logger.info("Attempting to summarize %s for %s", url, user_id)
    requests.Session()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(60000)
        page.goto(url)
        text = page.text_content("body")  # Extracts all text within the body tag
        browser.close()

    _message = f"""
    I'm providing you the entire webpage to you, and what i want in return is a summary. You are an expert at picking
    out the succinct curcial details of a webpage and summarizing them. You don't say anything about ads, about suggested
    links, instead you focus brilliantly on the actual content of the page. However, don't say "this webpage", instead, summarize it
    as if you were a news presenter. Mention the title if you find it please. Do not plagiarise anything, summarize in your own words,
    never quote directly or paraphrase closely. Also, always state the title and authors at the beginning of your summary if possible.
    Here is the webpage, I look forward to your summary:
    {text}
    """
    try:
        # The summary goes through build_llm (provider / tier / OPENAI_BASE_URL
        # routing); TTS needs the raw SDK, so it comes from openai_client(),
        # the one sanctioned constructor.  Both used to share a raw
        # ``OpenAI(api_key=...)`` client with no base URL, which under keyless
        # Prax sent the proxy token to api.openai.com (see
        # docs/security/secrets-proxy.md).  Prompt unchanged; the legacy
        # ``max_tokens=4096`` field is not carried over — see
        # conversation_memory.summarize_and_replace for why.
        from prax.agent.llm_factory import build_llm, openai_client

        response = build_llm(tier="low").invoke([{'role': 'system', 'content': _message}])
        summary = response.text
        logger.debug(f"convert_web_to_mp3: {summary}")

        response2 = openai_client().audio.speech.create(
          model="tts-1",
          voice="shimmer",
          input=summary
        )

        if not os.path.exists(f"./static/temp/{user_id}/"):
            os.makedirs(f"./static/temp/{user_id}/")

        id = uuid.uuid4()
        mp3_file_path = f"./static/temp/{user_id}/{id}.mp3"
        response2.stream_to_file(mp3_file_path)
        audio = MP3(mp3_file_path, ID3=ID3)
        audio['TIT2'] = TIT2(encoding=3, text=base64.b64encode(url.encode()).decode())
        audio.save()

        return f"{NGROK_URL}/static/temp/{user_id}/{id}.mp3", summary
    except Exception:
        logger.error("Error getting summary", exc_info=True)
        return "Unable to fetch this, sorry.", "I was unable to fetch the summary."

