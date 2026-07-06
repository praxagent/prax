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
import re

import requests

from prax.settings import settings

logger = logging.getLogger(__name__)

_READER_BASE = "https://r.jina.ai/"

# X / Twitter status URLs → capture the numeric tweet id.
_X_STATUS_RE = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:x\.com|twitter\.com)/[^/\s]+/status(?:es)?/(\d+)",
    re.IGNORECASE,
)


def _twitter_token() -> str:
    return (getattr(settings, "twitter_api", None) or "").strip()


def _format_tweet_markdown(tweet: dict, includes: dict) -> str:
    users = {u["id"]: u for u in (includes.get("users") or [])}
    author = users.get(tweet.get("author_id"), {})
    name, handle = author.get("name", ""), author.get("username", "")
    # Long ("note") tweets carry the full body under note_tweet.text.
    text = ((tweet.get("note_tweet") or {}).get("text")) or tweet.get("text", "")
    created = tweet.get("created_at", "")
    pm = tweet.get("public_metrics") or {}
    metrics = ""
    if pm:
        metrics = (f"\n\n*{pm.get('like_count', 0)} likes · "
                   f"{pm.get('retweet_count', 0)} reposts · "
                   f"{pm.get('reply_count', 0)} replies*")
    header = f"# Tweet by {name} (@{handle})" if handle else "# Tweet"
    date_line = f"\n\n*{created}*" if created else ""
    return f"{header}{date_line}\n\n{text}{metrics}\n"


