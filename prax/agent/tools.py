"""LangChain tool wrappers — core tools that are NOT plugin-managed.

Reader/media tools (NPR, web summary, PDF, YouTube) have been migrated to the
plugin system under ``prax/plugins/tools/``.  Only tools that are truly part
of the kernel (search, datetime, URL fetch) remain here.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from langchain_core.tools import tool

from prax.agent.codegen_tools import build_codegen_tools_for_main_agent
from prax.agent.course_tools import build_course_tools
from prax.agent.doctor import build_doctor_tools
from prax.agent.research_agent import build_research_tools
from prax.agent.scheduler_tools import build_scheduler_tools
from prax.agent.spokes import build_all_spoke_tools
from prax.agent.subagent import build_subagent_tools
from prax.agent.vision_tools import build_vision_tools
from prax.agent.workspace_tools import build_workspace_tools
from prax.helpers_functions import background_search


def _run_coro_safely(coro_factory: Callable[[], asyncio.Future]):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    result: dict = {}

    def _worker():
        result["value"] = asyncio.run(coro_factory())

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join()
    return result.get("value", "")


@tool
def background_search_tool(query: str) -> str:
    """Perform a live web search and return a synthesis of web results.

    Good for: general research, factual questions, topic overviews.
    NOT suitable for: live prices, current fares, exchange rates, or any
    query where a specific numeric value must be accurate. This tool
    returns search-engine snippets, not structured pricing data. Do NOT
    quote specific prices or fares from these results.
    """
    return _run_coro_safely(lambda: background_search(query, to_number=None, sms_bool=False))


@tool
def get_current_datetime(timezone_name: str = "UTC") -> str:
    """Get the current date and time. Pass a timezone name like 'America/New_York',
    'America/Los_Angeles', 'Europe/London', etc. Defaults to UTC if not specified.
    Check the user's notes in their workspace if you don't know their timezone — ask them if needed.
    """
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(timezone_name)
    except (KeyError, Exception):
        tz = UTC
        timezone_name = "UTC (invalid timezone provided)"
    now = datetime.now(tz)
    return (
        f"Current date/time: {now.strftime('%A, %B %d, %Y at %I:%M %p')} "
        f"({timezone_name})"
    )


@tool
def fetch_url_content(url: str) -> str:
    """Fetch the text content of a URL without launching a full browser.

    Use this as the FIRST approach when a user shares a URL.  It is fast and
    lightweight.  Supports x.com/twitter.com links via the oEmbed API.
    For JavaScript-heavy sites that return empty content, fall back to
    delegate_browser.
    """
    import re

    import requests as _requests

    # x.com / twitter.com — use oEmbed API for tweet text.
    if any(d in url for d in ("x.com/", "twitter.com/")):
        try:
            resp = _requests.get(
                "https://publish.twitter.com/oembed",
                params={"url": url, "omit_script": "true"},
                timeout=10,
            )
            if resp.ok:
                data = resp.json()
                html = data.get("html", "")

                # Extract URLs from <a href="..."> BEFORE stripping tags.
                raw_urls = re.findall(r'href="(https?://[^"]+)"', html)

                # Resolve t.co redirects to get actual destination URLs.
                embedded_links: list[str] = []
                for link in raw_urls:
                    # Skip twitter/x internal links (profile, tweet permalink).
                    if any(d in link for d in ("twitter.com/", "x.com/", "pic.twitter.com")):
                        continue
                    if "t.co/" in link:
                        try:
                            r = _requests.head(link, allow_redirects=True, timeout=5)
                            embedded_links.append(r.url)
                        except Exception:
                            embedded_links.append(link)
                    else:
                        embedded_links.append(link)

                text = re.sub(r"<[^>]+>", "", html).strip()
                author = data.get("author_name", "Unknown")
                result = f"Tweet by {author}:\n{text}"
                if embedded_links:
                    result += "\n\nLinks in tweet:\n" + "\n".join(f"  - {lnk}" for lnk in embedded_links)
                result += f"\n\nSource: {url}"
                return result
        except Exception:
            pass
        return f"Could not fetch tweet. The URL may require authentication: {url}"

    # General URLs — simple HTTP fetch + basic text extraction.
    try:
        resp = _requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "html" not in content_type and "text" not in content_type:
            return f"URL returned non-text content ({content_type}). Try browser_open instead."

        html = resp.text

        # Extract title before stripping tags.
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else "Untitled"

        # Remove script/style blocks, then strip tags.
        html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) > 10000:
            text = text[:10000] + "\n\n[Content truncated]"

        return f"Title: {title}\n\n{text}\n\nSource: {url}"
    except Exception as e:
        return f"Failed to fetch URL: {e}. Try browser_open for JavaScript-heavy sites."


def build_default_tools():
    return (
        [background_search_tool, get_current_datetime, fetch_url_content]
        + build_workspace_tools()
        + build_scheduler_tools()
        + build_codegen_tools_for_main_agent()
        + build_subagent_tools()
        + build_all_spoke_tools()  # browser, content, finetune, knowledge, sandbox, sysadmin
        + build_course_tools()
        + build_research_tools()
        + build_vision_tools()
        + build_doctor_tools()
    )
