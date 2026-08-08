"""Service for downloading and extracting text from PDF files via opendataloader-pdf."""
from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import tempfile

import requests
from opendataloader_pdf import convert

logger = logging.getLogger(__name__)

ARXIV_ABS_RE = re.compile(r"https?://arxiv\.org/abs/([\d.]+v?\d*)")
ARXIV_PDF_RE = re.compile(r"https?://arxiv\.org/pdf/([\d.]+v?\d*)")
PDF_URL_RE = re.compile(r"https?://\S+\.pdf(\?\S*)?$", re.IGNORECASE)


def detect_pdf_url(text: str) -> str | None:
    """Return a direct PDF download URL if text contains an arxiv link or .pdf URL, else None."""
    m = ARXIV_ABS_RE.search(text)
    if m:
        return f"https://arxiv.org/pdf/{m.group(1)}.pdf"
    m = ARXIV_PDF_RE.search(text)
    if m:
        return f"https://arxiv.org/pdf/{m.group(1)}.pdf"
    m = PDF_URL_RE.search(text)
    if m:
        return m.group(0)
    return None


def download_pdf(url: str) -> str:
    """Download a PDF from a URL to a temp file. Caller is responsible for cleanup."""
    resp = requests.get(url, timeout=120, stream=True)
    resp.raise_for_status()

    fd, path = tempfile.mkstemp(suffix=".pdf", prefix="pdf_extract_")
    try:
        with os.fdopen(fd, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
    except Exception:
        os.unlink(path)
        raise

    logger.info("Downloaded PDF to %s (%d bytes)", path, os.path.getsize(path))
    return path


def extract_markdown(pdf_path: str) -> str:
    """Run opendataloader-pdf on a PDF and return the markdown content."""
    output_dir = tempfile.mkdtemp(prefix="pdf_output_")
    try:
        convert(input_path=[pdf_path], output_dir=output_dir, format="markdown")

        md_files = glob.glob(os.path.join(output_dir, "**", "*.md"), recursive=True)
        if not md_files:
            raise FileNotFoundError(f"No markdown output found in {output_dir}")

        with open(md_files[0], encoding="utf-8") as f:
            content = f.read()

        logger.info("Extracted %d chars of markdown from PDF", len(content))
        return _with_coverage_note(pdf_path, content)
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)



# Density below which an extraction is almost certainly missing most of the
# document. A page of prose is 1,500-3,000 characters; a page of dense tables
# or a title page is much less. 120 is deliberately far below any real page so
# the note fires on genuine failures (scans, unparseable layouts) rather than
# on merely sparse documents — a warning that cries wolf gets ignored, and an
# ignored warning is worse than none.
_MIN_CHARS_PER_PAGE = 120


def _page_count(pdf_path: str) -> int | None:
    """Page count, or None if it cannot be determined cheaply."""
    try:
        from pypdf import PdfReader

        return len(PdfReader(pdf_path).pages)
    except Exception:  # noqa: BLE001 - a missing count must not break extraction
        return None


def _with_coverage_note(pdf_path: str, content: str) -> str:
    """Prefix an honest warning when the extraction looks far too thin.

    The converter returns markdown with no indication of how much of the
    document it actually recovered, so a 40-page scan and a 40-page report both
    come back as "some markdown". A caller then summarises whatever arrived
    with full confidence. Stating the shortfall turns a silent gap into a
    disclosed one; it does not fix the extraction, and does not pretend to.
    """
    pages = _page_count(pdf_path)
    if not pages:
        return content
    body = (content or "").strip()
    if not body:
        return (f"[NO TEXT EXTRACTED] This {pages}-page PDF produced no text at "
                f"all — most likely scanned images. It has not been read.")
    if len(body) < _MIN_CHARS_PER_PAGE * pages:
        return (f"[LIKELY INCOMPLETE EXTRACTION] Only {len(body)} characters were "
                f"recovered from {pages} pages (~{len(body) // pages} per page), "
                f"far below a normal page of text. Much of this document is "
                f"probably missing — treat what follows as a partial reading and "
                f"say so if you summarise it.\n\n{body}")
    return content


def process_pdf_url(url: str) -> str:
    """Download a PDF from URL, extract markdown, clean up. Returns markdown text."""
    pdf_path = download_pdf(url)
    try:
        return extract_markdown(pdf_path)
    finally:
        os.unlink(pdf_path)


def process_pdf_url_with_paths(url: str) -> tuple[str, str]:
    """Download a PDF, extract markdown, return (markdown_text, pdf_temp_path).

    Unlike process_pdf_url, this does NOT delete the PDF — caller is responsible for cleanup.
    """
    pdf_path = download_pdf(url)
    try:
        markdown = extract_markdown(pdf_path)
        return markdown, pdf_path
    except Exception:
        os.unlink(pdf_path)
        raise
