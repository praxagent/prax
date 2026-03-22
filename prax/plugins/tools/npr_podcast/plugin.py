"""NPR News Now podcast — fetch the latest top-of-the-hour episode URL."""
from langchain_core.tools import tool

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "Fetch the latest NPR News Now podcast episode URL"


@tool
def npr_podcast_tool(subject: str | None = None) -> str:
    """Return the latest NPR top-of-the-hour audio URL.

    No arguments are required. Returns a direct link to the MP3.
    """
    _ = subject
    from prax.readers.news.npr_top_hour import get_latest_npr_podcast

    return get_latest_npr_podcast() or "Unable to fetch NPR update."


def register():
    return [npr_podcast_tool]
