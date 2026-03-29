"""Thompson Sampling bandit for adaptive tier selection.

Learns which model tier works best for each component (orchestrator, research
spoke, browser spoke, etc.) by maintaining Beta distribution posteriors over
success rates.  Each (component, tier) pair has its own Beta(alpha, beta),
updated after every run based on success/failure outcomes.

This replaces static tier assignment with a data-driven approach that
balances exploration (trying underexplored tiers) with exploitation (using
tiers that have proven effective).

References:
    - Thompson, W.R. (1933). "On the Likelihood that One Unknown Probability
      Exceeds Another in View of the Evidence of Two Samples." Biometrika.
    - Chapelle & Li (2011). "An Empirical Evaluation of Thompson Sampling."
      NeurIPS 2011.
    - Russo et al. (2018). "A Tutorial on Thompson Sampling."
      Foundations and Trends in Machine Learning.
    - ATLAS project (itigges22/ATLAS) — Thompson Sampling router with
      cost-weighted efficiency and difficulty-binned posteriors.

Usage::

    from prax.agent.tier_bandit import get_bandit, record_outcome

    # Select tier for a component
    tier = get_bandit().select_tier("subagent_research", difficulty="hard")

    # After the run, record the outcome
    record_outcome("subagent_research", tier="medium", success=True)
"""
from __future__ import annotations

import json
import logging
import random
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_TIERS = ["low", "medium", "high", "pro"]

# Relative cost weights for each tier (used in cost-weighted efficiency)
_TIER_COSTS = {
    "low": 1.0,
    "medium": 4.0,
    "high": 16.0,
    "pro": 64.0,
}

# Persistence path for bandit state
_DEFAULT_STATE_PATH = Path(__file__).parent.parent / "data" / "tier_bandit_state.json"


@dataclass
class BetaPosterior:
    """Beta distribution posterior for a (component, tier) pair."""

    alpha: float = 1.0  # successes + 1 (uniform prior)
    beta: float = 1.0   # failures + 1

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def sample_count(self) -> int:
        """Number of observations (excluding the prior)."""
        return int(self.alpha + self.beta - 2)

    def sample(self) -> float:
        """Draw a sample from the Beta distribution."""
        return random.betavariate(self.alpha, self.beta)

    def update(self, success: bool) -> None:
        """Update the posterior with an observation."""
        if success:
            self.alpha += 1
        else:
            self.beta += 1

    def to_dict(self) -> dict:
        return {"alpha": self.alpha, "beta": self.beta}

    @classmethod
    def from_dict(cls, d: dict) -> BetaPosterior:
        return cls(alpha=d.get("alpha", 1.0), beta=d.get("beta", 1.0))


