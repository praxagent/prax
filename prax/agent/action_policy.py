"""Lightweight safety/governance layer for side-effectful agent actions.

Classifies tools by risk level, tracks epistemic capability metadata,
and provides helpers for confirmation gating and structured audit logging.
Standalone — no prax imports.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum


class RiskLevel(Enum):
    """How dangerous a tool invocation is.

    LOW    – read-only or local workspace writes (git-backed, reversible)
    MEDIUM – external reads (HTTP GET, API queries), local state changes
    HIGH   – external writes (messages, POST/PUT/DELETE), sandbox exec, file send
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SourceReliability(Enum):
    """How much to trust a tool's output for specific factual claims.

    VERIFIED   – direct, structured data from a purpose-built API (live fare
                 engine, stock ticker, official database).  Safe to quote.
    INDICATIVE – aggregated or scraped data that may be stale/approximate.
                 Must be labeled approximate and source-cited.
    INFORMATIONAL – general web content, search snippets, summaries.  NEVER
                    use as the sole basis for specific numeric claims, dates,
                    rankings, statistics, or quoted statements.
    """

    VERIFIED = "verified"
    INDICATIVE = "indicative"
    INFORMATIONAL = "informational"


# ── tool capability metadata ────────────────────────────────────────

TOOL_CAPABILITIES: dict[str, dict] = {
    # Web search — good for general research, NOT for specific claims.
    "background_search_tool": {
        "reliability": SourceReliability.INFORMATIONAL,
        "good_for": ["general research", "factual questions", "topic overviews"],
        "not_good_for": [
            "live prices", "current fares", "exchange rates", "stock prices",
            "real-time data", "specific statistics", "current rankings",
        ],
        "epistemic_note": (
            "Returns web search snippets, not structured data. "
            "Do NOT state specific numbers, prices, statistics, rankings, "
            "dates, or quantities from this result as if they are verified facts. "
            "Use only for general background and context."
        ),
    },
    "fetch_url_content": {
        "reliability": SourceReliability.INFORMATIONAL,
        "good_for": ["reading web pages", "article text", "documentation"],
        "not_good_for": ["live prices", "real-time data", "dynamic content"],
        "epistemic_note": (
            "Returns static page text. Dynamic content (prices, availability, "
            "live stats) may be missing, stale, or inaccurate. Do NOT treat "
            "scraped numbers as verified data."
        ),
    },
    # Browser — can see rendered pages but still scraping, not an API.
    "browser_read_page": {
        "reliability": SourceReliability.INDICATIVE,
        "good_for": ["rendered page content", "JS-heavy sites"],
        "not_good_for": ["verified pricing", "real-time quotes"],
        "epistemic_note": (
            "Reads rendered browser content. Any specific numbers, prices, "
            "or data points are INDICATIVE only — label as approximate and "
            "cite the source URL."
        ),
    },
    # Flight search plugin — purpose-built API, structured fare data.
    "flight_search": {
        "reliability": SourceReliability.VERIFIED,
        "good_for": ["live flight fares", "airline routes", "price comparison"],
        "not_good_for": [],
        "epistemic_note": (
            "Returns structured fare data from Amadeus API. "
            "Prices can be quoted directly with source attribution."
        ),
    },
    "airport_lookup": {
        "reliability": SourceReliability.VERIFIED,
        "good_for": ["IATA codes", "airport identification"],
        "not_good_for": [],
        "epistemic_note": "",
    },
}


def get_tool_capability(tool_name: str) -> dict | None:
    """Return capability metadata for *tool_name*, or None if not catalogued."""
    return TOOL_CAPABILITIES.get(tool_name)


# ── tool classification ──────────────────────────────────────────────

_HIGH: set[str] = {
    # browser state changes / form submission
    "browser_click",
    "browser_fill",
    "browser_request_login",
    "browser_finish_login",
    # automated message scheduling
    "schedule_create",
    "schedule_update",
    "schedule_reminder",
    # system mutation
    "plugin_activate",
    "plugin_write",
    "self_improve_deploy",
}

_MEDIUM: set[str] = {
    # outbound file delivery — user asked for it, don't gate it
    "workspace_send_file",
    # external reads
    "browser_open",
    "browser_read_page",
    "browser_screenshot",
    "browser_find",
    "fetch_url_content",
    "background_search_tool",
    "arxiv_search",
    "arxiv_fetch_papers",
    "rss_check",
    # local state changes
    "schedule_delete",
    "schedule_reload",
    "reminder_delete",
    # sandbox — the container itself is the safety boundary
    "sandbox_execute",
    "sandbox_start",
    "sandbox_message",
    "sandbox_review",
    "sandbox_finish",
    "sandbox_abort",
    # publishable content
    "note_create",
    "note_update",
    "url_to_note",
    "pdf_to_note",
    "arxiv_to_note",
    "course_create",
    "course_update",
    "course_publish",
    # workspace writes
    "project_create",
    "project_add_note",
    "project_add_link",
    "project_add_source",
}

TOOL_RISK_MAP: dict[str, RiskLevel] = {
    name: RiskLevel.HIGH for name in _HIGH
} | {name: RiskLevel.MEDIUM for name in _MEDIUM}


# ── public helpers ───────────────────────────────────────────────────


def get_risk_level(tool_name: str) -> RiskLevel:
    """Return the risk level for *tool_name*, defaulting to MEDIUM.

    IMPORTED plugin tools are automatically elevated to HIGH risk so
    they require user confirmation before execution.
    """
    # Static classification takes precedence.
    static = TOOL_RISK_MAP.get(tool_name)
    if static is not None:
        return static

    # Dynamic: check if the tool belongs to an IMPORTED plugin.
    try:
        from prax.plugins.loader import get_plugin_loader
        from prax.plugins.registry import PluginTrust

        loader = get_plugin_loader()
        tool_map = loader.get_tool_plugin_map()
        rel_path = tool_map.get(tool_name)
        if rel_path:
            tier = loader.registry.get_trust_tier(rel_path)
            if tier == PluginTrust.IMPORTED:
                return RiskLevel.HIGH
    except Exception:
        pass  # Best-effort — don't break risk classification

    return RiskLevel.MEDIUM


def requires_confirmation(tool_name: str) -> bool:
    """Return True when the tool should be gated behind user confirmation."""
    return get_risk_level(tool_name) is RiskLevel.HIGH


_TRUNCATE = 200


def _truncate(text: str | None, limit: int = _TRUNCATE) -> str | None:
    if text is None:
        return None
    return text[:limit] + "..." if len(text) > limit else text


def log_action(
    tool_name: str,
    risk: RiskLevel,
    args: dict,
    result: str | None = None,
) -> dict:
    """Build a structured audit-log entry (does not persist anywhere)."""
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "tool_name": tool_name,
        "risk": risk.value,
        "args": _truncate(str(args)),
        "result": _truncate(result),
    }


# ── decorator ────────────────────────────────────────────────────────


def risk_tool(*, risk: RiskLevel):
    """Decorator that wraps LangChain's ``@tool`` and attaches risk metadata.

    Usage::

        @risk_tool(risk=RiskLevel.HIGH)
        def sandbox_execute(code: str) -> str:
            \"\"\"Execute code in sandbox.\"\"\"
            ...

    The resulting object is a standard LangChain ``StructuredTool`` with an
    extra ``_risk_level`` attribute that ``wrap_with_governance`` reads.
    """
    from langchain_core.tools import tool as _lc_tool

    def decorator(func):
        lc_tool = _lc_tool(func)
        lc_tool._risk_level = risk
        return lc_tool

    return decorator
