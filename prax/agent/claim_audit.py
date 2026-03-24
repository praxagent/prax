"""Lightweight deterministic claim-auditor for agent responses.

Scans the agent's final response for specific numeric claims (dollar amounts,
percentages, rankings) and checks whether those values appear verbatim in any
tool result from the current turn.  Flags ungrounded claims in the audit log.

This is NOT an LLM — it is a fast regex pass that catches the exact failure
mode from the PHL→SNA incident (fabricated $83 fare from search snippets).

Wired into the orchestrator after the agent generates its response.
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
