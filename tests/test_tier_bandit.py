"""Tests for prax.agent.tier_bandit — Thompson Sampling tier selection."""
from __future__ import annotations

import pytest

from prax.agent.tier_bandit import BetaPosterior, TierBandit


class TestBetaPosterior:
    def test_uniform_prior(self):
        p = BetaPosterior()
        assert p.alpha == 1.0
        assert p.beta == 1.0
        assert p.mean == 0.5
        assert p.sample_count == 0

    def test_update_success(self):
        p = BetaPosterior()
        p.update(success=True)
        assert p.alpha == 2.0
        assert p.beta == 1.0
        assert p.mean == pytest.approx(2 / 3)
        assert p.sample_count == 1

    def test_update_failure(self):
        p = BetaPosterior()
        p.update(success=False)
        assert p.alpha == 1.0
        assert p.beta == 2.0
        assert p.mean == pytest.approx(1 / 3)

    def test_sample_returns_valid_range(self):
        p = BetaPosterior(alpha=5.0, beta=3.0)
        for _ in range(100):
            s = p.sample()
            assert 0.0 <= s <= 1.0

    def test_serialization(self):
        p = BetaPosterior(alpha=3.5, beta=2.1)
        d = p.to_dict()
        p2 = BetaPosterior.from_dict(d)
        assert p2.alpha == 3.5
        assert p2.beta == 2.1


class TestTierBandit:
    def test_select_tier_returns_valid_tier(self, tmp_path):
        bandit = TierBandit(state_path=tmp_path / "state.json")
        tier = bandit.select_tier("orchestrator", "moderate")
        assert tier in ["low", "medium", "high", "pro"]

    def test_select_with_available_tiers(self, tmp_path):
        bandit = TierBandit(state_path=tmp_path / "state.json")
        tier = bandit.select_tier(
            "orchestrator", "moderate",
            available_tiers=["low", "medium"],
        )
        assert tier in ["low", "medium"]

    def test_record_outcome_updates_posterior(self, tmp_path):
        bandit = TierBandit(state_path=tmp_path / "state.json")

        # Record several successes for medium tier
        for _ in range(10):
            bandit.record_outcome("research", "medium", success=True, difficulty="hard")

        # Record several failures for low tier
        for _ in range(10):
            bandit.record_outcome("research", "low", success=False, difficulty="hard")

        stats = bandit.get_stats("research")
        assert stats["research"]["hard"]["medium"]["mean"] > 0.7
        assert stats["research"]["hard"]["low"]["mean"] < 0.3

    def test_exploit_only_uses_mean(self, tmp_path):
        bandit = TierBandit(state_path=tmp_path / "state.json")

        # Load the bandit with a clear winner
        for _ in range(20):
            bandit.record_outcome("test", "medium", success=True, difficulty="moderate")
            bandit.record_outcome("test", "low", success=False, difficulty="moderate")

        # Exploit mode should consistently pick medium
        picks = set()
        for _ in range(10):
            picks.add(bandit.select_tier("test", "moderate", exploit_only=True))
        # With overwhelming evidence, exploit should always pick medium
        assert "medium" in picks

    def test_difficulty_constraints_easy_penalizes_high(self, tmp_path):
        """Easy tasks should penalize high/pro tiers via 0.3x multiplier."""
        bandit = TierBandit(state_path=tmp_path / "state.json")

        # Give all tiers equal success rates for easy tasks
        for tier in ["low", "medium", "high"]:
            for _ in range(10):
                bandit.record_outcome("test", tier, success=True, difficulty="easy")

        # For easy tasks, high tier gets 0.3x efficiency penalty on top of
        # its 16x cost weight — it should almost never be picked over low.
        picks = {"low": 0, "medium": 0, "high": 0}
        for _ in range(100):
            tier = bandit.select_tier("test", "easy", available_tiers=["low", "medium", "high"])
            picks[tier] += 1

        # High should be picked rarely for easy tasks
        assert picks["high"] < 30  # heavily penalized

    def test_persistence(self, tmp_path):
        state_path = tmp_path / "state.json"

        bandit1 = TierBandit(state_path=state_path)
        bandit1.record_outcome("comp1", "medium", success=True, difficulty="moderate")
        bandit1.record_outcome("comp1", "medium", success=True, difficulty="moderate")

        # Load from same file
        bandit2 = TierBandit(state_path=state_path)
        stats = bandit2.get_stats("comp1")
        assert stats["comp1"]["moderate"]["medium"]["alpha"] == 3.0  # 1 prior + 2 successes

    def test_reset(self, tmp_path):
        bandit = TierBandit(state_path=tmp_path / "state.json")
        bandit.record_outcome("comp", "low", success=True)
        assert len(bandit.get_stats()) > 0
        bandit.reset()
        assert len(bandit.get_stats()) == 0

    def test_get_stats_empty(self, tmp_path):
        bandit = TierBandit(state_path=tmp_path / "state.json")
        assert bandit.get_stats() == {}
        assert bandit.get_stats("nonexistent") == {}

    def test_get_stats_single_component(self, tmp_path):
        bandit = TierBandit(state_path=tmp_path / "state.json")
        bandit.record_outcome("a", "low", success=True)
        bandit.record_outcome("b", "high", success=False)

        stats_a = bandit.get_stats("a")
        assert "a" in stats_a
        assert "b" not in stats_a
