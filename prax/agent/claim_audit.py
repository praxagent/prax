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

# Strip the [SCHEDULED_TASK — ...] / [Reminder ...] system preamble from the
# task prompt before intent matching.  The preamble itself contains the words
# "news/headlines/briefings" and "weather" inside its *rules section* — without
# stripping, every scheduled task ("say hi") would be misclassified as a
# news/weather request.  Mirrors session_service._SYSTEM_PREFIX_RE.
_SCHEDULED_PREFIX_RE = re.compile(
    r"^\[(?:SCHEDULED_TASK|Reminder)[^\]]*\]\s*",
    re.IGNORECASE,
)


def _strip_scheduled_preamble(task_prompt: str) -> str:
    return _SCHEDULED_PREFIX_RE.sub("", task_prompt or "")


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

# Hard tool/spoke FAILURE markers — a crash/exception, not a soft "couldn't
# fetch".  Their presence in a tool result means something BROKE this turn.
_HARD_FAILURE_MARKERS = (
    "spoke agent failed",
    "object has no attribute",
    "object is not subscriptable",
    "traceback (most recent call",
    "unhandled exception",
    "internal error",
    "no response and no error",
)

# System-error acknowledgment vocabulary — if the RESPONSE uses any of these it
# has owned up to a *technical failure*.  Deliberately distinct from a content
# caveat like "could not verify the login-gated page": that discloses a source
# limitation, NOT that a spoke crashed.
_CRASH_ACK_MARKERS = (
    "an error occurred", "ran into an error", "hit an error", "encountered an error",
    "returned an error", "an exception", "threw an exception", "raised an exception",
    "a failure", "failed to", "failed while", "was unable to complete",
    "went wrong", "something went wrong", "didn't work", "did not work",
    "crashed", "ran into a problem", "hit a snag", "a bug", "it broke",
    "technical issue", "technical problem", "internal error",
)

_WEATHER_UNAVAILABLE_MARKERS = (
    "weather unavailable",
    "couldn't fetch weather",
    "could not fetch weather",
    "no weather tool",
    "weather tool is not configured",
    "weather not available",
)

# Regex covering the natural-language ways a response can disclose that
# weather couldn't be fetched.  The literal-substring list above misses
# common phrasings ("Weather is unavailable", "Weather data unavailable",
# "couldn't fetch fresh ... weather data"); this regex keeps the audit
# from blocking briefings that DID legitimately disclose unavailability.
_WEATHER_UNAVAILABLE_RE = re.compile(
    r"\bweather\b[\w\s,:'\-]{0,40}\b("
    r"unavailable|not available|no data|cannot|can't|couldn't|could not|"
    r"not configured|no location|holding"
    r")\b"
    r"|\b(?:no|cannot|can't|couldn't|could not|unable to)\b[\w\s,:'\-]{0,60}\bweather\b",
    re.IGNORECASE,
)

# Structured signal from delegate_environment: when the spoke can't resolve
# a concrete city/region it returns LOCATION_UNCERTAIN.  That IS the correct
# weather-unavailability disclosure for the audit — the spoke did its job
# and reported "no live source confirmable", which the system prompt
# explicitly tells the orchestrator to treat as "weather unavailable".
_LOCATION_UNCERTAIN_MARKER = "LOCATION_UNCERTAIN"


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


def _weather_unavailable_disclosed(response: str, messages: list) -> bool:
    """Return True if weather unavailability was disclosed.

    Checks three signals, any of which is sufficient:

    1. Literal-substring markers in the response (legacy path).
    2. Natural-language regex match in the response — catches common
       phrasings like "Weather is unavailable today", "Weather data
       unavailable", "couldn't fetch fresh news or weather data".
    3. ``LOCATION_UNCERTAIN`` evidence from a sub-agent tool message
       (typically ``delegate_environment``) — the structured signal the
       environment spoke emits when it can't resolve a concrete location,
       which the system prompt explicitly treats as "weather unavailable".
    """
    lowered = (response or "").lower()
    if any(marker in lowered for marker in _WEATHER_UNAVAILABLE_MARKERS):
        return True
    if _WEATHER_UNAVAILABLE_RE.search(response or ""):
        return True
    try:
        from langchain_core.messages import ToolMessage
    except Exception:
        return False
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        if _LOCATION_UNCERTAIN_MARKER in str(msg.content or ""):
            return True
    return False


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
    user_task = _strip_scheduled_preamble(task_prompt)
    combined = f"{user_task}\n{response}"
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

    if _matches_any(user_task, _SCHEDULED_WEATHER_INTENT_PATTERNS):
        weather_tools = _successful_weather_tool_names(messages)
        disclosed_unavailable = _weather_unavailable_disclosed(response, messages)
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


