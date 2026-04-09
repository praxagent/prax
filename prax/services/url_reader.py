"""Jina Reader wrapper — single source of truth for URL→clean-markdown.

All of Prax's URL fetching for note creation, auto-capture, and the
orchestrator-level ``fetch_url_content`` tool routes through this
helper.  Consolidating here gives us:

- One place to add the ``Authorization: Bearer`` header when
  ``JINA_API_KEY`` is set (upgrades from the free tier to paid quota).
- Consistent error messages, timeouts, and truncation behavior.
- A single swap-point if we ever want to replace the underlying reader
  service with something else (Firecrawl, Trafilatura, custom crawler).

The Jina Reader renders pages with a real headless browser server-side,
which is why its output is clean enough to feed straight into the
deep-dive note pipeline without the FontAwesome-icon noise that a raw
``requests.get`` + BeautifulSoup path produces.
"""
from __future__ import annotations

import logging

import requests

from prax.settings import settings

logger = logging.getLogger(__name__)

_READER_BASE = "https://r.jina.ai/"


class ReaderError(RuntimeError):
    """Raised when the reader service cannot produce usable content."""


def _headers() -> dict[str, str]:
    """Build the request headers, adding auth if a Jina key is configured."""
    headers = {
        "Accept": "text/markdown",
        "X-No-Cache": "true",
    }
    key = getattr(settings, "jina_api_key", None)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def fetch_markdown(
    url: str,
    *,
    timeout: int = 20,
    max_chars: int = 50_000,
) -> str:
    """Fetch ``url`` and return clean markdown.

    Raises :class:`ReaderError` on any failure (network, HTTP non-2xx,
    empty/minimal response).  Callers are expected to catch and either
    fall back to another fetch path (e.g. ``delegate_browser``) or
    report the failure to the user.

    ``max_chars`` truncates extremely long pages so they don't blow
    downstream context windows.  The default (50 k chars) is enough for
    a typical long-form article while still fitting comfortably in a
    deep-dive writer's context.
    """
    try:
        resp = requests.get(
            f"{_READER_BASE}{url}",
            headers=_headers(),
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        raise ReaderError(f"Reader request failed: {exc}") from exc

    if resp.status_code >= 400:
        raise ReaderError(
            f"Reader returned HTTP {resp.status_code} for {url}"
        )

    text = resp.text.strip()
    if len(text) < 50:
        raise ReaderError(
            f"Reader returned minimal content for {url} — the page may "
            "require JavaScript or authentication. Try delegate_browser "
            "for full browser rendering with a persistent session."
        )

    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n*[Content truncated]*"

    return text


def fetch_markdown_and_title(
    url: str,
    *,
    timeout: int = 20,
    max_chars: int = 50_000,
) -> tuple[str, str]:
    """Fetch ``url`` and return ``(clean_markdown, title)``.

    The Jina reader emits the page title as a ``Title: ...`` line at
    the top of its response when the page has one.  This helper pops
    that line so the body stays clean for synthesis.
    """
    raw = fetch_markdown(url, timeout=timeout, max_chars=max_chars)

    title = ""
    lines = raw.split("\n", 2)
    if lines and lines[0].startswith("Title:"):
        title = lines[0][len("Title:"):].strip()
        body = lines[2] if len(lines) > 2 else ""
        return body, title

    return raw, title


def try_fetch_markdown(url: str, *, timeout: int = 20) -> str | None:
    """Convenience wrapper: returns ``None`` on any error instead of raising.

    Used by callers (like SMS auto-capture) that want to degrade
    gracefully rather than propagate failures to the user.
    """
    try:
        return fetch_markdown(url, timeout=timeout)
    except ReaderError:
        logger.debug("URL reader failed for %s", url, exc_info=True)
        return None
    except Exception:
        logger.exception("URL reader unexpected error for %s", url)
        return None
