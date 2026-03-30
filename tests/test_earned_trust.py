"""Tests for prax.agent.earned_trust — adaptive constraint relaxation."""
from __future__ import annotations

from unittest.mock import patch

from prax.agent.earned_trust import (
    _DOWNGRADE_ELIGIBLE,
    _HIGH_TRUST_OBS,
    TrustAdjustments,
    get_trust_adjustments,
)


class TestTrustAdjustments:
    def test_default_is_neutral(self):
        t = TrustAdjustments()
        assert t.recursion_multiplier == 1.0
        assert t.risk_downgrade_eligible == set()

    def test_guided_mode_always_neutral(self):
        with patch("prax.agent.autonomy.get_autonomy_level", return_value="guided"):
            t = get_trust_adjustments("research")
            assert t.recursion_multiplier == 1.0
            assert t.risk_downgrade_eligible == set()

    def test_insufficient_observations_neutral(self, tmp_path):
        """Below _MIN_OBSERVATIONS, no trust is granted."""
        from prax.agent.tier_bandit import TierBandit

        with patch("prax.agent.autonomy.get_autonomy_level", return_value="balanced"):
            bandit = TierBandit(state_path=tmp_path / "state.json")
            # Record only a few observations
            for _ in range(5):
                bandit.record_outcome("test_comp", "medium", success=True)

            with patch("prax.agent.tier_bandit.get_bandit", return_value=bandit):
                t = get_trust_adjustments("test_comp")
                assert t.recursion_multiplier == 1.0

    def test_high_success_grants_trust(self, tmp_path):
        """Above threshold success rate and observations → trust bonus."""
        from prax.agent.tier_bandit import TierBandit

        with patch("prax.agent.autonomy.get_autonomy_level", return_value="balanced"):
            bandit = TierBandit(state_path=tmp_path / "state.json")
            # Record enough successful observations
            for _ in range(_HIGH_TRUST_OBS + 5):
                bandit.record_outcome("trusted_comp", "medium", success=True)

            with patch("prax.agent.tier_bandit.get_bandit", return_value=bandit):
                with patch("prax.agent.metacognitive.get_metacognitive_store") as mock_store:
                    mock_profile = mock_store.return_value.get_profile.return_value
                    mock_profile.get_active_patterns.return_value = []

                    t = get_trust_adjustments("trusted_comp")
                    assert t.recursion_multiplier == 1.5
                    assert "browser_click" in t.risk_downgrade_eligible

    def test_active_failure_patterns_cancel_trust(self, tmp_path):
        """Active metacognitive patterns should cancel trust bonuses."""
        from prax.agent.metacognitive import FailurePattern
        from prax.agent.tier_bandit import TierBandit

        with patch("prax.agent.autonomy.get_autonomy_level", return_value="balanced"):
            bandit = TierBandit(state_path=tmp_path / "state.json")
            for _ in range(_HIGH_TRUST_OBS + 5):
                bandit.record_outcome("failing_comp", "medium", success=True)

            with patch("prax.agent.tier_bandit.get_bandit", return_value=bandit):
                with patch("prax.agent.metacognitive.get_metacognitive_store") as mock_store:
                    mock_profile = mock_store.return_value.get_profile.return_value
                    # Simulate an active failure pattern
                    mock_profile.get_active_patterns.return_value = [
                        FailurePattern(
                            pattern_id="timeout",
                            description="Times out frequently",
                            occurrences=5,
                            confidence=0.8,
                        )
                    ]

                    t = get_trust_adjustments("failing_comp")
                    assert t.recursion_multiplier == 1.0
                    assert t.risk_downgrade_eligible == set()

    def test_downgrade_eligible_set(self):
        """Only browser interaction tools should be downgrade-eligible."""
        assert "browser_click" in _DOWNGRADE_ELIGIBLE
        assert "browser_fill" in _DOWNGRADE_ELIGIBLE
        # System mutation tools should NEVER be eligible
        assert "self_improve_deploy" not in _DOWNGRADE_ELIGIBLE
        assert "plugin_activate" not in _DOWNGRADE_ELIGIBLE
        assert "schedule_create" not in _DOWNGRADE_ELIGIBLE