def decide_scheduled_briefing_action(
    narrative: dict | None,
    scheduled_grounding: dict | None,
) -> str:
    """Classify how to handle audit flags on a scheduled briefing.

    Returns one of:
      - ``"pass"``: deliver the response as-is.
      - ``"weather_disclaimer"``: deliver, but append a weather-unavailable
        disclosure (news content is verified; only weather is missing).
      - ``"suppress"``: replace with the safe fallback (substantive content
        is unverified — fabricated news risk).

    Why: an earlier policy suppressed the entire briefing on *any* scheduled
    grounding flag, which threw away verified news whenever weather couldn't
    be resolved. Verified news + a "weather unavailable" line is strictly
    more useful than a blanket "I'm holding this one".
    """
    if narrative:
        return "suppress"
    if not scheduled_grounding:
        return "pass"
    reqs = [r.lower() for r in scheduled_grounding.get("requirements") or []]
    if not reqs:
        return "pass"
    news_missing = any("news" in r or "briefing" in r for r in reqs)
    if news_missing:
        return "suppress"
    if all("weather" in r for r in reqs):
        return "weather_disclaimer"
    return "suppress"


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
# Two tiers: strong markers fire the audit on their own; offer markers only
# count when accompanied by a strong marker.  A reply that says "Done — if
# you want, I can also turn this into a tech-focused brief" is an extension
# offer on top of completed work, not a partial-work caveat.  Treating bare
# offer language as a caveat caused every successful research/news delegation
# to false-flag the orchestrator's "Done" response.
_TOOL_CAVEAT_MARKERS: list[str] = [
    "one caveat",
    "however,",
    "but it does not guarantee",
    "does not guarantee",
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

# Soft offer markers — "want me to do more?" style.  These are caveats only
# when paired with a strong marker above.  Standalone they're extension
# offers from a successfully completed delegation.
_TOOL_OFFER_MARKERS: list[str] = [
    "if you want, i can",
    "if you'd like, i can",
    "if you want me to",
    "do you want me to",
    "want me to",
    "should i",
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


def audit_tool_failures(response: str, tool_results: list[str]) -> dict | None:
    """Flag a HARD tool/spoke failure this turn that the response hides.

    The honesty gap: a spoke crashed (``"Spoke agent failed: ..."`` / an
    exception) yet the orchestrator — often a weak model — reported success and
    never told the user.  Deterministic: if any tool result carries a
    hard-failure/crash marker AND the response uses NO system-error
    acknowledgment vocabulary, return the failures so they get surfaced.  A
    content caveat like "could not verify" does NOT count as owning up to a
    crash, so a response that discloses a *source* limitation while silently
    swallowing a *spoke crash* is still flagged.
    """
    if not response:
        return None
    low = response.lower()
    if any(m in low for m in _CRASH_ACK_MARKERS):
        return None  # the response already discloses a technical failure
    failures = [
        (r or "")[:200]
        for r in (tool_results or [])
        if any(m in (r or "").lower() for m in _HARD_FAILURE_MARKERS)
    ]
    return {"failures": failures} if failures else None


# Only audit URLs that look like Prax-GENERATED artifact links — the ones the
# agent creates and could invent or get wrong. External citations are audited
# separately, against what the turn read: audit_unread_citations below.
_URL_RE = re.compile(r'https?://[^\s)\]}<>"\']+', re.IGNORECASE)
_ARTIFACT_URL_HINTS = (
    "/notes/", "/note/", "/courses/", "/course/", "/library/",
    "/spaces/", "/space/", "/outputs/", ".ts.net/", "ngrok",
)


def audit_fabricated_links(response: str, tool_results: list[str]) -> dict | None:
    """Flag a Prax-artifact URL asserted in the response that no tool produced.

    A "saved at https://.../notes/…" claim must be GROUNDED in a tool output — a
    publish/create tool returned that URL. An artifact-looking link present only in
    the response is a candidate fabrication (the model invented where it saved
    something). Deterministic; external citations are excluded via the artifact
    filter. NOTE: a link a tool *did* return but that fails to RESOLVE (a 404) needs
    a live HTTP check — tracked separately, this catches invented links only.
    """
    if not response:
        return None
    joined = " ".join(tool_results or [])
    fabricated: list[str] = []
    for raw in _URL_RE.findall(response):
        url = raw.rstrip(".,);:'\"")
        low = url.lower()
        if not any(h in low for h in _ARTIFACT_URL_HINTS):
            continue  # not a Prax artifact link — out of scope
        if url in joined or url.rstrip("/") in joined:
            continue  # grounded in a tool result
        fabricated.append(url)
    return {"urls": sorted(set(fabricated))} if fabricated else None


# --- Read-before-cite (docs/research/ideascientist.md) ----------------------
#
# An external URL in a reply is presented as a source. Where did it come from?
#   read      — a tool was asked to act on it this turn (fetch, navigate, …),
#   seen      — a tool returned it, or the user / the turn's context contained it,
#   repeated  — an earlier reply gave it (that reply was audited in its turn),
#   unread    — none of these: the model wrote it from memory.
# Only "unread" is flagged. It is not proof of a wrong link — a well-known URL
# from memory is often right — but nothing this turn read it.

_CODE_SPAN_RE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
_PLACEHOLDER_HOSTS = ("example.com", "example.org", "example.net")
_PLACEHOLDER_SUFFIXES = (".example", ".test", ".invalid", ".localhost", ".local",
                         ".internal", ".lan")


def _norm_url(url: str) -> tuple[str, str]:
    """(host+path+query, host+path) for *url*, scheme- and ``www.``-insensitive,
    fragment dropped, trailing slash and sentence punctuation trimmed."""
    from urllib.parse import urlsplit
    url = url.rstrip(".,);:'\"!?*_>")
    try:
        p = urlsplit(url)
    except ValueError:
        return url.lower(), url.lower()
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/")
    base = f"{host}{path}"
    return (f"{base}?{p.query}" if p.query else base), base


def _is_citable(url: str) -> bool:
    """External, public, and not a Prax artifact (audit_fabricated_links owns
    those) or a placeholder/local address."""
    import ipaddress
    from urllib.parse import urlsplit
    low = url.lower()
    if any(h in low for h in _ARTIFACT_URL_HINTS):
        return False
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    if not host or "." not in host:
        return False  # localhost, bare names
    if host in _PLACEHOLDER_HOSTS or any(host.endswith("." + h) for h in _PLACEHOLDER_HOSTS):
        return False
    if host.endswith(_PLACEHOLDER_SUFFIXES):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True  # a domain name


def _urls_in(text: str) -> set[tuple[str, str]]:
    # JSON-encoded tool output escapes slashes ("https:\/\/…").
    return {_norm_url(u) for u in _URL_RE.findall((text or "").replace("\\/", "/"))}


def _tool_call_text(msg) -> str:
    import json
    parts = []
    for call in getattr(msg, "tool_calls", None) or []:
        args = call.get("args") if isinstance(call, dict) else getattr(call, "args", None)
        try:
            parts.append(json.dumps(args, ensure_ascii=False, default=str))
        except Exception:
            parts.append(str(args))
    return " ".join(parts)


def _message_text(msg) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, list):  # Responses-API content blocks
        return " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content or "")


_CREDENTIAL_QUERY_KEY = re.compile(
    r"key|token|secret|passw|signature|^sig$|auth|credential|session|^x-amz-",
    re.IGNORECASE)


def _has_credential_query(url: str) -> bool:
    from urllib.parse import parse_qsl, urlsplit
    try:
        query = urlsplit(url).query
    except ValueError:
        return True
    return any(_CREDENTIAL_QUERY_KEY.search(k) for k, _ in parse_qsl(query, keep_blank_values=True))


def fetched_urls(messages: list) -> list[str]:
    """The citable URLs *messages*' tool calls asked a tool to act on — what an
    agent fetched or opened — in first-seen order, one per normalised address.

    For a spoke's footer ("sources read"), so the orchestrator receives what
    the spoke read even when its summary drops the link. URLs with a
    credential-looking query parameter (``api_key=``, ``token=``, a signed S3
    link) are left out: they are not citations, and the footer travels to the
    orchestrator, the trace and TeamWork.
    """
    try:
        from langchain_core.messages import AIMessage
    except Exception:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        for raw in _URL_RE.findall(_tool_call_text(msg).replace("\\/", "/")):
            url = raw.rstrip(".,);:'\"!?*_>\\")
            if not _is_citable(url) or _has_credential_query(url):
                continue
            key = _norm_url(url)[0]
            if key not in seen:
                seen.add(key)
                out.append(url)
    return out


def audit_unread_citations(response: str, messages: list) -> dict | None:
    """Flag external URLs the reply cites that nothing this turn read or saw.

    *messages* is the turn's full list (system prompt, history, the user's
    message, tool calls and results, the final reply). The model's own words
    — assistant message text — never count as grounding, except an EARLIER
    reply's (``repeated``), so a follow-up that repeats a source the last turn
    fetched is not flagged. URLs inside code are examples, not citations, and
    are skipped. Returns ``{"unread": [...], "read": n, "seen": n,
    "repeated": n}`` when at least one citation is unread, else None.
    """
    if not response:
        return None
    cited: dict[tuple[str, str], str] = {}
    for raw in _URL_RE.findall(_CODE_SPAN_RE.sub(" ", response)):
        url = raw.rstrip(".,);:'\"!?*_>")
        if _is_citable(url):
            cited.setdefault(_norm_url(url), url)
    if not cited:
        return None
    try:
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    except Exception:
        return None
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)),
                     default=-1)
    read, seen, repeated = set(), set(), set()
    for i, msg in enumerate(messages):
        if isinstance(msg, AIMessage):
            read |= _urls_in(_tool_call_text(msg))
            if i < last_human:
                repeated |= _urls_in(_message_text(msg))
        elif isinstance(msg, ToolMessage):
            seen |= _urls_in(_message_text(msg))
        else:  # system prompt, the user's messages
            seen |= _urls_in(_message_text(msg))

    def _in(keys: set, norm: tuple[str, str]) -> bool:
        full, base = norm
        return any(full == k[0] or base == k[1] for k in keys)

    tiers = {"read": 0, "seen": 0, "repeated": 0}
    unread: list[str] = []
    for norm, url in cited.items():
        if _in(read, norm):
            tiers["read"] += 1
        elif _in(seen, norm):
            tiers["seen"] += 1
        elif _in(repeated, norm):
            tiers["repeated"] += 1
        else:
            unread.append(url)
    return {"unread": sorted(unread), **tiers} if unread else None


