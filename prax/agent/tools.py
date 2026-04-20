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
from prax.agent.doctor import build_doctor_tools
from prax.agent.obs_tools import build_obs_tools
from prax.agent.research_agent import build_research_tools
from prax.agent.spokes import build_all_spoke_tools
from prax.agent.subagent import build_subagent_tools
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
    """Fetch the text content of a URL as clean, LLM-friendly markdown.

    Routes through the Jina Reader service (headless browser,
    server-side render) to extract the main content — strips
    navigation, ads, sidebars, and boilerplate automatically.  Honors
    the ``JINA_API_KEY`` setting if configured for paid-tier throughput.

    Use this as the FIRST approach when a user shares a URL.  It is fast
    (~1-2s) and produces high-quality output for articles, docs, and blogs.

    If the result seems incomplete — e.g. only a snippet is returned,
    threaded content is truncated, or the page requires authentication —
    fall back to delegate_browser which uses a full browser with JS
    rendering and persistent login sessions.
    """
    from prax.services.url_reader import ReaderError, fetch_markdown

    try:
        # Orchestrator-level fetches get a smaller cap than the note
        # pipeline because they go straight into turn context.
        text = fetch_markdown(url, max_chars=15_000)
    except ReaderError as exc:
        return (
            f"{exc}\n\nUse delegate_browser for full browser rendering "
            "with JS + persistent sessions if the page needs them."
        )

    return f"{text}\n\nSource: {url}"


def build_default_tools():
    from prax.agent.sandbox_tools import sandbox_shell

    return (
        # Kernel tools — essential for basic reasoning
        [background_search_tool, get_current_datetime, fetch_url_content, sandbox_shell]
        # Orchestrator-level workspace tools (planning, todos, notes, meta)
        + build_workspace_tools()
        # Self-improvement entry points (pending/rollback only)
        + build_codegen_tools_for_main_agent()
        # Sub-agent delegation (delegate_task, delegate_parallel)
        + build_subagent_tools()
        # All spoke delegation tools (browser, content, course, desktop,
        # finetune, knowledge, memory, sandbox, scheduler, sysadmin, workspace)
        + build_all_spoke_tools()
        # Research delegation
        + build_research_tools()
        # Diagnostics
        + build_doctor_tools()
        # Observability — only when the LGTM stack is configured
        + build_obs_tools()
    )
