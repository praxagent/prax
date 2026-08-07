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
    # Shaped like the real settings object: tiers are a SET plus a
    # tier_enabled() predicate, not four booleans. A fake that keeps the old
    # shape would let a consolidation like this pass its tests while breaking
    # production — the failure mode this suite exists to catch.
    fake = SimpleNamespace(
        low_model="gpt-5.4-nano",
        medium_model="gpt-5.4-mini",
        high_model="gpt-5.4",
        pro_model="gpt-5.4-pro",
        base_model="gpt-5.4-nano",
        enabled_tiers="low,medium,high",
    )
    fake.tier_enabled = lambda name: name in set(fake.enabled_tiers.split(","))
    monkeypatch.setattr("prax.agent.model_tiers.settings", fake, raising=False)

    # Patch the lazy import inside get_tier_configs / resolve_model.
    import prax.agent.model_tiers as mod
    _orig_get = mod.get_tier_configs

    def _patched():
        return {
            Tier.LOW: TierConfig(Tier.LOW, fake.low_model, fake.tier_enabled("low")),
            Tier.MEDIUM: TierConfig(Tier.MEDIUM, fake.medium_model, fake.tier_enabled("medium")),
            Tier.HIGH: TierConfig(Tier.HIGH, fake.high_model, fake.tier_enabled("high")),
            Tier.PRO: TierConfig(Tier.PRO, fake.pro_model, fake.tier_enabled("pro")),
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


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        # test_resolve_model_returns_requested_tier
        ("low", "gpt-5.4-nano"),
        ("medium", "gpt-5.4-mini"),
        ("high", "gpt-5.4"),
        # test_resolve_model_defaults_to_low
        (None, "gpt-5.4-nano"),
        # test_resolve_model_handles_unknown_tier
        ("ultra", "gpt-5.4-nano"),
        # test_resolve_model_accepts_tier_enum
        (Tier.HIGH, "gpt-5.4"),
    ],
)
def test_resolve_model(requested, expected):
    assert resolve_model(requested) == expected


def test_resolve_model_falls_back_when_disabled(_mock_settings):
    _mock_settings.enabled_tiers = "low,medium,high"
    # PRO disabled — should fall back to HIGH
    result = resolve_model("pro")
    assert result == "gpt-5.4"


def test_resolve_model_falls_up_when_lower_disabled(_mock_settings):
    _mock_settings.enabled_tiers = "high"
    # LOW disabled, MEDIUM disabled — should fall up to HIGH
    result = resolve_model("low")
    assert result == "gpt-5.4"


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


class TestEnabledTiersConsolidation:
    """ENABLED_TIERS replaced four booleans; the old vars must still work.

    Four booleans expressing membership in a set is the shape that should have
    been a set. But an existing deployment's .env still says LOW_ENABLED=false,
    and silently changing its behaviour on upgrade would be worse than the
    duplication being removed — so the legacy vars still win when set, and warn.
    """

    def test_the_set_is_parsed(self, monkeypatch):
        from prax.settings import AppSettings
        s = AppSettings(FLASK_SECRET_KEY="x", ENABLED_TIERS="low, HIGH ")
        assert s.enabled_tiers == "low,high"
        monkeypatch.delenv("HIGH_ENABLED", raising=False)
        monkeypatch.delenv("LOW_ENABLED", raising=False)
        monkeypatch.delenv("PRO_ENABLED", raising=False)
        assert s.tier_enabled("low") is True
        assert s.tier_enabled("high") is True
        assert s.tier_enabled("pro") is False

    def test_an_unknown_tier_fails_loudly(self):
        import pytest as _pytest

        from prax.settings import AppSettings
        with _pytest.raises(Exception, match="unknown tier"):
            AppSettings(FLASK_SECRET_KEY="x", ENABLED_TIERS="low,ultra")

    def test_an_empty_set_fails_loudly(self):
        import pytest as _pytest

        from prax.settings import AppSettings
        with _pytest.raises(Exception, match="at least one tier"):
            AppSettings(FLASK_SECRET_KEY="x", ENABLED_TIERS=" ")

    def test_a_legacy_var_still_wins(self, monkeypatch):
        """An existing .env must not silently change behaviour on upgrade."""
        from prax.settings import AppSettings
        s = AppSettings(FLASK_SECRET_KEY="x", ENABLED_TIERS="low,medium,high")
        monkeypatch.setenv("HIGH_ENABLED", "false")
        assert s.tier_enabled("high") is False
        monkeypatch.setenv("PRO_ENABLED", "true")
        assert s.tier_enabled("pro") is True

    def test_legacy_absent_means_the_set_decides(self, monkeypatch):
        from prax.settings import AppSettings
        s = AppSettings(FLASK_SECRET_KEY="x", ENABLED_TIERS="low")
        for v in ("LOW_ENABLED", "MEDIUM_ENABLED", "HIGH_ENABLED", "PRO_ENABLED"):
            monkeypatch.delenv(v, raising=False)
        assert s.tier_enabled("low") is True
        assert s.tier_enabled("medium") is False


class TestTeamWorkSingleGate:
    """TEAMWORK_URL is the switch; the boolean was a second gate on one thing.

    CLAUDE.md documents "TEAMWORK_URL empty → Prax silently skips" as a trap
    that yields an empty workspace instead of an error. The redundancy WAS the
    trap: a deployment could set the URL and still be skipped because the
    boolean defaulted false.
    """

    def _s(self, **kw):
        from prax.settings import AppSettings
        return AppSettings(FLASK_SECRET_KEY="x", **kw)

    def test_a_url_alone_turns_it_on(self, monkeypatch):
        monkeypatch.delenv("TEAMWORK_ENABLED", raising=False)
        assert self._s(TEAMWORK_URL="http://tw:8000").teamwork_active is True

    def test_no_url_means_standalone(self, monkeypatch):
        monkeypatch.delenv("TEAMWORK_ENABLED", raising=False)
        assert self._s(TEAMWORK_URL="").teamwork_active is False

    def test_an_explicit_legacy_off_still_wins(self, monkeypatch):
        """An existing .env that deliberately disabled TeamWork keeps working."""
        monkeypatch.setenv("TEAMWORK_ENABLED", "false")
        assert self._s(TEAMWORK_URL="http://tw:8000").teamwork_active is False

    def test_legacy_true_is_not_required(self, monkeypatch):
        monkeypatch.setenv("TEAMWORK_ENABLED", "true")
        assert self._s(TEAMWORK_URL="http://tw:8000").teamwork_active is True
        assert self._s(TEAMWORK_URL="").teamwork_active is False


def test_legacy_tier_warning_fires_once_per_process(monkeypatch, caplog):
    """A deprecation notice that floods the log teaches people to ignore the
    log — and tier_enabled runs on every LLM build."""
    import logging

    from prax import settings as settings_mod
    from prax.settings import AppSettings

    monkeypatch.setattr(settings_mod, "_WARNED_LEGACY_TIERS", set())
    monkeypatch.setenv("PRO_ENABLED", "true")
    s = AppSettings(FLASK_SECRET_KEY="x", ENABLED_TIERS="low")
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            assert s.tier_enabled("pro") is True
    assert sum("PRO_ENABLED is deprecated" in r.message for r in caplog.records) == 1
