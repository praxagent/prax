"""Deutschlandfunk radio — fetch the latest news broadcast audio."""
from langchain_core.tools import tool

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "Fetch the latest Deutschlandfunk radio news broadcast URL"


@tool
def deutschlandfunk_tool(subject: str | None = None) -> str:
    """Return the latest Deutschlandfunk news broadcast audio URL.

    No arguments are required. Returns a direct link to the MP3.
    """
    _ = subject
    from prax.readers.news.deutschlandfunk_radio import deutschlandfunk_process

    try:
        result = deutschlandfunk_process("agent")
        return result or "Unable to fetch Deutschlandfunk broadcast."
    except Exception as e:
        return f"Error fetching Deutschlandfunk: {e}"


def register():
    return [deutschlandfunk_tool]
