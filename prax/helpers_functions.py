import asyncio
import glob
import logging
import os

from langchain_community.tools import DuckDuckGoSearchRun

from prax.settings import settings
from prax.sms import send_sms  # re-export for backwards compat

search_tool = DuckDuckGoSearchRun()

logger = logging.getLogger(__name__)


def delete_temp_files(call_sid):
    file_pattern = f"./static/temp/{call_sid}_*"
    files_to_delete = glob.glob(file_pattern)
    if files_to_delete:
        for file_path in files_to_delete:
            try:
                os.remove(file_path)
                logger.info("File %s has been deleted.", file_path)
            except FileNotFoundError:
                logger.warning("File %s not found.", file_path)
            except PermissionError:
                logger.warning("Permission denied to delete %s.", file_path)
            except OSError as e:
                logger.error("Error deleting %s: %s", file_path, e)
    else:
        logger.debug("No files found with prefix '%s'.", file_pattern)
    return None

def create_convo_state():
    return {
        'convo_started': True,
        'model_name': settings.base_model,
        'music': True,
        'language': 'en',
        'news_stale': True,
        'arxiv_stale': True,
        'chat_mode': True,
        'reader_mode': False,
        'read_buffer': {},
        'reader_data': [],
        'buffer_redirect': None,
        'reader_source': None,
        'start_index': 0,
        'in_article': False,
        'article_index': None,
        'article_content': None,
        'old_start_index': None,
        'buffer_link': None,
        'buffer_comments': None,
        'buffer_title': None,
        'None_stale': None,
        'current_buffer_id': None,
    }

def gather_speech(response, language_code):
    response.play('/static/mp3/beep.mp3', loop=1)
    response.gather(
        speech_timeout='auto',
        speech_model='experimental_conversations',
        input='speech',
        action='/respond',
        language='en-US' if language_code == 'en' else f'{language_code}-{language_code.upper()}',
        timeout=30,
    )


# Per-request HTTP timeout for the keyed providers. WEB_SEARCH_TIMEOUT_S is the
# outer wall-clock ceiling (background_search_tool); this bounds a single call so
# a slow provider can't sit on the connection.
_SEARCH_HTTP_TIMEOUT = 15


def _format_search_results(items: list[dict], answer: str = "") -> str:
    """Format normalised hits ({title, body, url}) as grounding-friendly lines:
    optional leading answer, then ``- title — snippet (url)`` per result."""
    lines: list[str] = []
    if answer and answer.strip():
        lines.append(f"Answer: {answer.strip()}")
    for r in items:
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()
        url = (r.get("url") or "").strip()
        lines.append(f"- {title} — {body} ({url})")
    return "\n".join(lines) if lines else "No search results found."


def _missing_key_msg(provider: str, env_key: str) -> str:
    return (
        f"Search provider '{provider}' needs {env_key}, which isn't configured. "
        f"Set it, or switch SEARCH_PROVIDER (the 'ddgs' provider works without a key)."
    )


def _ddgs_search(query: str) -> str:
    """Search via the maintained ``ddgs`` package (no key). Still scrapes
    DuckDuckGo's frontend, so it can rate-limit — prefer a keyed provider."""
    from ddgs import DDGS

    results = DDGS(timeout=10).text(query, max_results=settings.search_max_results)
    items = [
        {"title": r.get("title"), "body": r.get("body"),
         "url": r.get("href") or r.get("url")}
        for r in (results or [])
    ]
    return _format_search_results(items)


def _brave_search(query: str) -> str:
    """Brave Search API — an independent index behind a real, keyed API."""
    key = getattr(settings, "brave_api_key", None)
    if not key:
        return _missing_key_msg("brave", "BRAVE_API_KEY")
    import requests

    resp = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": settings.search_max_results},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
        timeout=_SEARCH_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    web = (resp.json().get("web") or {}).get("results") or []
    items = [
        {"title": r.get("title"), "body": r.get("description"), "url": r.get("url")}
        for r in web
    ]
    return _format_search_results(items)


def _jina_search(query: str) -> str:
    """Jina Search (``s.jina.ai``) — reuses JINA_API_KEY (keyless free tier).
    ``X-Respond-With: no-content`` returns SERP metadata only (title/snippet/url),
    not full page bodies, keeping the grounding payload small."""
    import requests

    headers = {"Accept": "application/json", "X-Respond-With": "no-content"}
    key = getattr(settings, "jina_api_key", None)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    resp = requests.get(
        "https://s.jina.ai/", params={"q": query},
        headers=headers, timeout=_SEARCH_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json().get("data") or []
    items = [
        {"title": r.get("title"), "body": r.get("description") or r.get("content"),
         "url": r.get("url")}
        for r in data[: settings.search_max_results]
    ]
    return _format_search_results(items)


def _tavily_search(query: str) -> str:
    """Tavily — LLM/agent-optimised: returns extracted content plus an optional
    synthesised answer, surfaced first so the model can ground on it directly."""
    key = getattr(settings, "tavily_api_key", None)
    if not key:
        return _missing_key_msg("tavily", "TAVILY_API_KEY")
    import requests

    resp = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": key, "query": query,
            "max_results": settings.search_max_results,
            "include_answer": True, "search_depth": "basic",
        },
        timeout=_SEARCH_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    items = [
        {"title": r.get("title"), "body": r.get("content"), "url": r.get("url")}
        for r in (data.get("results") or [])
    ]
    return _format_search_results(items, answer=data.get("answer") or "")


# SEARCH_PROVIDER -> handler function NAME. Resolved through module globals at
# call time (not bound here) so the reloader — and tests — see the current
# function. 'legacy' (and any unknown value) falls through to the
# DuckDuckGoSearchRun path below.
_SEARCH_PROVIDERS = {
    "ddgs": "_ddgs_search",
    "brave": "_brave_search",
    "jina": "_jina_search",
    "tavily": "_tavily_search",
}


async def background_search(text_input, to_number, sms_bool=True):
    """Perform a web search and optionally SMS the result.

    Provider selected by ``SEARCH_PROVIDER``: keyless 'legacy' (default,
    DuckDuckGoSearchRun) / 'ddgs'; or a keyed real Search API 'brave' /
    'tavily' / 'jina'. A keyed provider whose key is missing returns an
    actionable message; a provider that raises (HTTP/timeout) is caught and
    returned as a clear error so the turn never crashes on search.
    """
    provider = (getattr(settings, "search_provider", "legacy") or "legacy").lower()
    handler_name = _SEARCH_PROVIDERS.get(provider)
    if handler_name is not None:
        handler = globals()[handler_name]
        try:
            result = await asyncio.to_thread(handler, text_input)
        except Exception as exc:  # HTTP error, timeout, malformed response
            result = (
                f"Search via '{provider}' failed: {type(exc).__name__}: {exc}. "
                f"Check the provider's API key/quota or switch SEARCH_PROVIDER."
            )
    else:
        result = await asyncio.to_thread(search_tool.run, text_input)

    if sms_bool and to_number:
        send_sms(result, to_number)
    return result
