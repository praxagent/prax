"""Autonomy profiles — configurable constraint levels for the agent.

Three levels:
  guided:    All constraints active, prescriptive workflow rules enforced.
  balanced:  Prescriptive workflow rules become preferences; agent uses judgment.
  autonomous: Also relaxes recursion limits and allows self-tier-upgrade.

The autonomy level is set via the PRAX_AUTONOMY environment variable.
"""
from __future__ import annotations

import logging

from prax.settings import settings

logger = logging.getLogger(__name__)

GUIDED = "guided"
BALANCED = "balanced"
AUTONOMOUS = "autonomous"

_VALID_LEVELS = {GUIDED, BALANCED, AUTONOMOUS}


def get_autonomy_level() -> str:
    """Return the current autonomy level from settings."""
    level = settings.autonomy.lower().strip()
    if level not in _VALID_LEVELS:
        logger.warning(
            "Invalid PRAX_AUTONOMY='%s', falling back to 'guided'. "
            "Valid values: guided, balanced, autonomous",
            level,
        )
        return GUIDED
    return level


def get_recursion_limit(base_limit: int) -> int:
    """Return an autonomy-adjusted recursion limit.

    guided:     base_limit (unchanged)
    balanced:   base_limit * 1.25
    autonomous: base_limit * 1.5
    """
    level = get_autonomy_level()
    if level == AUTONOMOUS:
        return int(base_limit * 1.5)
    if level == BALANCED:
        return int(base_limit * 1.25)
    return base_limit


def is_prescriptive() -> bool:
    """True when prescriptive workflow rules should be enforced (guided mode)."""
    return get_autonomy_level() == GUIDED


def is_autonomous() -> bool:
    """True when running in autonomous mode."""
    return get_autonomy_level() == AUTONOMOUS
