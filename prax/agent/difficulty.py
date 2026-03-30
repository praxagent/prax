"""Difficulty-based routing — estimate task complexity before choosing a tier.

Classifies incoming user messages into difficulty levels and recommends an
appropriate model tier.  This allows the orchestrator to start with a
smarter model on hard tasks instead of always defaulting to LOW and hoping
the LLM self-upgrades.

The approach draws on research in adaptive computation and test-time compute
scaling — allocating more inference budget to harder problems.

References:
    - Graves, A. (2016). "Adaptive Computation Time for Recurrent Neural
      Networks." arXiv:1603.08983.
    - Snell et al. (2024). "Scaling LLM Test-Time Compute Optimally Can Be
      More Effective Than Scaling Model Parameters." arXiv:2408.03314.
    - ATLAS project (itigges22/ATLAS) — signal-fused difficulty estimation
      with Thompson Sampling routing.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Difficulty levels
# ---------------------------------------------------------------------------

EASY = "easy"
MODERATE = "moderate"
HARD = "hard"

# Tier recommendations per difficulty level
_TIER_FOR_DIFFICULTY = {
    EASY: "low",
    MODERATE: "low",      # low can handle most moderate tasks
    HARD: "medium",       # upgrade for genuinely hard tasks
}

# ---------------------------------------------------------------------------
# Signal extractors
# ---------------------------------------------------------------------------

# Keywords that signal complex tasks
_HARD_KEYWORDS = re.compile(
    r"\b("
    r"compare|contrast|synthesize|analyze|evaluate|critique|"
    r"research|investigate|deep.?dive|in.?depth|comprehensive|"
    r"multi.?step|complex|detailed|thorough|"
    r"create a course|design a course|teach me|"
    r"debug|troubleshoot|diagnose|"
    r"write.*code|implement|refactor|architect|"
    r"paper|arxiv|pdf|academic"
    r")\b",
    re.IGNORECASE,
)

# Keywords that signal simple tasks
_EASY_KEYWORDS = re.compile(
    r"\b("
    r"what time|what date|hello|hi|hey|thanks|thank you|"
    r"save.*file|save.*to.*workspace|"
    r"what is|who is|when was|where is|"
    r"remind me|set a reminder|"
    r"list my|show my|check my"
    r")\b",
    re.IGNORECASE,
)

# Patterns indicating multi-part requests
_MULTI_PART_PATTERN = re.compile(
    r"(?:\d+[.)]\s)|(?:first.*then)|(?:and also)|(?:additionally)|"
    r"(?:after that)|(?:step \d)|(?:three things|two things|multiple)",
    re.IGNORECASE,
)


def _word_count(text: str) -> int:
    return len(text.split())


def _count_questions(text: str) -> int:
    return text.count("?")


def estimate_difficulty(message: str) -> str:
    """Estimate the difficulty of a user message.

    Returns one of: "easy", "moderate", "hard".

    Uses a weighted signal fusion approach:
      - Message length (longer = more complex intent)
      - Hard/easy keyword density
      - Multi-part request indicators
      - Question count
    """
    if not message or not message.strip():
        return EASY

    score = 0.0

    # Signal 1: Message length
    words = _word_count(message)
    if words > 100:
        score += 2.0
    elif words > 40:
        score += 1.0
    elif words < 10:
        score -= 1.0

    # Signal 2: Hard keywords
    hard_matches = len(_HARD_KEYWORDS.findall(message))
    score += hard_matches * 1.5

    # Signal 3: Easy keywords
    easy_matches = len(_EASY_KEYWORDS.findall(message))
    score -= easy_matches * 1.5

    # Signal 4: Multi-part requests
    multi_parts = len(_MULTI_PART_PATTERN.findall(message))
    score += multi_parts * 1.0

    # Signal 5: Question count (multiple questions = more complex)
    questions = _count_questions(message)
    if questions > 2:
        score += 1.0

    # Signal 6: URLs (fetching/processing = moderate+)
    url_count = len(re.findall(r"https?://", message))
    score += url_count * 0.5

    # Map score to difficulty level
    if score >= 3.0:
        return HARD
    elif score >= 1.0:
        return MODERATE
    else:
        return EASY


def recommended_tier(message: str) -> str:
    """Return the recommended tier for a message based on its difficulty."""
    difficulty = estimate_difficulty(message)
    tier = _TIER_FOR_DIFFICULTY[difficulty]
    logger.debug(
        "Difficulty estimate: %s → tier=%s (message: %.60s...)",
        difficulty, tier, message,
    )
    return tier


def difficulty_context_for_prompt(message: str) -> str:
    """Return a compact difficulty tag for injection into the system prompt.

    This lets the LLM know how the system classified the request, so it
    can calibrate its own tool selection and delegation accordingly.
    """
    difficulty = estimate_difficulty(message)
    return (
        f"[Difficulty: {difficulty.upper()}] "
        f"The system classified this request as {difficulty}. "
        f"{'Use tools and delegation judiciously — this should be straightforward.' if difficulty == EASY else ''}"
        f"{'Plan and delegate as needed.' if difficulty == MODERATE else ''}"
        f"{'This is complex — consider creating a plan and using higher-tier delegation.' if difficulty == HARD else ''}"
    )
