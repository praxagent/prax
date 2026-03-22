import os
import tempfile

import pytest

from prax.services.pdf_service import (
    detect_pdf_url,
    download_pdf,
    extract_markdown,
    process_pdf_url,
    process_pdf_url_with_paths,
)


class TestDetectPdfUrl:
    def test_arxiv_abs(self):
        assert detect_pdf_url("check out https://arxiv.org/abs/2301.12345") == "https://arxiv.org/pdf/2301.12345.pdf"

    def test_arxiv_abs_with_version(self):
        assert detect_pdf_url("https://arxiv.org/abs/2301.12345v2") == "https://arxiv.org/pdf/2301.12345v2.pdf"

    def test_arxiv_pdf(self):
        assert detect_pdf_url("https://arxiv.org/pdf/2301.12345") == "https://arxiv.org/pdf/2301.12345.pdf"

    def test_direct_pdf_url(self):
        assert detect_pdf_url("https://example.com/paper.pdf") == "https://example.com/paper.pdf"

    def test_pdf_url_with_query(self):
        assert detect_pdf_url("https://example.com/paper.pdf?dl=1") == "https://example.com/paper.pdf?dl=1"

    def test_pdf_url_case_insensitive(self):
        assert detect_pdf_url("https://example.com/PAPER.PDF") == "https://example.com/PAPER.PDF"

    def test_normal_text_returns_none(self):
        assert detect_pdf_url("just a normal message") is None

    def test_non_pdf_url_returns_none(self):
        assert detect_pdf_url("https://example.com/page.html") is None


class TestDownloadPdf:
    def test_downloads_to_temp_file(self, monkeypatch):
        class FakeResp:
            def raise_for_status(self):
                pass
            def iter_content(self, chunk_size=None):
                return [b"%PDF-fake-content"]
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        monkeypatch.setattr("prax.services.pdf_service.requests.get", lambda *a, **kw: FakeResp())

        path = download_pdf("https://example.com/test.pdf")
        try:
            assert os.path.exists(path)
            assert path.endswith(".pdf")
            with open(path, "rb") as f:
                assert f.read() == b"%PDF-fake-content"
        finally:
            os.unlink(path)

    def test_cleans_up_on_error(self, monkeypatch):
        class FakeResp:
            def raise_for_status(self):
                pass
            def iter_content(self, chunk_size=None):
                raise OSError("network error")

        monkeypatch.setattr("prax.services.pdf_service.requests.get", lambda *a, **kw: FakeResp())

        with pytest.raises(OSError):
            download_pdf("https://example.com/test.pdf")


class TestExtractMarkdown:
    def test_reads_markdown_output(self, monkeypatch):
        def fake_convert(input_path, output_dir, format):
            md_path = os.path.join(output_dir, "output.md")
            with open(md_path, "w") as f:
                f.write("# Hello\n\nExtracted content.")

        monkeypatch.setattr("prax.services.pdf_service.convert", fake_convert)

        fd, pdf_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            result = extract_markdown(pdf_path)
            assert "# Hello" in result
            assert "Extracted content." in result
        finally:
            os.unlink(pdf_path)

    def test_raises_on_no_output(self, monkeypatch):
        def fake_convert(input_path, output_dir, format):
            pass  # produces no files

        monkeypatch.setattr("prax.services.pdf_service.convert", fake_convert)

        fd, pdf_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            with pytest.raises(FileNotFoundError):
                extract_markdown(pdf_path)
        finally:
            os.unlink(pdf_path)

    def test_cleans_up_output_dir(self, monkeypatch):
        created_dirs = []

        original_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(**kwargs):
            d = original_mkdtemp(**kwargs)
            created_dirs.append(d)
            return d

        monkeypatch.setattr("prax.services.pdf_service.tempfile.mkdtemp", tracking_mkdtemp)

        def fake_convert(input_path, output_dir, format):
            with open(os.path.join(output_dir, "out.md"), "w") as f:
                f.write("content")

        monkeypatch.setattr("prax.services.pdf_service.convert", fake_convert)

        fd, pdf_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            extract_markdown(pdf_path)
            assert len(created_dirs) == 1
            assert not os.path.exists(created_dirs[0])
        finally:
            os.unlink(pdf_path)


class TestProcessPdfUrl:
    def test_end_to_end(self, monkeypatch):
        monkeypatch.setattr(
            "prax.services.pdf_service.download_pdf",
            lambda url: _make_temp_pdf(),
        )
        monkeypatch.setattr(
            "prax.services.pdf_service.extract_markdown",
            lambda path: "# Summary\n\nGreat paper.",
        )

        result = process_pdf_url("https://arxiv.org/pdf/2301.12345.pdf")
        assert "Great paper." in result

    def test_cleans_up_pdf_on_success(self, monkeypatch):
        pdf_path = _make_temp_pdf()
        monkeypatch.setattr("prax.services.pdf_service.download_pdf", lambda url: pdf_path)
        monkeypatch.setattr("prax.services.pdf_service.extract_markdown", lambda path: "ok")

        process_pdf_url("https://example.com/test.pdf")
        assert not os.path.exists(pdf_path)

    def test_cleans_up_pdf_on_error(self, monkeypatch):
        pdf_path = _make_temp_pdf()
        monkeypatch.setattr("prax.services.pdf_service.download_pdf", lambda url: pdf_path)
        monkeypatch.setattr(
            "prax.services.pdf_service.extract_markdown",
            lambda path: (_ for _ in ()).throw(RuntimeError("extraction failed")),
        )

        with pytest.raises(RuntimeError):
            process_pdf_url("https://example.com/test.pdf")
        assert not os.path.exists(pdf_path)


class TestProcessPdfUrlWithPaths:
    def test_returns_markdown_and_path(self, monkeypatch):
        monkeypatch.setattr("prax.services.pdf_service.download_pdf", lambda url: _make_temp_pdf())
        monkeypatch.setattr("prax.services.pdf_service.extract_markdown", lambda path: "# Paper")

        markdown, pdf_path = process_pdf_url_with_paths("https://example.com/test.pdf")
        assert markdown == "# Paper"
        assert os.path.exists(pdf_path)  # NOT cleaned up — caller's responsibility
        os.unlink(pdf_path)

    def test_cleans_up_on_extract_error(self, monkeypatch):
        pdf_path = _make_temp_pdf()
        monkeypatch.setattr("prax.services.pdf_service.download_pdf", lambda url: pdf_path)
        monkeypatch.setattr(
            "prax.services.pdf_service.extract_markdown",
            lambda path: (_ for _ in ()).throw(RuntimeError("fail")),
        )

        with pytest.raises(RuntimeError):
            process_pdf_url_with_paths("https://example.com/test.pdf")
        assert not os.path.exists(pdf_path)


def _make_temp_pdf() -> str:
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.write(fd, b"%PDF-fake")
    os.close(fd)
    return path
