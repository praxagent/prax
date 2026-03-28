"""Tiered model selection — LOW / MEDIUM / HIGH / PRO.

Provider-agnostic abstraction over model intelligence levels.  Each tier
maps to a concrete model name via environment variables.  The agent sees
which tiers are enabled at startup and can select the appropriate level
for itself and its sub-agents.

Tier semantics:
    LOW    — cheapest, fastest; simple routing, formatting, classification
    MEDIUM — everyday workhorse; chat, research, tool use
    HIGH   — full intelligence; complex reasoning, planning, coding
    PRO    — maximum capability; long-horizon tasks, hard problems (expensive)

Provider mapping examples:
    OpenAI:    nano / mini / 5.4 / 5.4-pro
    Anthropic: haiku / sonnet / opus / opus (with extended thinking)
    Google:    flash / pro / ultra / ultra
    Local:     small / medium / large / large
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class Tier(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    PRO = "pro"


@dataclass(frozen=True)
class TierConfig:
    """Resolved configuration for a single tier."""
    tier: Tier
    model: str
    enabled: bool


def get_tier_configs() -> dict[Tier, TierConfig]:
    """Load tier configurations from settings.

    Returns a dict mapping each Tier to its TierConfig.  Disabled tiers
    are included (with ``enabled=False``) so callers can inspect what
    *would* be available.
    """
    from prax.settings import settings
    return {
        Tier.LOW: TierConfig(
            tier=Tier.LOW,
            model=settings.low_model,
            enabled=settings.low_enabled,
        ),
        Tier.MEDIUM: TierConfig(
            tier=Tier.MEDIUM,
            model=settings.medium_model,
            enabled=settings.medium_enabled,
        ),
        Tier.HIGH: TierConfig(
            tier=Tier.HIGH,
            model=settings.high_model,
            enabled=settings.high_enabled,
        ),
        Tier.PRO: TierConfig(
            tier=Tier.PRO,
            model=settings.pro_model,
            enabled=settings.pro_enabled,
        ),
    }


def get_available_tiers() -> list[TierConfig]:
    """Return only the enabled tiers, ordered LOW → PRO."""
    return [tc for tc in get_tier_configs().values() if tc.enabled]


def resolve_model(tier: Tier | str | None = None) -> str:
    """Resolve a tier to a concrete model name.

    Falls back gracefully: if the requested tier is disabled, use the
    nearest enabled tier below it.  If nothing below is enabled, try above.
    If nothing at all is enabled, fall back to BASE_MODEL from settings.
    """
    from prax.settings import settings

    if tier is None:
        tier = Tier.LOW

    if isinstance(tier, str):
        try:
            tier = Tier(tier.lower())
        except ValueError:
            logger.warning("Unknown tier '%s', falling back to LOW", tier)
            tier = Tier.LOW

    configs = get_tier_configs()

    # Requested tier is available — use it.
    if configs[tier].enabled:
        return configs[tier].model

    # Fall back: nearest enabled tier below, then above.
    ordered = [Tier.LOW, Tier.MEDIUM, Tier.HIGH, Tier.PRO]
    idx = ordered.index(tier)

    # Search downward first.
    for i in range(idx - 1, -1, -1):
        if configs[ordered[i]].enabled:
            logger.info(
                "Tier %s disabled, falling back to %s (%s)",
                tier.value, ordered[i].value, configs[ordered[i]].model,
            )
            return configs[ordered[i]].model

    # Search upward.
    for i in range(idx + 1, len(ordered)):
        if configs[ordered[i]].enabled:
            logger.info(
                "Tier %s disabled, falling up to %s (%s)",
                tier.value, ordered[i].value, configs[ordered[i]].model,
            )
            return configs[ordered[i]].model

    # Nothing enabled — use BASE_MODEL as ultimate fallback.
    logger.warning("No model tiers enabled, using BASE_MODEL=%s", settings.base_model)
    return settings.base_model


def tier_summary() -> str:
    """Human-readable summary of available tiers for logs and system prompts."""
    configs = get_tier_configs()
    lines = []
    for tier in [Tier.LOW, Tier.MEDIUM, Tier.HIGH, Tier.PRO]:
        tc = configs[tier]
        status = tc.model if tc.enabled else "disabled"
        lines.append(f"  {tier.value.upper():6s} → {status}")
    return "\n".join(lines)


def tier_for_system_prompt() -> str:
    """Compact tier info suitable for embedding in the agent's system prompt."""
    available = get_available_tiers()
    if not available:
        return "No model tiers configured."
    parts = []
    for tc in available:
        parts.append(f"{tc.tier.value.upper()}={tc.model}")
    return "Available model tiers: " + ", ".join(parts) + "."
