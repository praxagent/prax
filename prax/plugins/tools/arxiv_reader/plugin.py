"""arXiv paper reader — fetch and list papers from arXiv listing pages."""
from langchain_core.tools import tool

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "Fetch arXiv paper listings with titles, abstracts, and authors"


@tool
def arxiv_fetch_papers(url: str) -> str:
    """Fetch papers from an arXiv listing page.

    Args:
        url: An arXiv listing URL, e.g. "https://arxiv.org/list/astro-ph/new".

    Returns a formatted list of papers with identifiers, titles, authors,
    abstracts, and source links.
    """

    # The reader expects call_sid with arxiv_url in convo_states;
    # call it directly with the URL parsing done here.
    import requests
    from bs4 import BeautifulSoup

    response = requests.get(url, timeout=30)
    if response.status_code != 200:
        return f"Failed to fetch arXiv page (status {response.status_code})."

    html = response.text
    soup = BeautifulSoup(html, "html.parser")

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
                a.text
                for a in dd.find("div", class_="list-authors").find_all("a")
            ]
            source_link = (
                f"https://arxiv.org/abs/{identifier.replace('arXiv:', '')}"
            )
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


def register():
    return [arxiv_fetch_papers]
