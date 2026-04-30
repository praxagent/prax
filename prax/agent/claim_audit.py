"""Lightweight deterministic claim-auditor for agent responses.

Two layers of checks run against the final response:

1. **Numeric claim audit** — scans for specific numeric claims (dollar amounts,
   percentages, rankings) and verifies they appear verbatim in tool results.
   Catches the PHL→SNA incident (fabricated $83 fare from search snippets).

2. **Narrative grounding audit** — detects when the response asserts external
   world content (news, weather, headlines, market data) but no tool in the
   research/web/news/browser family was actually called this turn.  Catches
   the "hallucinated daily briefing" failure mode where context compaction
   drops history and the model pads from memory instead of fetching.

Neither check uses an LLM — both are fast regex passes.  For scheduled tasks
the orchestrator promotes narrative-grounding flags to a BLOCKING error so
the user never receives a fabricated notification while they're away.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Patterns that indicate a specific factual claim worth auditing.
# Each pattern captures the full match for comparison against tool results.
_CLAIM_PATTERNS: list[re.Pattern] = [
    # Dollar amounts: $83, $1,234.56, $83 million
    re.compile(r"\$[\d,]+(?:\.\d{1,2})?(?:\s*(?:million|billion|trillion|k|M|B))?"),
    # Euro/pound amounts: €50, £100
    re.compile(r"[€£][\d,]+(?:\.\d{1,2})?"),
    # Percentages: 42%, 3.5%
    re.compile(r"\d+(?:\.\d+)?%"),
    # Rankings: #1, ranked #3, No. 5
    re.compile(r"(?:#\d+|(?:No\.|ranked? ?#?)\s*\d+)", re.IGNORECASE),
]

# Tool result tags that indicate the source is informational (not to be quoted).
_INFORMATIONAL_MARKER = "[INFORMATIONAL SOURCE"


def audit_claims(
    response: str,
    tool_results: list[str],
) -> list[dict]:
    """Check response claims against tool evidence.

    Returns a list of audit entries for ungrounded claims.  Each entry has:
    - ``claim``: the specific value found in the response
    - ``grounded``: whether the value was found in any tool result
    - ``informational_only``: whether the only matching tool result was
      tagged INFORMATIONAL (meaning the value shouldn't be quoted)
    """
    if not response:
        return []
    # No tool results means pure conversation — the agent isn't using tools,
    # so claim auditing against tool evidence doesn't apply.
    if not tool_results:
        return []

    # Extract all specific claims from the response.
    claims: list[str] = []
    for pattern in _CLAIM_PATTERNS:
        claims.extend(pattern.findall(response))

    if not claims:
        return []

    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique_claims: list[str] = []
    for c in claims:
        if c not in seen:
            seen.add(c)
            unique_claims.append(c)

    # Check each claim against tool results.
    findings: list[dict] = []
    for claim in unique_claims:
        # Strip the tag prefix from tool results for matching purposes —
        # we want to know if the UNDERLYING result contains this value.
        grounded = False
        informational_only = True

        for tr in tool_results:
            if claim in tr:
                grounded = True
                if _INFORMATIONAL_MARKER not in tr:
                    informational_only = False

        if not grounded or informational_only:
            finding = {
                "claim": claim,
                "grounded": grounded,
                "informational_only": grounded and informational_only,
            }
            findings.append(finding)
            if not grounded:
                logger.warning(
                    "UNGROUNDED CLAIM in response: '%s' not found in any tool result",
                    claim,
                )
            elif informational_only:
                logger.warning(
                    "INFORMATIONAL-SOURCED CLAIM in response: '%s' found only in "
                    "INFORMATIONAL tool results (should not be quoted as fact)",
                    claim,
                )

    return findings


# ---------------------------------------------------------------------------
# Narrative grounding audit — detects hallucinated "news/weather" content
# ---------------------------------------------------------------------------

# Phrases in the response that indicate the agent is claiming to report
# external world content it cannot know without fetching.
_NARRATIVE_CLAIM_PATTERNS: list[re.Pattern] = [
    re.compile(r"\btop news\b", re.IGNORECASE),
    re.compile(r"\bnews item(?:s)?\b", re.IGNORECASE),
    re.compile(r"\bheadline(?:s)?\b", re.IGNORECASE),
    re.compile(r"\btoday[''']s (?:highlight|top|key|news|briefing)", re.IGNORECASE),
    re.compile(r"\bweather\b.*\b(?:forecast|today|tomorrow|tonight|sunny|rainy|cloudy|degrees|high|low)\b", re.IGNORECASE),
    re.compile(r"\b(?:stock|market|index|price) (?:is|was|closed|opened|rose|fell|jumped|dropped)\b", re.IGNORECASE),
    re.compile(r"\breport(?:s|edly)? (?:say|said|shows|showed|announced|released)\b", re.IGNORECASE),
    re.compile(r"\b(?:breaking|latest) news\b", re.IGNORECASE),
    re.compile(r"\bmorning briefing\b", re.IGNORECASE),
]

# Tool names (or name prefixes) that actually fetch external world content.
# If the response contains narrative claims but NONE of these was called,
# the claims are ungrounded.
_GROUNDING_TOOL_NAMES: set[str] = {
    "news",                    # news plugin
    "background_search_tool",  # web search
    "search_tool",
    "web_search",
    "web_summary_tool",
    "fetch_url",
    "url_fetch",
    "arxiv_search",
    "arxiv_fetch_papers",
    "delegate_research",       # research spoke
    "delegate_browser",        # browser spoke (real-time fetch)
    "browser_navigate",
    "browser_read",
    "weather",
    "get_weather",
}

# Scheduled tasks run while the user is absent.  Generic search snippets are
# too weak for this path: they can be irrelevant, stale, or explicitly tagged
# as informational.  A morning briefing must use the news pipeline or an
# equivalent stronger fetch path, not just background_search_tool.
_SCHEDULED_NEWS_INTENT_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(?:morning|daily|today'?s)?\s*briefing\b", re.IGNORECASE),
    re.compile(r"\btop\s+\d*\s*news\b", re.IGNORECASE),
    re.compile(r"\bheadline(?:s)?\b", re.IGNORECASE),
    re.compile(r"\bnews item(?:s)?\b", re.IGNORECASE),
]

_SCHEDULED_WEATHER_INTENT_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bweather\b", re.IGNORECASE),
    re.compile(r"\bforecast\b", re.IGNORECASE),
]

_SCHEDULED_NEWS_GROUNDING_TOOLS: set[str] = {
    "news",
    "delegate_research",
    "delegate_browser",
    "web_summary_tool",
    "fetch_url_content",
}

_WEATHER_GROUNDING_TOOLS: set[str] = {
    "weather",
    "get_weather",
    "weather_lookup",
}

_WEATHER_FETCH_EVIDENCE_TOOLS: set[str] = {
    "delegate_environment",
    "delegate_research",
    "delegate_browser",
    "fetch_url_content",
    "web_summary_tool",
    "sandbox_shell",
}

_WEATHER_SOURCE_MARKERS = (
    "weather.gov",
    "api.weather.gov",
    "forecast.weather.gov",
    "open-meteo",
    "api.open-meteo.com",
    "national weather service",
)

_WEATHER_EVIDENCE_MARKERS = (
    "forecast",
    "temperature",
    "degrees",
    "precipitation",
    "rain",
    "snow",
    "wind",
    "humidity",
)

_FAILED_TOOL_MARKERS = (
    "error",
    "could not",
    "couldn't",
    "unable to",
    "timed out",
    "timeout",
    "no news sources configured",
    "no sources configured",
    "reader returned http",
    "err_name_not_resolved",
)

_WEATHER_UNAVAILABLE_MARKERS = (
    "weather unavailable",
    "couldn't fetch weather",
    "could not fetch weather",
    "no weather tool",
    "weather tool is not configured",
    "weather not available",
)


def _extract_tool_names(messages: list) -> set[str]:
    """Return the set of tool names actually invoked this turn.

    Walks both AIMessage.tool_calls (the calls made) and ToolMessage.name
    (the results returned).  A tool is "called" only if its result came back —
    failed validation calls leave no ToolMessage and must not count as
    grounding evidence.
    """
    called: set[str] = set()
    try:
        from langchain_core.messages import ToolMessage
    except Exception:
        return called
    for msg in messages:
        if isinstance(msg, ToolMessage):
            name = getattr(msg, "name", None)
            if name:
                called.add(str(name))
    return called


def _successful_tool_names(messages: list) -> set[str]:
    """Return ToolMessage names whose result does not look like a failure."""
    called: set[str] = set()
    try:
        from langchain_core.messages import ToolMessage
    except Exception:
        return called
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        name = getattr(msg, "name", None)
        if not name:
            continue
        content = (msg.content or "").lower()
        if any(marker in content for marker in _FAILED_TOOL_MARKERS):
            continue
        called.add(str(name))
    return called


def _successful_weather_tool_names(messages: list) -> set[str]:
    """Return tools that produced weather-specific live evidence.

    A capable harness can get weather without a dedicated plugin by using
    research/browser/sandbox against an authoritative source.  Generic search
    snippets still do not count.
    """
    called: set[str] = set()
    try:
        from langchain_core.messages import ToolMessage
    except Exception:
        return called
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        name = str(getattr(msg, "name", "") or "")
        if not name:
            continue
        content = (msg.content or "").lower()
        if any(marker in content for marker in _FAILED_TOOL_MARKERS):
            continue
        if name in _WEATHER_GROUNDING_TOOLS:
            called.add(name)
            continue
        has_source = any(marker in content for marker in _WEATHER_SOURCE_MARKERS)
        has_weather_data = any(marker in content for marker in _WEATHER_EVIDENCE_MARKERS)
        if name in _WEATHER_FETCH_EVIDENCE_TOOLS and has_source and has_weather_data:
            called.add(name)
    return called


def _matches_any(text: str, patterns: list[re.Pattern]) -> bool:
    return any(pattern.search(text or "") for pattern in patterns)


def audit_scheduled_task_grounding(
    task_prompt: str,
    response: str,
    messages: list,
) -> dict | None:
    """Enforce minimum evidence for unattended scheduled tasks.

    The ordinary narrative audit checks whether *some* grounding tool ran.
    For scheduled briefings that is not enough: a search snippet about the
    wrong topic should never clear a "3 top news items" notification.  This
    audit applies an intent-specific floor against the scheduled task prompt.
    """
    combined = f"{task_prompt}\n{response}"
    findings: list[str] = []
    successful_tools = _successful_tool_names(messages)

    if _matches_any(combined, _SCHEDULED_NEWS_INTENT_PATTERNS):
        strong_news_tools = successful_tools & _SCHEDULED_NEWS_GROUNDING_TOOLS
        if not strong_news_tools:
            findings.append(
                "news/briefing requested but no successful news, research, "
                "browser, summary, or URL-fetch tool result was available; "
                "background_search_tool alone is not sufficient"
            )

    if _matches_any(task_prompt, _SCHEDULED_WEATHER_INTENT_PATTERNS):
        weather_tools = _successful_weather_tool_names(messages)
        disclosed_unavailable = any(
            marker in (response or "").lower()
            for marker in _WEATHER_UNAVAILABLE_MARKERS
        )
        if not weather_tools and not disclosed_unavailable:
            findings.append(
                "weather requested but no successful weather tool, "
                "authoritative weather fetch, or explicit weather-unavailable "
                "disclosure was present"
            )

    if not findings:
        return None

    return {
        "missing_grounding": True,
        "called_tools": sorted(_extract_tool_names(messages)),
        "successful_tools": sorted(successful_tools),
        "requirements": findings,
    }


def audit_narrative_grounding(
    response: str,
    messages: list,
) -> dict | None:
    """Check whether narrative claims in the response are backed by tool calls.

    Returns ``None`` if no narrative claims are detected OR if the response
    is grounded by at least one research/web/news/browser tool result.
    Returns a finding dict otherwise:

    - ``phrases``: list of narrative phrases found in the response
    - ``called_tools``: list of tool names actually invoked this turn
    - ``missing_grounding``: always True when this function returns non-None
    """
    if not response:
        return None

    matched_phrases: list[str] = []
    for pattern in _NARRATIVE_CLAIM_PATTERNS:
        matches = pattern.findall(response)
        if matches:
            # findall returns tuples if the pattern has groups; flatten.
            for m in matches:
                matched_phrases.append(m if isinstance(m, str) else " ".join(m))

    if not matched_phrases:
        return None

    called_tools = _extract_tool_names(messages)
    grounding_calls = called_tools & _GROUNDING_TOOL_NAMES
    if grounding_calls:
        return None  # At least one real fetch happened — claims have evidence

    return {
        "phrases": sorted(set(matched_phrases))[:6],
        "called_tools": sorted(called_tools),
        "missing_grounding": True,
    }


# ---------------------------------------------------------------------------
# Artifact-location audit — detects "where is it?" answers that search the
# wrong surfaces or answer from plan state instead of artifact evidence.
# ---------------------------------------------------------------------------

_ARTIFACT_LOCATION_INTENT_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"\bwhere\s+(?:is|are|was|were)\b.{0,80}\b"
        r"(?:it|file|link|url|note|artifact|thing|package|result|output)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"\bwhere\s+did\s+you\s+(?:put|save|store|create|publish)\b", re.IGNORECASE),
    re.compile(r"\b(?:link|url)\s+(?:again|please|for|to)\b", re.IGNORECASE),
    re.compile(r"\bshow\s+me\s+(?:the\s+)?(?:file|link|url|artifact|thing|note|package)\b", re.IGNORECASE),
]


def audit_artifact_location(
    task_prompt: str,
    response: str,
    messages: list,
) -> dict | None:
    """Require artifact-location follow-ups to use artifact evidence.

    The concrete regression: the user asked "Where is it?", the agent
    searched only generic workspace filenames, missed recent note URLs in
    trace/tool output, and the claim auditor passed.  This check keeps that
    from being silent.  A passing turn either calls ``artifact_locator``
    directly or delegates to the workspace spoke and gets the locator's
    recognizable "Most likely recent artifact locations" result back.
    """
    if not _matches_any(task_prompt or "", _ARTIFACT_LOCATION_INTENT_PATTERNS):
        return None

    try:
        from langchain_core.messages import ToolMessage
    except Exception:
        return None

    called_tools = _extract_tool_names(messages)
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        name = str(getattr(msg, "name", "") or "")
        content = str(getattr(msg, "content", "") or "")
        if name == "artifact_locator":
            return None
        if name == "delegate_workspace" and "Most likely recent artifact locations" in content:
            return None

    return {
        "missing_grounding": True,
        "called_tools": sorted(called_tools),
        "intent": "artifact-location",
    }


# ---------------------------------------------------------------------------
# Plan-completion alignment audit — detects "Done" lies when the plan had
# caveated sub-agent responses that never resulted in explicit step_done calls.
# ---------------------------------------------------------------------------

# Phrases in the final response that claim the work is complete.
_COMPLETION_CLAIM_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bdone\b[—\-:.,]", re.IGNORECASE),
    re.compile(r"^done\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\b(?:i(?:'ve| have)|all)\s+(?:created|finished|completed|shipped|done)\b", re.IGNORECASE),
    re.compile(r"\b(?:finished|completed|shipped)\s+(?:the|your)\b", re.IGNORECASE),
    re.compile(r"\bhere(?:'s| is) (?:the|your) (?:note|report|summary|result)\b", re.IGNORECASE),
]

# Phrases in delegation tool results that signal partial / caveated work.
# Mirror of ConversationAgent._CAVEAT_MARKERS — kept separate so the
# auditor can run against old traces without importing orchestrator.
_TOOL_CAVEAT_MARKERS: list[str] = [
    "one caveat",
    "however,",
    "but it does not guarantee",
    "does not guarantee",
    "if you want, i can",
    "if you'd like, i can",
    "if you want me to",
    "do you want me to",
    "want me to",
    "should i",
    "partial",
    "could not",
    "couldn't",
    "unable to",
    "not fully",
    "not complete",
    "does not include",
    "doesn't include",
    "placeholder",
    "skipped",
    "didn't actually",
    "did not actually",
]


def audit_plan_completion(
    response: str,
    messages: list,
) -> dict | None:
    """Check for "Done" claims that are contradicted by sub-agent caveats.

    Returns a finding dict if the response claims completion but a
    delegation tool's reply this turn contained a caveat marker that
    the main agent appears to have ignored.  Returns ``None`` otherwise.

    Catches the failure mode where the orchestrator's
    ``_auto_complete_plan_steps`` marks every step done after a delegation
    returned "successfully", the main agent then says "Done — here's the
    note", and the sub-agent's response actually said "I archived it but
    it does not guarantee the synthesized summary format you asked for".
    """
    if not response:
        return None

    completion_claim = None
    for pattern in _COMPLETION_CLAIM_PATTERNS:
        m = pattern.search(response)
        if m:
            completion_claim = m.group(0)
            break
    if not completion_claim:
        return None

    try:
        from langchain_core.messages import ToolMessage
    except Exception:
        return None

    # Walk delegation tool results looking for caveats that contradict the
    # completion claim.  Only consider delegation-style tools — the kind
    # the orchestrator's auto-completer uses as proof of "work done".
    delegation_names = {
        "delegate_task", "delegate_parallel", "delegate_research",
        "delegate_browser", "delegate_sandbox", "delegate_sysadmin",
        "delegate_finetune", "delegate_content_editor", "delegate_knowledge",
        "delegate_memory", "delegate_workspace", "delegate_course",
        "delegate_scheduler",
    }
    hit_marker: str | None = None
    hit_tool: str | None = None
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        name = getattr(msg, "name", None) or ""
        if name not in delegation_names:
            continue
        content = (msg.content or "").lower()
        for marker in _TOOL_CAVEAT_MARKERS:
            if marker in content:
                hit_marker = marker
                hit_tool = name
                break
        if hit_marker:
            break

    if not hit_marker:
        return None

    return {
        "completion_claim": completion_claim.strip(),
        "caveat_marker": hit_marker,
        "caveat_tool": hit_tool,
        "missing_grounding": True,
    }


def format_audit_warning(findings: list[dict]) -> str:
    """Format ungrounded-claim findings into a human-readable audit note."""
    if not findings:
        return ""

    ungrounded = [f for f in findings if not f["grounded"]]
    info_sourced = [f for f in findings if f["informational_only"]]

    parts: list[str] = []
    if ungrounded:
        values = ", ".join(f["claim"] for f in ungrounded)
        parts.append(
            f"UNGROUNDED: {values} — not found in any tool result"
        )
    if info_sourced:
        values = ", ".join(f["claim"] for f in info_sourced)
        parts.append(
            f"INFORMATIONAL-SOURCED: {values} — found only in general web content"
        )
    return "; ".join(parts)
