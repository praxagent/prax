"""arXiv plugin — search, browse, and save papers as notes."""
import io
import re
import tarfile
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from langchain_core.tools import tool

PLUGIN_VERSION = "2"
PLUGIN_DESCRIPTION = (
    "Search arXiv, browse listings, fetch full paper content, "
    "and save papers as notes"
)

_ARXIV_API = "http://export.arxiv.org/api/query"
_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _search_arxiv(query: str, max_results: int = 10) -> list[dict]:
    """Query the arXiv Atom API and return structured results."""
    resp = requests.get(
        _ARXIV_API,
        params={
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()

    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(resp.text)
    papers = []
    for entry in root.findall("atom:entry", ns):
        arxiv_id = (entry.findtext("atom:id", "", ns) or "").split("/abs/")[-1]
        title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
        summary = (entry.findtext("atom:summary", "", ns) or "").strip()
        authors = [
            a.findtext("atom:name", "", ns)
            for a in entry.findall("atom:author", ns)
        ]
        published = (entry.findtext("atom:published", "", ns) or "")[:10]

        categories = [
            c.get("term", "")
            for c in entry.findall("arxiv:primary_category", ns)
        ] + [
            c.get("term", "")
            for c in entry.findall("atom:category", ns)
        ]
        # Deduplicate while preserving order.
        seen = set()
        unique_cats = []
        for c in categories:
            if c and c not in seen:
                seen.add(c)
                unique_cats.append(c)

        pdf_link = ""
        for link in entry.findall("atom:link", ns):
            if link.get("title") == "pdf":
                pdf_link = link.get("href", "")
                break

        papers.append({
            "id": arxiv_id,
            "title": title,
            "authors": authors,
            "abstract": summary,
            "published": published,
            "categories": unique_cats,
            "pdf_url": pdf_link,
            "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
        })
    return papers


def _format_paper(p: dict, include_abstract: bool = True) -> str:
    """Format a single paper dict as readable text."""
    lines = [
        f"**{p['title']}**",
        f"  ID: {p['id']}",
        f"  Authors: {', '.join(p['authors'][:8])}"
        + (f" (+{len(p['authors']) - 8} more)" if len(p['authors']) > 8 else ""),
    ]
    if p.get("published"):
        lines.append(f"  Published: {p['published']}")
    if p.get("categories"):
        lines.append(f"  Categories: {', '.join(p['categories'][:5])}")
    if include_abstract:
        abstract = p["abstract"][:400]
        if len(p["abstract"]) > 400:
            abstract += "..."
        lines.append(f"  Abstract: {abstract}")
    lines.append(f"  Link: {p['abs_url']}")
    return "\n".join(lines)


def _fetch_paper_detail(arxiv_id: str) -> dict | None:
    """Fetch a single paper by ID via the arXiv API."""
    results = _search_arxiv(f"id:{arxiv_id}", max_results=1)
    return results[0] if results else None


def _extract_latex_source(arxiv_id: str) -> str | None:
    """Download and extract the main .tex file from an arXiv e-print."""
    url = f"https://arxiv.org/e-print/{arxiv_id}"
    try:
        resp = requests.get(url, timeout=60, stream=True)
        if resp.status_code != 200:
            return None
        tf = tarfile.open(fileobj=io.BytesIO(resp.content))
        # Find the largest .tex file (usually the main paper).
        tex_files = [m for m in tf.getmembers() if m.name.endswith(".tex")]
        if not tex_files:
            return None
        largest = max(tex_files, key=lambda m: m.size)
        f = tf.extractfile(largest)
        if f is None:
            return None
        return f.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _latex_to_readable(tex: str) -> str:
    """Light cleanup of LaTeX source into readable markdown.

    This strips common LaTeX boilerplate while preserving math and structure.
    Not a full converter — just enough to be useful in a note.
    """
    # Remove comments.
    tex = re.sub(r"(?m)^%.*$", "", tex)
    tex = re.sub(r"(?<!\\)%.*$", "", tex, flags=re.MULTILINE)

    # Strip preamble (everything before \begin{document}).
    doc_start = tex.find(r"\begin{document}")
    if doc_start >= 0:
        tex = tex[doc_start + len(r"\begin{document}"):]
    doc_end = tex.find(r"\end{document}")
    if doc_end >= 0:
        tex = tex[:doc_end]

    # Convert sections to markdown headers.
    tex = re.sub(r"\\section\*?\{([^}]+)\}", r"## \1", tex)
    tex = re.sub(r"\\subsection\*?\{([^}]+)\}", r"### \1", tex)
    tex = re.sub(r"\\subsubsection\*?\{([^}]+)\}", r"#### \1", tex)

    # Convert \textbf, \textit, \emph.
    tex = re.sub(r"\\textbf\{([^}]+)\}", r"**\1**", tex)
    tex = re.sub(r"\\textit\{([^}]+)\}", r"*\1*", tex)
    tex = re.sub(r"\\emph\{([^}]+)\}", r"*\1*", tex)

    # Convert \cite{...} to [ref].
    tex = re.sub(r"\\cite\{([^}]+)\}", r"[\1]", tex)

    # Convert equation environments to $$ blocks.
    for env in ("equation", "equation*", "align", "align*", "gather", "gather*"):
        tex = tex.replace(f"\\begin{{{env}}}", "\n$$")
        tex = tex.replace(f"\\end{{{env}}}", "$$\n")

    # Convert \[ \] to $$.
    tex = tex.replace(r"\[", "\n$$")
    tex = tex.replace(r"\]", "$$\n")

    # Strip \label, \ref, \eqref.
    tex = re.sub(r"\\label\{[^}]*\}", "", tex)
    tex = re.sub(r"\\(?:eq)?ref\{([^}]*)\}", r"(\1)", tex)

    # Strip remaining common commands that don't render.
    for cmd in (
        r"\maketitle", r"\tableofcontents", r"\newpage", r"\clearpage",
        r"\noindent", r"\bigskip", r"\medskip", r"\smallskip",
    ):
        tex = tex.replace(cmd, "")

    # Convert itemize/enumerate to markdown lists.
    tex = re.sub(r"\\begin\{(?:itemize|enumerate)\}", "", tex)
    tex = re.sub(r"\\end\{(?:itemize|enumerate)\}", "", tex)
    tex = re.sub(r"\\item\s*", "- ", tex)

    # Convert figure/table environments — keep caption, strip rest.
    tex = re.sub(
        r"\\begin\{(?:figure|table)\}.*?\\caption\{([^}]+)\}.*?\\end\{(?:figure|table)\}",
        r"\n*Figure: \1*\n",
        tex,
        flags=re.DOTALL,
    )
    # Strip remaining environments we can't handle.
    tex = re.sub(r"\\begin\{[^}]+\}", "", tex)
    tex = re.sub(r"\\end\{[^}]+\}", "", tex)

    # Clean up excessive whitespace.
    tex = re.sub(r"\n{3,}", "\n\n", tex)
    return tex.strip()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def arxiv_search(query: str, max_results: int = 10) -> str:
    """Search arXiv for papers matching a query.

    Searches titles, abstracts, and authors.  Supports arXiv query syntax:
    - Simple keywords: "transformer attention mechanism"
    - Field-specific: "ti:attention AND au:vaswani" (ti=title, au=author, abs=abstract, cat=category)
    - Category filter: "cat:cs.LG AND ti:diffusion"

    Common categories: cs.AI, cs.LG, cs.CL, stat.ML, math.OC, physics, astro-ph, quant-ph

    Args:
        query: Search query (keywords or arXiv query syntax).
        max_results: Number of results to return (default 10, max 30).
    """
    try:
        max_results = min(max(max_results, 1), 30)
        papers = _search_arxiv(query, max_results)
        if not papers:
            return f"No papers found for: {query}"
        formatted = [_format_paper(p) for p in papers]
        return f"Found {len(papers)} papers:\n\n" + "\n\n".join(formatted)
    except Exception as e:
        return f"arXiv search failed: {e}"


@tool
def arxiv_fetch_papers(url: str) -> str:
    """Fetch papers from an arXiv listing page.

    Args:
        url: An arXiv listing URL, e.g. "https://arxiv.org/list/astro-ph/new".

    Returns a formatted list of papers with identifiers, titles, authors,
    abstracts, and source links.
    """
    try:
        response = requests.get(url, timeout=_TIMEOUT)
        if response.status_code != 200:
            return f"Failed to fetch arXiv page (status {response.status_code})."

        soup = BeautifulSoup(response.text, "html.parser")

        articles = []
        for dt, dd in zip(soup.find_all("dt"), soup.find_all("dd"), strict=False):
            try:
                title = (
                    dd.find("div", class_="list-title")
                    .text.strip()
                    .replace("Title:", "")
                    .strip()
                )
                identifier = dt.span.a.text
                abstract_el = dd.find("p", class_="mathjax")
                abstract = abstract_el.text.strip() if abstract_el else "Abstract not available"
                authors = [
                    a.text for a in dd.find("div", class_="list-authors").find_all("a")
                ]
                source_link = f"https://arxiv.org/abs/{identifier.replace('arXiv:', '')}"
                articles.append(
                    f"**{title}**\n"
                    f"  ID: {identifier}\n"
                    f"  Authors: {', '.join(authors)}\n"
                    f"  Abstract: {abstract[:300]}{'...' if len(abstract) > 300 else ''}\n"
                    f"  Link: {source_link}"
                )
            except Exception:
                continue

        if not articles:
            return "No papers found on that arXiv page."
        return f"Found {len(articles)} papers:\n\n" + "\n\n".join(articles[:20])
    except Exception as e:
        return f"Failed to fetch arXiv page: {e}"


@tool
def arxiv_to_note(arxiv_id: str, include_source: bool = True) -> str:
    """Fetch an arXiv paper and save it as a note with full content.

    Downloads the paper metadata and optionally the LaTeX source, converts
    it to readable markdown with preserved math ($$), and creates a note
    the user can read in their browser.

    Args:
        arxiv_id: The arXiv paper ID (e.g. "2305.09702" or "2401.12345v2").
        include_source: If True, download and convert LaTeX source. If False, use abstract only.
    """
    from prax.agent.user_context import current_user_id
    from prax.services import note_service
    from prax.utils.ngrok import get_ngrok_url

    uid = current_user_id.get() or "unknown"
    base_url = get_ngrok_url()
    if not base_url:
        return "Cannot create note — NGROK_URL is not configured."

    # Fetch paper metadata.
    paper = _fetch_paper_detail(arxiv_id.strip())
    if not paper:
        return f"Paper not found: {arxiv_id}"

    # Build the note content.
    parts = [
        f"**Authors:** {', '.join(paper['authors'])}",
        f"**Published:** {paper['published']}",
        f"**Categories:** {', '.join(paper['categories'])}",
        f"**arXiv:** [{paper['id']}]({paper['abs_url']})"
        + (f" | [PDF]({paper['pdf_url']})" if paper.get("pdf_url") else ""),
        "",
        "## Abstract",
        "",
        paper["abstract"],
    ]

    # Try to get full paper content from LaTeX source.
    if include_source:
        tex = _extract_latex_source(arxiv_id.strip())
        if tex:
            readable = _latex_to_readable(tex)
            if len(readable) > 200:
                parts.extend(["", "---", "", "## Full Paper", "", readable])
            else:
                parts.append("\n\n*LaTeX source was too short or could not be converted.*")
        else:
            parts.append("\n\n*LaTeX source not available for this paper.*")

    content = "\n".join(parts)
    tags = ["arxiv"] + paper["categories"][:3]

    try:
        meta = note_service.create_note(uid, paper["title"], content, tags)
        result = note_service.publish_notes(uid, base_url, slug=meta["slug"])
        if "error" in result:
            return f"Note saved but Hugo build failed: {result['error']}"
        return (
            f"Paper saved as note: **{paper['title']}**\n"
            f"Note: `{meta['slug']}`\n"
            f"URL: {result['url']}\n\n"
            f"Use note_update to add annotations or summaries."
        )
    except Exception as e:
        return f"Error creating note: {e}"


def register():
    return [arxiv_search, arxiv_fetch_papers, arxiv_to_note]
