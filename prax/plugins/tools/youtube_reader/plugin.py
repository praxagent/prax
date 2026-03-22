"""YouTube transcriber — download audio and transcribe via Whisper."""
from langchain_core.tools import tool

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "Download YouTube video audio and transcribe it to text"


@tool
def youtube_transcribe(url: str) -> str:
    """Download a YouTube video's audio and transcribe it to text using OpenAI Whisper.

    Accepts any YouTube URL (youtube.com/watch, youtu.be, youtube.com/shorts).
    Returns the video metadata and full transcript.

    Args:
        url: A YouTube video URL.
    """
    from prax.services.youtube_service import process_youtube_url

    try:
        result = process_youtube_url(url)
        duration = result.get("duration_seconds")
        duration_str = f" ({duration // 60}m {duration % 60}s)" if duration else ""
        return (
            f"**{result['title']}**\n"
            f"Channel: {result['channel']}{duration_str}\n"
            f"Source: {result['url']}\n\n"
            f"Transcript:\n{result['transcript']}"
        )
    except Exception as e:
        return f"Failed to transcribe YouTube video: {e}"


def register():
    return [youtube_transcribe]
