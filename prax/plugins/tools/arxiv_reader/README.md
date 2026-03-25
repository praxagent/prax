# arXiv Reader Plugin

Search arXiv, browse category listings, and save papers as rendered notes.

## Tools

### `arxiv_search(query, max_results=10)`
Search arXiv by keywords, authors, categories. Supports arXiv query syntax
(`ti:`, `au:`, `abs:`, `cat:`).

### `arxiv_fetch_papers(url)`
Browse an arXiv listing page (e.g. `arxiv.org/list/cs.LG/new`).

### `arxiv_to_note(arxiv_id, include_source=True)`
Fetch a paper by ID, download its LaTeX source, convert to readable markdown
with preserved math, and publish as a note with a shareable URL.

Requires: `requests`, `beautifulsoup4`
