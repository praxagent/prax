"""Note quality review — runs before publishing to catch raw dumps and low-effort content.

Two layers of checks:
1. **Heuristic** — cheap pattern matching for obvious problems (orphan commas,
   ``[Image]`` placeholders, raw MathJax delimiters, absurdly long paragraphs).
2. **LLM-as-judge** — a cheap model reviews for semantic quality: is the content
   synthesized or raw-copied? Does it have structure? Does it match the request?

When a note fails review, the caller returns feedback to the agent, which can
retry up to ``MAX_REVISIONS`` times before the review is bypassed (to avoid
infinite loops on genuinely hard content).
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading

logger = logging.getLogger(__name__)

MAX_REVISIONS = 5

# ---------------------------------------------------------------------------
# Heuristic checks — cheap pattern matching
# ---------------------------------------------------------------------------

# Patterns that strongly indicate a raw HTML/markdown dump rather than
# synthesized content.
_RAW_DUMP_PATTERNS = [
    # Orphan comma on its own line — classic sign of stripped MathJax like
    # "$x = [0.1, -0.2]$" becoming just ","
    (re.compile(r"^\s*,\s*$", re.MULTILINE), "orphan comma on its own line"),
    # Line consisting of just a single character or punctuation
    (re.compile(r"^\s*[.;:\-]\s*$", re.MULTILINE), "orphan punctuation line"),
    # Image placeholders from HTML extraction
    (re.compile(r"\[Image\]|\[image\]"), "raw [Image] placeholder"),
    # Raw MathJax script tags
    (re.compile(r"<script[^>]*math"), "raw MathJax script tag"),
    # Duplicated variable like "K = [0.1] K = [0.1]" — comes from MathJax
    # rendering once as text, once in the tex source
    (re.compile(r"([A-Za-z])\s*=\s*\[[^\]]+\]\s+\1\s*=\s*\[[^\]]+\]"),
     "duplicated variable definition (MathJax artifact)"),
    # HTML entities that should have been decoded
    (re.compile(r"&(amp|lt|gt|quot|nbsp|#\d+);"), "raw HTML entity"),
    # Multiple asterisks/footnote markers scattered
    (re.compile(r"^\s*\d+\s*\.\s*$", re.MULTILINE), "orphan footnote number"),
]


def heuristic_check(title: str, content: str) -> list[str]:
    """Return a list of quality issues found via heuristics.

    Empty list means no issues found. Non-empty list is grounds for rejection.
    """
    issues: list[str] = []

    # Pattern matches.
    for pattern, reason in _RAW_DUMP_PATTERNS:
        matches = pattern.findall(content)
        if matches:
            count = len(matches)
            issues.append(f"{reason} ({count} occurrence{'s' if count > 1 else ''})")

    # Line-based checks.
    lines = content.split("\n")
    orphan_lines = sum(1 for line in lines if len(line.strip()) <= 2 and line.strip())
    if orphan_lines > 8:
        issues.append(
            f"{orphan_lines} near-empty lines (content looks fragmented)"
        )

    # Heading check — a deep dive should have section headings.
    headings = [line for line in lines if line.strip().startswith("#")]
    body_lines = [line for line in lines if line.strip() and not line.strip().startswith("#")]
    if len(body_lines) > 30 and len(headings) < 2:
        issues.append(
            f"long note ({len(body_lines)} lines) with no section headings"
        )

    # Check for actual synthesis — look for explanatory transitions.
    explanatory_markers = [
        "this means", "in other words", "the key insight", "intuition",
        "example:", "note that", "here's why", "because of this",
        "the idea", "this gives us", "substituting",
    ]
    content_lower = content.lower()
    if len(body_lines) > 20:
        marker_hits = sum(1 for m in explanatory_markers if m in content_lower)
        if marker_hits == 0:
            issues.append(
                "no explanatory transitions found — may be raw-copied rather than synthesized"
            )

    return issues


# ---------------------------------------------------------------------------
# LLM-as-judge — semantic quality review
# ---------------------------------------------------------------------------

_REVIEW_PROMPT = """\
You are a quality reviewer for a knowledge note. Check if this note meets
the user's request for a "deep dive with equations and toy examples".

Title: {title}

Content:
{content}

Evaluate on these criteria and return a JSON object with:
{{
    "approved": true or false,
    "issues": ["list of specific issues, each one actionable"],
    "verdict": "brief one-sentence summary"
}}

Reject (approved=false) if ANY of these are true:
1. Content is a raw copy/dump from a source (not synthesized prose)
2. Equations are missing, broken, or present as plain text instead of LaTeX
3. No clear section headings for a note > 30 lines
4. Missing the user-requested elements (toy examples, explanations, diagrams)
5. Content has obvious formatting artifacts (orphan commas, [Image] placeholders,
   duplicated variables, raw HTML)
6. Content is too shallow to count as a "deep dive" (just definitions, no walkthrough)
7. Content lacks explanatory prose — reads like bullet points or copied text
8. Too shallow — content merely defines concepts without explaining them,
   providing examples, or building intuition. A deep dive must go deeper
   than what a reader could find in a dictionary or glossary entry.

