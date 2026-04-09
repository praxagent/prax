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
