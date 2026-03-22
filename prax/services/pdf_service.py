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
        return content
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


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
