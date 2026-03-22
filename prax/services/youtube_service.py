"""Service for downloading YouTube audio and transcribing via OpenAI Whisper."""
from __future__ import annotations

import logging
import os
import re
import tempfile

from openai import OpenAI

from prax.settings import settings

logger = logging.getLogger(__name__)

YOUTUBE_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([\w-]{11})"
)

# Whisper API accepts files up to 25 MB.
MAX_FILE_SIZE_MB = 25


def _openai_client() -> OpenAI:
    if not settings.openai_key:
        raise RuntimeError("OPENAI_KEY is required for YouTube transcription")
    return OpenAI(api_key=settings.openai_key)


def is_youtube_url(url: str) -> bool:
    """Return True if the string looks like a YouTube video URL."""
    return YOUTUBE_URL_RE.search(url) is not None


def download_audio(url: str) -> tuple[str, dict]:
    """Download audio from a YouTube URL as mp3.

    Returns (tmp_file_path, metadata_dict).
    Caller is responsible for deleting the temp file.
    """
    import yt_dlp

    fd, tmp_path = tempfile.mkstemp(suffix=".mp3", prefix="yt_audio_")
    os.close(fd)

    ydl_opts = {
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "outtmpl": tmp_path.replace(".mp3", ".%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    # yt-dlp may produce a file with a different extension before post-processing;
    # the final mp3 ends up at the path with .mp3.
    actual_path = tmp_path
    if not os.path.exists(actual_path):
        # Try the path before post-processor renamed it.
        for ext in ("webm", "m4a", "opus", "ogg"):
            candidate = tmp_path.replace(".mp3", f".{ext}")
            if os.path.exists(candidate):
                actual_path = candidate
                break

    file_size_mb = os.path.getsize(actual_path) / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        os.unlink(actual_path)
        raise ValueError(
            f"Audio file is {file_size_mb:.1f} MB, exceeding the {MAX_FILE_SIZE_MB} MB Whisper API limit. "
            "Try a shorter video."
        )

    metadata = {
        "title": info.get("title", "Unknown"),
        "channel": info.get("uploader") or info.get("channel", "Unknown"),
        "duration_seconds": info.get("duration"),
        "url": info.get("webpage_url", url),
    }

    logger.info(
        "Downloaded YouTube audio: %s (%.1f MB, %ss)",
        metadata["title"],
        file_size_mb,
        metadata.get("duration_seconds", "?"),
    )
    return actual_path, metadata


def transcribe_audio(audio_path: str) -> str:
    """Transcribe an audio file using OpenAI Whisper API."""
    client = _openai_client()
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(model="whisper-1", file=f)
    return result.text


def process_youtube_url(url: str) -> dict:
    """Full pipeline: download YouTube audio → transcribe → return structured result.

    Returns dict with keys: title, channel, duration_seconds, url, transcript.
    """
    audio_path, metadata = download_audio(url)
    try:
        transcript = transcribe_audio(audio_path)
    finally:
        os.unlink(audio_path)

    metadata["transcript"] = transcript
    logger.info(
        "Transcribed YouTube video '%s' — %d chars",
        metadata["title"],
        len(transcript),
    )
    return metadata
