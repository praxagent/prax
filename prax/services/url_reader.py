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


def _thread_fetch_enabled() -> bool:
    return bool(getattr(settings, "twitter_thread_fetch", False))


# Everything the formatters need: entities expand t.co links, conversation_id
# and in_reply_to_user_id drive thread assembly.
_TWEET_FIELDS = (
    "created_at,public_metrics,note_tweet,lang,conversation_id,"
    "in_reply_to_user_id,entities"
)


def _expand_urls_in_text(text: str, entities: dict | None) -> str:
    """Replace t.co short links with their expanded URLs (best-effort)."""
    for ent in (entities or {}).get("urls") or []:
        short, full = ent.get("url"), ent.get("expanded_url")
        if short and full:
            text = text.replace(short, full)
    return text


def _tweet_body(tweet: dict) -> str:
    """Full tweet text with t.co links expanded.

    Long ("note") tweets carry the full body — and its own entity offsets —
    under ``note_tweet``.
    """
    note = tweet.get("note_tweet") or {}
    if note.get("text"):
        return _expand_urls_in_text(note["text"], note.get("entities"))
    return _expand_urls_in_text(tweet.get("text", ""), tweet.get("entities"))


def _tweet_metrics_line(tweet: dict, *, label: str = "") -> str:
    pm = tweet.get("public_metrics") or {}
    if not pm:
        return ""
    suffix = f" ({label})" if label else ""
    return (f"\n\n*{pm.get('like_count', 0)} likes · "
            f"{pm.get('retweet_count', 0)} reposts · "
            f"{pm.get('reply_count', 0)} replies{suffix}*")


def _format_tweet_markdown(tweet: dict, includes: dict) -> str:
    users = {u["id"]: u for u in (includes.get("users") or [])}
    author = users.get(tweet.get("author_id"), {})
    name, handle = author.get("name", ""), author.get("username", "")
    text = _tweet_body(tweet)
    created = tweet.get("created_at", "")
    header = f"# Tweet by {name} (@{handle})" if handle else "# Tweet"
    date_line = f"\n\n*{created}*" if created else ""
    return f"{header}{date_line}\n\n{text}{_tweet_metrics_line(tweet)}\n"


def _format_thread_markdown(posts: list[dict], author: dict, truncated: bool) -> str:
    """Render an ordered self-thread as one markdown document."""
    name, handle = author.get("name", ""), author.get("username", "")
    who = f"{name} (@{handle})" if handle else "author"
    root = posts[0]
    created = root.get("created_at", "")
    parts = [f"# X thread by {who} — {len(posts)} posts"]
    if created:
        parts.append(f"\n*{created}*")
    for i, t in enumerate(posts, 1):
        parts.append(f"\n## {i}/{len(posts)}\n\n{_tweet_body(t)}")
    metrics = _tweet_metrics_line(root, label="root post").strip("\n")
    if metrics:
        parts.append(f"\n{metrics}")
    if truncated:
        parts.append("\n*[Thread truncated — more than 100 self-replies]*")
    return "\n".join(parts) + "\n"


