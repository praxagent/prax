"""The model catalog — what the UI is allowed to claim about providers.

The hard part is not listing models, it is being honest under KEYLESS operation.
There the real credentials live in the secrets-proxy and Prax holds only a
placeholder, so "is ANTHROPIC_KEY set here?" is the wrong question: the answer is
"no" on a correctly-configured box where Anthropic works fine. Reporting that as
"unavailable" would be a confident lie, so the catalog separates what Prax knows
(`verified`) from what it can only infer (`configured`).
"""
from __future__ import annotations

import pytest

from prax.agent import model_catalog as mc

# ── Provider inference ───────────────────────────────────────────────────────

@pytest.mark.parametrize("model,expected", [
    ("gpt-5.4-mini", "openai"),
    ("gpt-4o", "openai"),
    ("o3-mini", "openai"),
    ("claude-sonnet-4-6", "anthropic"),
    ("openai/gpt-4o-mini", "openrouter"),      # namespaced => routed
    ("anthropic/claude-3.5", "openrouter"),
    ("some-local-llama", "unknown"),
    ("", "unknown"),
    (None, "unknown"),
])
def test_provider_inference(model, expected):
    assert mc.provider_for_model(model) == expected


def test_namespaced_name_wins_over_the_bare_prefix():
    # The bug this guards: "openai/gpt-4o" starts with neither hint cleanly, and
    # a naive startswith("gpt-") check would file OpenRouter models under OpenAI.
    assert mc.provider_for_model("openai/gpt-4o-mini") == "openrouter"
    assert mc.provider_for_model("gpt-4o-mini") == "openai"


# ── Honesty under keyless operation ──────────────────────────────────────────

def test_direct_key_is_reported_as_verified(monkeypatch):
    from prax.settings import settings
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.setattr(settings, "openai_key", "sk-real", raising=False)

    openai = next(p for p in mc.provider_status() if p.id == "openai")
    assert openai.configured and openai.verified
    assert openai.reason is None


def test_proxied_egress_is_configured_but_NOT_verified(monkeypatch):
    # The heart of it: with a placeholder key and a proxy, Prax must not claim
    # certainty it does not have.
    from prax.settings import settings
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8786")
    monkeypatch.setattr(settings, "openai_key", "placeholder-value", raising=False)

    openai = next(p for p in mc.provider_status() if p.id == "openai")
    assert openai.configured is True
    assert openai.verified is False
    assert "proxied" in (openai.reason or "")


def test_no_local_key_but_proxied_still_counts_as_configured(monkeypatch):
    # A keyless box legitimately has no Anthropic key locally; the proxy injects
    # it. Reporting "unavailable" here would be wrong.
    from prax.settings import settings
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8786")
    monkeypatch.setattr(settings, "anthropic_key", None, raising=False)
    monkeypatch.setattr(settings, "anthropic_base_url", None, raising=False)

    anthropic = next(p for p in mc.provider_status() if p.id == "anthropic")
    assert anthropic.configured is True and anthropic.verified is False
    assert "proxy" in (anthropic.reason or "").lower()


def test_no_key_and_no_proxy_is_honestly_unavailable(monkeypatch):
    from prax.settings import settings
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.setattr(settings, "anthropic_key", None, raising=False)
    monkeypatch.setattr(settings, "anthropic_base_url", None, raising=False)

    anthropic = next(p for p in mc.provider_status() if p.id == "anthropic")
    assert anthropic.configured is False and anthropic.verified is False
    assert "ANTHROPIC_KEY" in (anthropic.reason or "")


def test_every_reason_is_actionable_when_unavailable(monkeypatch):
    # A greyed-out provider with no explanation is a support ticket.
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    from prax.settings import settings
    for spec in mc.PROVIDERS:
        monkeypatch.setattr(settings, spec["env_key"], None, raising=False)
        monkeypatch.setattr(settings, spec["base_url_setting"], None, raising=False)
    for p in mc.provider_status():
        assert p.reason, f"{p.id} is unavailable with no reason given"


# ── Selection mode: three states, kept distinct ──────────────────────────────

def test_selection_modes():
    assert mc.selection_mode(None) == "auto"
    assert mc.selection_mode("") == "auto"
    assert mc.selection_mode("high") == "tier"        # pin a capability level
    assert mc.selection_mode("gpt-5.5") == "model"    # pin one exact model


def test_tier_pin_is_not_confused_with_a_model_named_like_a_tier():
    # "high" is a tier; a model must not be misread as one just by being short.
    assert mc.selection_mode("claude-high-2") == "model"


# ── The assembled catalog ────────────────────────────────────────────────────

def test_catalog_shape_is_complete():
    cat = mc.catalog(None)
    assert {"providers", "tiers", "mode", "override", "egress_proxied"} <= set(cat)
    assert {p["id"] for p in cat["providers"]} == {"openai", "anthropic", "openrouter"}


def test_catalog_reports_all_four_tiers_with_their_provider():
    tiers = mc.catalog(None)["tiers"]
    assert {"low", "medium", "high", "pro"} <= set(tiers)
    for name, info in tiers.items():
        assert {"model", "enabled", "provider"} <= set(info), name


def test_catalog_groups_configured_models_under_their_provider():
    cat = mc.catalog(None)
    for provider in cat["providers"]:
        for model in provider["models"]:
            assert mc.provider_for_model(model) == provider["id"]


def test_catalog_reflects_the_override_in_mode():
    assert mc.catalog("high")["mode"] == "tier"
    assert mc.catalog("gpt-5.5")["mode"] == "model"
    assert mc.catalog(None)["mode"] == "auto"