Approve (approved=true) only if the note:
- Is clearly synthesized prose with the author's voice
- Has proper LaTeX math (e.g. $x^2 + y^2$ or $$R^\\top R = I$$)
- Has clear sections with # headings
- Includes toy examples with concrete numbers when appropriate
- Has explanatory transitions ("the key insight is...", "this means...", etc.)
- Is a genuine deep dive, not a summary or extract

Return ONLY the JSON object, no other text.
"""


def llm_review(title: str, content: str) -> dict | None:
    """Run an LLM-based quality review. Returns dict or None on failure.

    Dict shape: ``{"approved": bool, "issues": list[str], "verdict": str}``
    """
    import json

    try:
        from prax.agent.llm_factory import build_llm
        from prax.plugins.llm_config import get_component_config

        cfg = get_component_config("note_quality_reviewer")
        llm = build_llm(
            provider=cfg.get("provider"),
            model=cfg.get("model"),
            tier=cfg.get("tier") or "medium",
            temperature=cfg.get("temperature") or 0.2,
        )
        # Truncate very long content to stay within budget.
        review_content = content[:8000]
        if len(content) > 8000:
            review_content += "\n\n[...truncated for review...]"
        prompt = _REVIEW_PROMPT.format(title=title, content=review_content)
        result = llm.invoke(prompt)
        text = result.content if hasattr(result, "content") else str(result)

        # Extract JSON from the response.
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        data = json.loads(text)
        return {
            "approved": bool(data.get("approved", False)),
            "issues": data.get("issues", []),
            "verdict": data.get("verdict", ""),
        }
    except Exception:
        logger.debug("LLM review failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Revision tracking — prevent infinite loops
# ---------------------------------------------------------------------------

_revision_counts: dict[str, int] = {}
_revision_lock = threading.Lock()


def _note_key(title: str) -> str:
    """Hash key used to track revisions of the same conceptual note."""
    normalized = re.sub(r"\s+", " ", title.strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def get_revision_count(title: str) -> int:
    """Return the number of revisions already attempted for this note."""
    with _revision_lock:
        return _revision_counts.get(_note_key(title), 0)


def increment_revision(title: str) -> int:
    """Bump the revision counter and return the new count."""
    with _revision_lock:
        key = _note_key(title)
        _revision_counts[key] = _revision_counts.get(key, 0) + 1
        # Bound the dict — drop oldest entries if too many.
        if len(_revision_counts) > 500:
            # Drop half the entries (simple FIFO — keys aren't time-ordered).
            for k in list(_revision_counts.keys())[:250]:
                _revision_counts.pop(k, None)
        return _revision_counts[key]


def clear_revision(title: str) -> None:
    """Clear the revision counter for a note (call on successful save)."""
    with _revision_lock:
        _revision_counts.pop(_note_key(title), None)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def review_note(title: str, content: str) -> dict:
    """Run the full quality review. Returns a dict with the outcome.

    Output shape::

        {
            "approved": bool,
            "revision": int,           # current revision count
            "max_revisions": int,      # ceiling — bypassed after this
            "heuristic_issues": [...],
            "llm_issues": [...],
            "verdict": str,
            "force_save": bool,        # True if revision >= max (save anyway)
        }
    """
    revision = get_revision_count(title)
    heuristic_issues = heuristic_check(title, content)

    # Only run the LLM review if heuristics pass OR we have budget — no need
    # to double-fail. Skip the LLM call when heuristics already caught issues.
    llm_issues: list[str] = []
    verdict = ""
    if not heuristic_issues:
        llm_result = llm_review(title, content)
        if llm_result is not None:
            if not llm_result["approved"]:
                llm_issues = llm_result["issues"]
            verdict = llm_result["verdict"]

    issues_found = bool(heuristic_issues or llm_issues)
    force_save = revision >= MAX_REVISIONS

    return {
        "approved": not issues_found,
        "revision": revision,
        "max_revisions": MAX_REVISIONS,
        "heuristic_issues": heuristic_issues,
        "llm_issues": llm_issues,
        "verdict": verdict,
        "force_save": force_save,
    }


def format_feedback(review: dict) -> str:
    """Format a review result as actionable feedback for the agent."""
    lines = [
        f"Note quality check FAILED (revision {review['revision']}/{review['max_revisions']}).",
        "",
    ]
    if review["heuristic_issues"]:
        lines.append("**Structural issues:**")
        for issue in review["heuristic_issues"]:
            lines.append(f"  - {issue}")
        lines.append("")
    if review["llm_issues"]:
        lines.append("**Content issues:**")
        for issue in review["llm_issues"]:
            lines.append(f"  - {issue}")
        lines.append("")
    if review["verdict"]:
        lines.append(f"**Reviewer verdict:** {review['verdict']}")
        lines.append("")
    lines.append(
        "Rewrite the note addressing these issues, then call note_create again. "
        "Do NOT save a raw URL dump — synthesize the content with your own prose, "
        "proper LaTeX math, section headings, and explanatory transitions. "
        f"You have {review['max_revisions'] - review['revision']} more attempt(s) "
        "before the check is bypassed."
    )
    return "\n".join(lines)