def _get_tweet(tweet_id: str, token: str, *, timeout: int = 15) -> dict | None:
    """GET /2/tweets/{id} with the standard field set; full payload or None."""
    try:
        resp = requests.get(
            f"https://api.twitter.com/2/tweets/{tweet_id}",
            params={
                "tweet.fields": _TWEET_FIELDS,
                "expansions": "author_id",
                "user.fields": "name,username",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.warning("Twitter API request failed for tweet %s: %s", tweet_id, exc)
        return None
    if resp.status_code >= 400:
        logger.warning("Twitter API HTTP %s for tweet %s", resp.status_code, tweet_id)
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    return data if (data or {}).get("data") else None


def _search_self_thread(
    conversation_id: str, username: str, token: str, *, timeout: int = 15,
) -> tuple[list[dict], bool] | None:
    """Self-thread posts (the author replying to themself) in a conversation.

    Uses the 7-day recent-search window, one page (100 posts).  Returns
    ``(tweets, truncated)``, or ``None`` when search is unavailable (API tier
    without search access, rate limit, network) so callers can fall back to
    the single tweet.
    """
    try:
        resp = requests.get(
            "https://api.twitter.com/2/tweets/search/recent",
            params={
                "query": (
                    f"conversation_id:{conversation_id} "
                    f"from:{username} to:{username}"
                ),
                "max_results": 100,
                "tweet.fields": _TWEET_FIELDS,
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.warning("Twitter thread search failed for %s: %s", conversation_id, exc)
        return None
    if resp.status_code >= 400:
        logger.warning(
            "Twitter thread search HTTP %s for conversation %s "
            "(recent search needs Basic tier or above)",
            resp.status_code, conversation_id,
        )
        return None
    try:
        payload = resp.json()
    except Exception:
        return None
    tweets = payload.get("data") or []
    truncated = bool((payload.get("meta") or {}).get("next_token"))
    return tweets, truncated


def _maybe_fetch_thread(
    tweet: dict, includes: dict, token: str, *, timeout: int = 15,
) -> str | None:
    """Assemble the author's full self-thread around *tweet*, if there is one.

    Returns thread markdown, or ``None`` to fall back to the single-tweet
    render (flag off, no replies, search unavailable, or no self-replies
    actually found).
    """
    if not _thread_fetch_enabled():
        return None
    cid = tweet.get("conversation_id")
    reply_count = (tweet.get("public_metrics") or {}).get("reply_count", 0)
    if not cid or not reply_count:
        return None
    users = {u["id"]: u for u in (includes.get("users") or [])}
    author = users.get(tweet.get("author_id")) or {}
    username = author.get("username")
    if not username:
        return None
    found = _search_self_thread(cid, username, token, timeout=timeout)
    if found is None:
        return None
    replies, truncated = found
    posts = {str(t["id"]): t for t in replies if t.get("id")}
    posts[str(tweet["id"])] = tweet  # ensure the linked tweet is included
    if str(cid) not in posts:
        # Linked mid-thread: the root isn't in the search results (it is not
        # a reply *to* the author), so fetch it directly.  A deleted root is
        # fine — we render what we have.
        root_data = _get_tweet(str(cid), token, timeout=timeout)
        if root_data:
            posts[str(cid)] = root_data["data"]
    if len(posts) < 2:
        return None  # no actual self-thread — use the single-tweet render
    ordered = [posts[k] for k in sorted(posts, key=int)]
    return _format_thread_markdown(ordered, author, truncated)


def fetch_tweet_via_api(url: str, *, timeout: int = 15) -> str | None:
    """Fetch an x.com/twitter.com STATUS link via the X API v2 as markdown.

    Returns ``None`` (so the caller falls back to the web reader) when the URL is
    not a tweet, ``TWITTER_API`` isn't configured, or the API call fails. X has
    locked down unauthenticated scraping, so the reader/browser path fails on
    tweets — the API is the only reliable route.

    With ``TWITTER_THREAD_FETCH=true``, a tweet that is part of a thread is
    expanded to the author's full self-thread (root + self-replies, in posting
    order) via recent search; anything that blocks the expansion (API tier,
    rate limit, thread older than the 7-day search window) degrades to the
    single linked tweet.
    """
    m = _X_STATUS_RE.search(url or "")
    if not m:
        return None
    token = _twitter_token()
    if not token:
        return None
    data = _get_tweet(m.group(1), token, timeout=timeout)
    if not data:
        return None
    tweet = data["data"]
    includes = data.get("includes", {})
    thread_md = _maybe_fetch_thread(tweet, includes, token, timeout=timeout)
    if thread_md:
        return thread_md
    return _format_tweet_markdown(tweet, includes)


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


# Human-readable labels for the non-reader fetch paths, keyed by the source
# string that fetch_markdown_with_source() returns.  Callers use these to
# surface fetch provenance (e.g. fetch_url_content's "Source:" line).
SOCIAL_SOURCE_LABELS = {
    "x-api": "X API v2",
    "bluesky-api": "Bluesky AppView API",
    "threads-api": "Threads Graph API",
}


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
    return fetch_markdown_with_source(url, timeout=timeout, max_chars=max_chars)[0]


def fetch_markdown_with_source(
    url: str,
    *,
    timeout: int = 20,
    max_chars: int = 50_000,
) -> tuple[str, str]:
    """Like :func:`fetch_markdown`, but also reports which path produced the
    content: ``"x-api"``, ``"bluesky-api"``, ``"threads-api"`` (native platform
    APIs — structured data), or ``"web-reader"`` (scraped page text).
    """
    # SSRF guard: reject internal/metadata targets before handing the URL to
    # the reader (defense-in-depth — don't make the fetcher a confused deputy).
    try:
        from prax.utils.ssrf import SSRFError, validate_url
        validate_url(url)
    except SSRFError as exc:
        raise ReaderError(f"Refusing to fetch blocked URL: {exc}") from exc

    # Social posts: platforms that block scraping route through their APIs
    # first, each fail-safe (returns None → fall through to the web reader).
    # Fetchers are looked up at call time so tests can monkeypatch them.
    social_fetchers = (
        (fetch_tweet_via_api, "x-api"),
        (fetch_bsky_via_api, "bluesky-api"),
        (fetch_threads_via_api, "threads-api"),
    )
    for _social, source in social_fetchers:
        social_md = _social(url, timeout=timeout)
        if social_md:
            return social_md[:max_chars], source

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

    return text, "web-reader"


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
