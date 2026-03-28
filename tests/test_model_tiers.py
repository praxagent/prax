"""Tests for prax.agent.model_tiers."""
from types import SimpleNamespace

import pytest

from prax.agent.model_tiers import (
    Tier,
    TierConfig,
    get_available_tiers,
    resolve_model,
    tier_for_system_prompt,
    tier_summary,
)


@pytest.fixture(autouse=True)
def _mock_settings(monkeypatch):
    """Provide fake settings for all tests."""
    fake = SimpleNamespace(
        low_model="gpt-5.4-nano",
        low_enabled=True,
        medium_model="gpt-5.4-mini",
        medium_enabled=True,
        high_model="gpt-5.4",
        high_enabled=True,
        pro_model="gpt-5.4-pro",
        pro_enabled=False,
        base_model="gpt-5.4-nano",
    )
    monkeypatch.setattr("prax.agent.model_tiers.settings", fake, raising=False)

    # Patch the lazy import inside get_tier_configs / resolve_model.
    import prax.agent.model_tiers as mod
    _orig_get = mod.get_tier_configs

    def _patched():
        return {
            Tier.LOW: TierConfig(Tier.LOW, fake.low_model, fake.low_enabled),
            Tier.MEDIUM: TierConfig(Tier.MEDIUM, fake.medium_model, fake.medium_enabled),
            Tier.HIGH: TierConfig(Tier.HIGH, fake.high_model, fake.high_enabled),
            Tier.PRO: TierConfig(Tier.PRO, fake.pro_model, fake.pro_enabled),
        }

    monkeypatch.setattr(mod, "get_tier_configs", _patched)
    yield fake


def test_tier_enum_values():
    assert Tier.LOW.value == "low"
    assert Tier.MEDIUM.value == "medium"
    assert Tier.HIGH.value == "high"
    assert Tier.PRO.value == "pro"


def test_get_available_tiers_excludes_disabled(_mock_settings):
    available = get_available_tiers()
    names = [t.tier for t in available]
    assert Tier.LOW in names
    assert Tier.MEDIUM in names
    assert Tier.HIGH in names
    assert Tier.PRO not in names  # disabled by default


def test_resolve_model_returns_requested_tier():
    assert resolve_model("low") == "gpt-5.4-nano"
    assert resolve_model("medium") == "gpt-5.4-mini"
    assert resolve_model("high") == "gpt-5.4"


def test_resolve_model_falls_back_when_disabled(_mock_settings):
    _mock_settings.pro_enabled = False
    # PRO disabled — should fall back to HIGH
    result = resolve_model("pro")
    assert result == "gpt-5.4"


def test_resolve_model_falls_up_when_lower_disabled(_mock_settings):
    _mock_settings.low_enabled = False
    _mock_settings.medium_enabled = False
    _mock_settings.high_enabled = True
    # LOW disabled, MEDIUM disabled — should fall up to HIGH
    result = resolve_model("low")
    assert result == "gpt-5.4"


def test_resolve_model_defaults_to_low():
    assert resolve_model(None) == "gpt-5.4-nano"


def test_resolve_model_handles_unknown_tier():
    # Unknown tier string should default to LOW
    assert resolve_model("ultra") == "gpt-5.4-nano"


def test_resolve_model_accepts_tier_enum():
    assert resolve_model(Tier.HIGH) == "gpt-5.4"


def test_tier_summary_format():
    summary = tier_summary()
    assert "LOW" in summary
    assert "MEDIUM" in summary
    assert "HIGH" in summary
    assert "PRO" in summary
    assert "disabled" in summary  # PRO is disabled


def test_tier_for_system_prompt():
    info = tier_for_system_prompt()
    assert "LOW=gpt-5.4-nano" in info
    assert "MEDIUM=gpt-5.4-mini" in info
    assert "HIGH=gpt-5.4" in info
    # PRO is disabled, should not appear
    assert "PRO" not in info