# Phrases that promise the user a retrievable thing *in this reply*.
_DELIVERY_PROMISE_PATTERNS: list[re.Pattern] = [
    re.compile(r"here(?:'s| is)\s+(?:the|your)\s+(?:link|file|download|mp3|pdf|audio|video|image|attachment)\b", re.IGNORECASE),
    re.compile(r"^\s*(?:the\s+)?link\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\byou can (?:download|grab|get) it (?:here|at)\b", re.IGNORECASE),
    re.compile(r"\b(?:i(?:'ve| have))?\s*attached\b", re.IGNORECASE),
    re.compile(r"\bsending (?:it|the file) (?:now|over)\b", re.IGNORECASE),
]

# Tools whose success means a file actually reached the user.
_DELIVERY_TOOLS = frozenset({
    "workspace_send_file", "send_file", "workspace_share_link",
})


def audit_undelivered_artifact(response: str, messages: list) -> dict | None:
    """Flag a reply that PROMISES a link/file but delivers neither.

    The live regression: asked "Link please?", the agent replied
    "Here's the link: ``polydao_narration.mp3``" — a bare filename in
    backticks. No URL, no upload, nothing the user could click or open.
    ``audit_fabricated_links`` cannot catch this because it only fires when a
    URL is *present*; ``audit_artifact_location`` passed because
    ``artifact_locator`` had in fact been called. The failure lives exactly in
    the gap between them.

    Deterministic and conservative: it fires only when the reply makes an
    explicit delivery promise AND carries no URL AND no delivery tool
    succeeded this turn. A reply that merely *mentions* a path, or that says
    "the file is at …" without promising a link, is not flagged.
    """
    if not response:
        return None
    promises = [p.pattern for p in _DELIVERY_PROMISE_PATTERNS if p.search(response)]
    if not promises:
        return None
    if _URL_RE.search(response):
        return None  # a link is present — fabrication is the other check's job

    try:
        from langchain_core.messages import ToolMessage
    except Exception:
        return None

    for msg in messages or []:
        if not isinstance(msg, ToolMessage):
            continue
        if str(getattr(msg, "name", "") or "") not in _DELIVERY_TOOLS:
            continue
        content = str(getattr(msg, "content", "") or "")
        # workspace_send_file reports "Sent X to the user via ..." on success
        # and "... not found in workspace" on failure. Only success counts.
        if "not found" in content.lower():
            continue
        if "sent " in content.lower() or "delivered" in content.lower():
            return None

    return {"promises": promises, "delivered": False}


