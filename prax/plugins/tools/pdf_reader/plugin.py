"""PDF reader — download and extract text from PDF URLs."""
from langchain_core.tools import tool

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "Download a PDF from a URL and extract its text content"


@tool
def pdf_summary_tool(url: str) -> str:
    """Download a PDF from a URL (including arXiv links), extract its text, and return the content for summarization.

    Args:
        url: Direct URL to a PDF file.
    """
    from prax.services.pdf_service import process_pdf_url

    try:
        markdown = process_pdf_url(url)
        if len(markdown) > 50_000:
            markdown = markdown[:50_000] + "\n\n[Content truncated due to length]"
        return f"PDF Content:\n\n{markdown}"
    except Exception as e:
        return f"Failed to extract PDF: {e}"


def register():
    return [pdf_summary_tool]
