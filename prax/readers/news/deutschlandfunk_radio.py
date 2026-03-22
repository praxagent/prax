import logging
import os
import uuid

import ffmpeg
import requests
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

url = "https://www.deutschlandfunk.de/audiothek?drsearch:searchText=&drsearch:from-date=&drsearch:to-date=&drsearch:only-audio=1&drsearch:series=PAPAYA_BROADCAST_169&drsearch:stations=4f8db02a-35ae-4b78-9cd0-86b177726ec0&drsearch:stations-all=4f8db02a-35ae-4b78-9cd0-86b177726ec0&drsearch:stations-all=56f11dfd-a3a8-429b-acaf-06bf595f2dd8&drsearch:stations-all=64df3047-eea5-411a-877c-c415f344a8e7"


def run(playwright):
    browser = playwright.chromium.launch()
    context = browser.new_context()

    page = context.new_page()
    page.goto(url, wait_until="networkidle")

    page.wait_for_selector('article.b-two-column-teaser.b-teaser-audiotech')

    noscript_content = page.eval_on_selector(
        "article.b-two-column-teaser.b-teaser-audiotech noscript",
        "element => element.textContent"
    )

    if noscript_content:
        audio_url_start = noscript_content.find('href="') + len('href="')
        audio_url_end = noscript_content.find('"', audio_url_start)
        audio_url = noscript_content[audio_url_start:audio_url_end].removesuffix(' class=')[:-1]
        logger.info("Audio URL: %s", audio_url)

    context.close()
    browser.close()
    return audio_url

def deutschlandfunk_process(call_sid):
    audio_url = None
    with sync_playwright() as playwright:
        audio_url = run(playwright)
    return download_file(call_sid, audio_url)

def download_file(call_sid, url):
    logger.info("Downloading file")
    response = requests.get(url)
    logger.info(url)
    if response.status_code == 200:
        unique_id = uuid.uuid4()
        filename = f"./static/temp/{call_sid.strip()}_{unique_id}.mp3"
        logger.info("Saving file to %s", filename)
        with open(filename, "wb") as file:
            file.write(response.content)
        return filename
    else:
        logger.warning("Failed to download the file.")
        return None

def normalize_volume(input_file, volume_adjustment="-12.0dB"):
    output_file = f"{input_file}".replace(".mp3", "_normalized.mp3")

    try:
        (
            ffmpeg
            .input(input_file)
            .filter("volume", volume_adjustment)
            .output(output_file)
            .run()
        )

    except ffmpeg.Error as e:
        logger.error("FFmpeg error: %s", e)
        return None

    return output_file

def dlf_process(call_sid):
    download = deutschlandfunk_process(call_sid)
    normalized_file = normalize_volume(download)
    os.remove(download)
    return normalized_file
