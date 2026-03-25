"""Tests for the arXiv plugin helpers (no network calls)."""
import textwrap

from prax.plugins.tools.arxiv_reader.plugin import _format_paper, _latex_to_readable

SAMPLE_PAPER = {
    "id": "2305.09702",
    "title": "Attention Is All You Need (Redux)",
    "authors": ["A. Vaswani", "N. Shazeer"],
    "abstract": "We propose a new architecture based entirely on attention.",
    "published": "2023-05-16",
    "categories": ["cs.CL", "cs.LG"],
    "pdf_url": "https://arxiv.org/pdf/2305.09702",
    "abs_url": "https://arxiv.org/abs/2305.09702",
}


class TestFormatPaper:
    def test_includes_title(self):
        out = _format_paper(SAMPLE_PAPER)
        assert "Attention Is All You Need" in out

    def test_includes_authors(self):
        out = _format_paper(SAMPLE_PAPER)
        assert "Vaswani" in out

    def test_includes_link(self):
        out = _format_paper(SAMPLE_PAPER)
        assert "https://arxiv.org/abs/2305.09702" in out

    def test_without_abstract(self):
        out = _format_paper(SAMPLE_PAPER, include_abstract=False)
        assert "attention" not in out.lower().split("abstract")[-1] if "abstract" in out.lower() else True

    def test_truncates_many_authors(self):
        paper = {**SAMPLE_PAPER, "authors": [f"Author {i}" for i in range(15)]}
        out = _format_paper(paper)
        assert "+7 more" in out


class TestLatexToReadable:
    def test_strips_preamble(self):
        tex = r"""\documentclass{article}
\usepackage{amsmath}
\begin{document}
Hello world.
\end{document}"""
        result = _latex_to_readable(tex)
        assert "Hello world" in result
        assert "documentclass" not in result
        assert "usepackage" not in result

    def test_converts_sections(self):
        tex = r"""\begin{document}
\section{Introduction}
Some text.
\subsection{Background}
More text.
\end{document}"""
        result = _latex_to_readable(tex)
        assert "## Introduction" in result
        assert "### Background" in result

    def test_converts_text_formatting(self):
        tex = r"\begin{document}\textbf{bold} and \textit{italic}\end{document}"
        result = _latex_to_readable(tex)
        assert "**bold**" in result
        assert "*italic*" in result

    def test_converts_equations(self):
        tex = r"""\begin{document}
\begin{equation}
E = mc^2
\end{equation}
\end{document}"""
        result = _latex_to_readable(tex)
        assert "$$" in result
        assert "E = mc^2" in result

    def test_converts_itemize(self):
        tex = r"""\begin{document}
\begin{itemize}
\item First
\item Second
\end{itemize}
\end{document}"""
        result = _latex_to_readable(tex)
        assert "- First" in result
        assert "- Second" in result

    def test_strips_comments(self):
        tex = textwrap.dedent("""\
            \\begin{document}
            % This is a comment
            Real content.
            \\end{document}""")
        result = _latex_to_readable(tex)
        assert "comment" not in result
        assert "Real content" in result

    def test_converts_cite(self):
        tex = r"\begin{document}As shown in \cite{vaswani2017}.\end{document}"
        result = _latex_to_readable(tex)
        assert "[vaswani2017]" in result
