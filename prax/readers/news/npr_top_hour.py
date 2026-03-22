import logging
import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import ffmpeg
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

def get_latest_npr_podcast():
    url = "https://www.npr.org/podcasts/500005/npr-news-now"
    response = requests.get(url)

    if response.status_code == 200:
        soup = BeautifulSoup(response.text, "html.parser")
        latest_episode = soup.find("article", class_="item podcast-episode")
        podcast_link = latest_episode.find("a", class_="audio-module-listen")
        if podcast_link:
            full_link = podcast_link["href"]
            split_link = urlsplit(full_link)
            clean_link = urlunsplit((split_link.scheme, split_link.netloc, split_link.path, "", ""))
            return clean_link
        else:
            logger.warning("Could not find the mp3 download link.")
            return None
    else:
        logger.warning("Failed to fetch the webpage.")
        return None

def download_file(call_sid, url):
    logger.info("Downloading file")
    response = requests.get(url)
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


def npr_process(call_sid):
    url = get_latest_npr_podcast()
    if url:
        raw_file = download_file(call_sid, url)
        if raw_file:
            normalized_file = normalize_volume(raw_file)
            os.remove(raw_file)
            logger.info("Downloaded and normalized file: %s", normalized_file)
            return normalized_file
        else:
            logger.warning("Could not download the file.")
    else:
        logger.warning("Could not fetch the URL for the MP3 file.")
    return None