class TierBandit:
    """Thompson Sampling bandit for model tier selection.

    Maintains Beta posteriors per (component, difficulty, tier).
    Selects the tier with highest cost-weighted efficiency on each pull.
    """

    def __init__(self, state_path: Path | None = None):
        self._state_path = state_path or _DEFAULT_STATE_PATH
        self._lock = threading.Lock()
        # posteriors[component][difficulty][tier] = BetaPosterior
        self._posteriors: dict[str, dict[str, dict[str, BetaPosterior]]] = {}
        self._load()

    def _key(self, component: str, difficulty: str, tier: str) -> BetaPosterior:
        """Get or create the posterior for a (component, difficulty, tier)."""
        if component not in self._posteriors:
            self._posteriors[component] = {}
        if difficulty not in self._posteriors[component]:
            self._posteriors[component][difficulty] = {}
        if tier not in self._posteriors[component][difficulty]:
            self._posteriors[component][difficulty][tier] = BetaPosterior()
        return self._posteriors[component][difficulty][tier]

    def select_tier(
        self,
        component: str,
        difficulty: str = "moderate",
        *,
        available_tiers: list[str] | None = None,
        exploit_only: bool = False,
    ) -> str:
        """Select the best tier for a component using Thompson Sampling.

        Args:
            component: Component name (e.g. "orchestrator", "subagent_research").
            difficulty: Estimated difficulty ("easy", "moderate", "hard").
            available_tiers: Subset of tiers to consider.  Defaults to all.
            exploit_only: If True, use mean instead of sampling (no exploration).

        Returns:
            The selected tier name.
        """
        tiers = available_tiers or _TIERS

        with self._lock:
            best_tier = tiers[0]
            best_efficiency = -1.0

            for tier in tiers:
                posterior = self._key(component, difficulty, tier)

                if exploit_only:
                    p_success = posterior.mean
                else:
                    p_success = posterior.sample()

                cost = _TIER_COSTS.get(tier, 1.0)
                efficiency = p_success / cost

                # Apply difficulty constraints:
                # Penalize low tier for hard tasks, high tier for easy tasks
                if difficulty == "hard" and tier == "low":
                    efficiency *= 0.5
                elif difficulty == "easy" and tier in ("high", "pro"):
                    efficiency *= 0.3

                if efficiency > best_efficiency:
                    best_efficiency = efficiency
                    best_tier = tier

        logger.debug(
            "Bandit selected tier=%s for %s (difficulty=%s, efficiency=%.3f)",
            best_tier, component, difficulty, best_efficiency,
        )
        return best_tier

    def record_outcome(
        self,
        component: str,
        tier: str,
        success: bool,
        difficulty: str = "moderate",
    ) -> None:
        """Record the outcome of a run for posterior updates.

        Args:
            component: Component name.
            tier: The tier that was used.
            success: Whether the run was successful.
            difficulty: The difficulty level of the task.
        """
        with self._lock:
            posterior = self._key(component, difficulty, tier)
            posterior.update(success)

        logger.info(
            "Bandit update: %s/%s/%s %s → alpha=%.1f beta=%.1f (mean=%.3f, n=%d)",
            component, difficulty, tier,
            "SUCCESS" if success else "FAILURE",
            posterior.alpha, posterior.beta, posterior.mean, posterior.sample_count,
        )
        self._save()

    def get_stats(self, component: str | None = None) -> dict:
        """Return current bandit statistics for inspection.

        Args:
            component: If provided, return stats for this component only.
        """
        with self._lock:
            result = {}
            for comp, difficulties in self._posteriors.items():
                if component and comp != component:
                    continue
                result[comp] = {}
                for diff, tiers in difficulties.items():
                    result[comp][diff] = {}
                    for tier, posterior in tiers.items():
                        result[comp][diff][tier] = {
                            "alpha": posterior.alpha,
                            "beta": posterior.beta,
                            "mean": round(posterior.mean, 4),
                            "samples": posterior.sample_count,
                        }
            return result

    def _save(self) -> None:
        """Persist bandit state to JSON."""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = {}
                for comp, difficulties in self._posteriors.items():
                    data[comp] = {}
                    for diff, tiers in difficulties.items():
                        data[comp][diff] = {
                            tier: p.to_dict() for tier, p in tiers.items()
                        }
            self._state_path.write_text(json.dumps(data, indent=2))
        except Exception:
            logger.debug("Failed to save bandit state", exc_info=True)

    def _load(self) -> None:
        """Load bandit state from JSON if it exists."""
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text())
            with self._lock:
                for comp, difficulties in data.items():
                    self._posteriors[comp] = {}
                    for diff, tiers in difficulties.items():
                        self._posteriors[comp][diff] = {
                            tier: BetaPosterior.from_dict(p)
                            for tier, p in tiers.items()
                        }
            logger.info("Loaded bandit state: %d components", len(self._posteriors))
        except Exception:
            logger.debug("Failed to load bandit state", exc_info=True)

    def reset(self) -> None:
        """Clear all posteriors (back to uniform priors)."""
        with self._lock:
            self._posteriors.clear()
        self._save()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_bandit: TierBandit | None = None
_bandit_lock = threading.Lock()


def get_bandit(state_path: Path | None = None) -> TierBandit:
    """Return the singleton TierBandit instance."""
    global _bandit
    if _bandit is None:
        with _bandit_lock:
            if _bandit is None:
                _bandit = TierBandit(state_path=state_path)
    return _bandit


def record_outcome(
    component: str,
    tier: str,
    success: bool,
    difficulty: str = "moderate",
) -> None:
    """Convenience function to record an outcome on the global bandit."""
    get_bandit().record_outcome(component, tier, success, difficulty)
