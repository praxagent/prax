"""Earned trust — adaptive constraint relaxation based on demonstrated competence.

Components with high success rates (from the Thompson Sampling bandit) and
no active failure patterns (from metacognitive profiles) earn relaxed
constraints: higher recursion limits and risk level downgrades.

Trust is only applied in balanced or autonomous autonomy modes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Thresholds for earning trust
_MIN_OBSERVATIONS = 20   # need at least this many observations
_HIGH_TRUST_OBS = 50     # extra trust at this many observations
_SUCCESS_THRESHOLD = 0.85  # mean success rate to qualify
_HIGH_SUCCESS = 0.90     # mean success rate for max trust

# Tools eligible for risk downgrade when trust is earned.
# Only browser interaction tools — never schedule/deploy/plugin tools.
_DOWNGRADE_ELIGIBLE = {
    "browser_click",
    "browser_fill",
    "browser_request_login",
    "browser_finish_login",
}


@dataclass
class TrustAdjustments:
    """Trust-based constraint adjustments for a component."""

    recursion_multiplier: float = 1.0
    risk_downgrade_eligible: set[str] = field(default_factory=set)


def get_trust_adjustments(component: str) -> TrustAdjustments:
    """Compute trust adjustments for a component.

    Queries the bandit and metacognitive store. Returns neutral adjustments
    if insufficient data or if autonomy mode is guided.
    """
    from prax.agent.autonomy import GUIDED, get_autonomy_level

    if get_autonomy_level() == GUIDED:
        return TrustAdjustments()

    adjustments = TrustAdjustments()

    try:
        from prax.agent.tier_bandit import get_bandit
        stats = get_bandit().get_stats(component)
        if component not in stats:
            return adjustments

        # Aggregate mean success rate across all difficulties
        total_alpha = 0.0
        total_beta = 0.0
        total_samples = 0
        for _diff, tiers in stats[component].items():
            for _tier, data in tiers.items():
                total_alpha += data.get("alpha", 1.0) - 1.0  # subtract prior
                total_beta += data.get("beta", 1.0) - 1.0
                total_samples += data.get("samples", 0)

        if total_samples < _MIN_OBSERVATIONS:
            return adjustments

        total_obs = total_alpha + total_beta
        mean_success = total_alpha / total_obs if total_obs > 0 else 0.5

        # Scale recursion multiplier based on demonstrated competence
        if mean_success >= _HIGH_SUCCESS and total_samples >= _HIGH_TRUST_OBS:
            adjustments.recursion_multiplier = 1.5
        elif mean_success >= _SUCCESS_THRESHOLD:
            adjustments.recursion_multiplier = 1.25

    except Exception:
        logger.debug("Failed to query bandit for trust", exc_info=True)

    # Check metacognitive profiles — active failure patterns reduce trust
    try:
        from prax.agent.metacognitive import get_metacognitive_store
        profile = get_metacognitive_store().get_profile(component)
        active_patterns = profile.get_active_patterns()

        if active_patterns:
            # Active failure patterns cancel trust bonuses
            adjustments.recursion_multiplier = 1.0
            return adjustments

    except Exception:
        logger.debug("Failed to query metacognitive for trust", exc_info=True)

    # If trust is earned, browser tools eligible for downgrade
    if adjustments.recursion_multiplier > 1.0:
        adjustments.risk_downgrade_eligible = set(_DOWNGRADE_ELIGIBLE)

    return adjustments