def fetch_tweet_via_api(url: str, *, timeout: int = 15) -> str | None:
    """Fetch an x.com/twitter.com STATUS link via the X API v2 as markdown.

    Returns ``None`` (so the caller falls back to the web reader) when the URL is
    not a tweet, ``TWITTER_API`` isn't configured, or the API call fails. X has
    locked down unauthenticated scraping, so the reader/browser path fails on
    tweets — the API is the only reliable route.
    """
    m = _X_STATUS_RE.search(url or "")
    if not m:
        return None
    token = _twitter_token()
    if not token:
        return None
    tweet_id = m.group(1)
    try:
        resp = requests.get(
            f"https://api.twitter.com/2/tweets/{tweet_id}",
            params={
                "tweet.fields": "created_at,public_metrics,note_tweet,lang",
                "expansions": "author_id",
                "user.fields": "name,username",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.warning("Twitter API request failed for %s: %s", url, exc)
        return None
    if resp.status_code >= 400:
        logger.warning("Twitter API HTTP %s for tweet %s", resp.status_code, tweet_id)
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    tweet = (data or {}).get("data")
    if not tweet:
        return None
    return _format_tweet_markdown(tweet, data.get("includes", {}))


# ---------------------------------------------------------------------------
# Bluesky (AT Protocol) — the public AppView needs NO auth for public posts
# ---------------------------------------------------------------------------

_BSKY_POST_RE = re.compile(
    r"https?://(?:www\.)?bsky\.app/profile/([^/\s]+)/post/([A-Za-z0-9]+)", re.IGNORECASE)
_BSKY_APPVIEW = "https://public.api.bsky.app/xrpc"


def _format_bsky_markdown(post: dict) -> str:
    author = post.get("author") or {}
    name, handle = author.get("displayName") or "", author.get("handle") or ""
    record = post.get("record") or {}
    text = record.get("text") or ""
    when = post.get("indexedAt") or record.get("createdAt") or ""
    metrics = (f"\n\n*{post.get('likeCount', 0)} likes · "
               f"{post.get('repostCount', 0)} reposts · "
               f"{post.get('replyCount', 0)} replies*")
    header = f"# Bluesky post by {name} (@{handle})" if handle else "# Bluesky post"
    date_line = f"\n\n*{when}*" if when else ""
    return f"{header}{date_line}\n\n{text}{metrics}\n"


def fetch_bsky_via_api(url: str, *, timeout: int = 15) -> str | None:
    """Fetch a Bluesky post via the public AT-Protocol AppView (no auth needed).

    Resolves the handle → DID, builds the ``at://`` URI, and calls
    ``app.bsky.feed.getPosts``. Returns ``None`` (→ reader fallback) for non-post
    URLs or on any error.
    """
    m = _BSKY_POST_RE.search(url or "")
    if not m:
        return None
    actor, rkey = m.group(1), m.group(2)
    try:
        did = actor
        if not actor.startswith("did:"):
            r = requests.get(f"{_BSKY_APPVIEW}/com.atproto.identity.resolveHandle",
                             params={"handle": actor}, timeout=timeout)
            if r.status_code >= 400:
                return None
            did = (r.json() or {}).get("did")
            if not did:
                return None
        at_uri = f"at://{did}/app.bsky.feed.post/{rkey}"
        r = requests.get(f"{_BSKY_APPVIEW}/app.bsky.feed.getPosts",
                         params={"uris": at_uri}, timeout=timeout)
        if r.status_code >= 400:
            return None
        posts = (r.json() or {}).get("posts") or []
        return _format_bsky_markdown(posts[0]) if posts else None
    except requests.RequestException as exc:
        logger.warning("Bluesky API failed for %s: %s", url, exc)
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Threads (Meta) — needs a token AND Advanced Access to read third-party posts;
# the web URL has a shortcode, not the media id, so we decode it (base64).
# ---------------------------------------------------------------------------

_THREADS_POST_RE = re.compile(
    r"https?://(?:www\.)?threads\.(?:net|com)/(?:@[^/\s]+/post|t)/([A-Za-z0-9_-]+)",
    re.IGNORECASE)
_IG_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _threads_token() -> str:
    return (getattr(settings, "threads_api", None) or "").strip()


def _shortcode_to_media_id(shortcode: str) -> int | None:
    """Threads/Instagram shortcodes are base64 (``_IG_ALPHABET``) big-endian
    encodings of the numeric media id."""
    n = 0
    for ch in shortcode:
        i = _IG_ALPHABET.find(ch)
        if i < 0:
            return None
        n = n * 64 + i
    return n or None


def fetch_threads_via_api(url: str, *, timeout: int = 15) -> str | None:
    """Best-effort Threads fetch via the Graph API when ``THREADS_API`` is set.

    NOTE: Meta only returns third-party public posts to apps granted **Advanced
    Access** for ``threads_basic`` (otherwise just official Meta accounts / your
    own tester posts). There's no oEmbed and no URL→content endpoint, so we decode
    the URL shortcode to a media id and query it. Fails safe to the reader on any
    error / access denial.
    """
    m = _THREADS_POST_RE.search(url or "")
    if not m:
        return None
    token = _threads_token()
    if not token:
        return None
    media_id = _shortcode_to_media_id(m.group(1))
    if not media_id:
        return None
    try:
        r = requests.get(
            f"https://graph.threads.net/v1.0/{media_id}",
            params={"fields": "text,username,permalink,timestamp", "access_token": token},
            timeout=timeout)
    except requests.RequestException as exc:
        logger.warning("Threads API failed for %s: %s", url, exc)
        return None
    if r.status_code >= 400:
        logger.warning("Threads API HTTP %s for %s (third-party posts need Advanced Access)",
                       r.status_code, url)
        return None
    try:
        data = r.json()
    except Exception:
        return None
    text = data.get("text")
    if not text:
        return None
    user, when = data.get("username") or "", data.get("timestamp") or ""
    header = f"# Threads post by @{user}" if user else "# Threads post"
    date_line = f"\n\n*{when}*" if when else ""
    return f"{header}{date_line}\n\n{text}\n"


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
    # SSRF guard: reject internal/metadata targets before handing the URL to
    # the reader (defense-in-depth — don't make the fetcher a confused deputy).
    try:
        from prax.utils.ssrf import SSRFError, validate_url
        validate_url(url)
    except SSRFError as exc:
        raise ReaderError(f"Refusing to fetch blocked URL: {exc}") from exc

    # Social posts: platforms that block scraping route through their APIs first,
    # each fail-safe (returns None → fall through to the web reader).
    for _social in (fetch_tweet_via_api, fetch_bsky_via_api, fetch_threads_via_api):
        social_md = _social(url, timeout=timeout)
        if social_md:
            return social_md[:max_chars]

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