# A reply telling the user something is on their screen right now — in their
# terminal, on the desktop, in a window. Live regression (2026-10-04): in the
# Desktop tab Prax ran `echo` with sandbox_shell, which runs in the background
# there, and said "You should see that message pop up in your terminal right
# now". Nothing appeared; the user saw the claim was false.
_ON_SCREEN_CLAIM_PATTERNS: list[re.Pattern] = [
    re.compile(r"\byou(?:'ll| will| should| can)?(?: now)? see\b[^.!?\n]{0,80}"
               r"\b(?:terminal|screen|desktop|window)\b", re.IGNORECASE),
    re.compile(r"\b(?:pop(?:ped|s)? up|appear(?:ed|s|ing)?|show(?:n|s|ing|ed)? up)\b[^.!?\n]{0,60}"
               r"\b(?:in|on) (?:your|the) (?:terminal|screen|desktop|window)\b", re.IGNORECASE),
    re.compile(r"\btyp(?:ed|ing) (?:it |that |this |something )?(?:directly |right )?"
               r"(?:in|into) (?:your|the) terminal\b", re.IGNORECASE),
]

# Tools that put something in front of the user on the sandbox desktop.
_ON_SCREEN_TOOLS = frozenset({
    "desktop_type", "desktop_key", "desktop_click", "desktop_open",
    "desktop_screenshot", "delegate_desktop",
})


def audit_unseen_on_screen(response: str, messages: list, active_view: str = "") -> dict | None:
    """Flag a reply that says something is visible on the user's screen when
    no tool put anything there this turn.

    Visible: a desktop tool that succeeded, or sandbox_shell while the user is
    on TeamWork's Terminal tab (it runs in their shared terminal there). In
    every other view sandbox_shell runs in the background, so it never counts.
    Deterministic and conservative: an on-screen claim must be explicit.
    """
    if not response:
        return None
    phrases = [m.group(0) for p in _ON_SCREEN_CLAIM_PATTERNS for m in [p.search(response)] if m]
    if not phrases:
        return None
    try:
        from langchain_core.messages import ToolMessage
    except Exception:
        return None
    for msg in messages or []:
        if not isinstance(msg, ToolMessage):
            continue
        name = str(getattr(msg, "name", "") or "")
        content = str(getattr(msg, "content", "") or "")
        failed = content.lower().startswith(("no desktop", "screenshot failed", "type failed",
                                            "key press failed", "click failed", "launch failed",
                                            "no window matches"))
        if name in _ON_SCREEN_TOOLS and not failed:
            return None
        if name == "sandbox_shell" and active_view == "terminal":
            return None
    return {"phrases": phrases}


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
