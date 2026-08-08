"""A partial PDF read must announce itself.

Prax had the TOTAL-failure case right: a PDF yielding no text at all said so
and named OCR as the missing capability. The PARTIAL case was silent — pages
that produced nothing were skipped with `continue`, so a 40-page document where
35 pages are scans came back as five pages of text with markers reading
"page 3 of 40 … page 17 of 40" and no statement that 35 pages were absent.

That is the failure class of the `honesty_absent_source_body` capability case
occurring in Prax's own code, and worse than the version that case tests: there
the gap was disclosed and stepped over, here it was never disclosed at all. A
caller summarising the output would confidently describe 12% of a document.
"""

import pytest

from prax.agent.library_tools import _extract_pdf_text, _page_ranges
from prax.services.pdf_service import _with_coverage_note


class _Page:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _Reader:
    is_encrypted = False

    def __init__(self, pages):
        self.pages = [_Page(t) for t in pages]


@pytest.fixture()
def reader(monkeypatch):
    """Patch pypdf.PdfReader wherever _extract_pdf_text imports it."""
    def _install(pages):
        import pypdf
        monkeypatch.setattr(pypdf, "PdfReader", lambda _p: _Reader(pages))
    return _install


class TestPageRanges:
    def test_compresses_runs(self):
        assert _page_ranges([1, 2, 3, 7, 9, 10]) == "1-3, 7, 9-10"

    def test_single_pages(self):
        assert _page_ranges([4]) == "4"
        assert _page_ranges([2, 5]) == "2, 5"

    def test_empty(self):
        assert _page_ranges([]) == ""


class TestPartialExtractionIsAnnounced:
    def test_missing_pages_are_named(self, reader):
        """The load-bearing case: 5 of 8 pages yield nothing."""
        reader(["alpha", "", "", "beta", "", "", "", "gamma"])
        out = _extract_pdf_text("x.pdf", max_chars=100_000)
        assert out.startswith("[INCOMPLETE EXTRACTION]")
        assert "5 of 8 pages" in out
        assert "2-3, 5-7" in out
        assert "alpha" in out and "gamma" in out

    def test_percentage_is_reported(self, reader):
        reader(["a"] + [""] * 3)
        out = _extract_pdf_text("x.pdf", max_chars=100_000)
        assert "75%" in out

    def test_caller_is_told_to_disclose(self, reader):
        """The banner exists to change what the agent SAYS, not just to log."""
        reader(["a", ""])
        out = _extract_pdf_text("x.pdf", max_chars=100_000)
        assert "partial reading" in out.lower()
        assert "say so if you summarise" in out.lower()

    def test_banner_leads_the_output(self, reader):
        """A caveat after 40 pages of content is a caveat nobody reads."""
        reader(["x" * 4000, "", "y" * 4000])
        out = _extract_pdf_text("x.pdf", max_chars=100_000)
        assert out.index("INCOMPLETE") < out.index("--- page")


class TestCompleteExtractionStaysClean:
    def test_no_banner_when_every_page_has_text(self, reader):
        """A warning that fires on healthy input gets ignored, and an ignored
        warning is worse than none."""
        reader(["alpha", "beta", "gamma"])
        out = _extract_pdf_text("x.pdf", max_chars=100_000)
        assert "INCOMPLETE" not in out
        assert out.startswith("--- page 1 of 3")

    def test_total_failure_keeps_its_existing_message(self, reader):
        reader(["", "", ""])
        out = _extract_pdf_text("x.pdf", max_chars=100_000)
        assert "No extractable text" in out
        assert "OCR" in out


class TestTruncationIsAlsoCoverage:
    def test_size_limit_names_the_pages_not_included(self, reader):
        reader(["z" * 500] * 10)
        out = _extract_pdf_text("x.pdf", max_chars=1200)
        assert "INCOMPLETE EXTRACTION" in out
        assert "size limit" in out
        assert "of 10" in out


class TestMarkdownPathCoverage:
    def _pages(self, monkeypatch, n):
        monkeypatch.setattr("prax.services.pdf_service._page_count", lambda _p: n)

    def test_thin_extraction_is_flagged(self, monkeypatch):
        self._pages(monkeypatch, 40)
        out = _with_coverage_note("x.pdf", "a short paragraph of text")
        assert "LIKELY INCOMPLETE EXTRACTION" in out
        assert "40 pages" in out

    def test_empty_extraction_is_flagged(self, monkeypatch):
        self._pages(monkeypatch, 12)
        out = _with_coverage_note("x.pdf", "   ")
        assert "NO TEXT EXTRACTED" in out
        assert "has not been read" in out

    def test_normal_density_is_untouched(self, monkeypatch):
        self._pages(monkeypatch, 3)
        content = "word " * 2000
        assert _with_coverage_note("x.pdf", content) == content

    def test_unknown_page_count_does_not_guess(self, monkeypatch):
        """No page count means no basis for a claim about coverage — silence is
        correct, a warning would be invented."""
        self._pages(monkeypatch, None)
        assert _with_coverage_note("x.pdf", "tiny") == "tiny"
