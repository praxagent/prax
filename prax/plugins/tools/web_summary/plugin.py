"""Webpage summariser — fetch a URL, summarise with GPT, and convert to audio."""
from langchain_core.tools import tool

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "Summarise a webpage and return text summary with audio link"


@tool
def web_summary_tool(url: str, user_id: str | None = None) -> str:
    """Summarize a webpage and provide an audio link.

    Args:
        url: The webpage URL to summarize.
        user_id: Optional user identifier for file storage.
    """
    from prax.readers.web.web2mp3 import convert_web_to_mp3

    link, summary = convert_web_to_mp3(url, user_id or "agent")
    return f"Summary: {summary}\nAudio Link: {link}"


def register():
    return [web_summary_tool]
