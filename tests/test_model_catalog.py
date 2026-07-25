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


# ── Dynamic discovery: ask the provider what it can serve ────────────────────
#
# Tier assignments only ever name the handful of models a deployment is
# configured to use, so a provider that is configured but unused showed an empty
# list — you could see OpenRouter was available and still have nothing to pick.

def _stub_requests(monkeypatch, payload=None, boom=None, capture=None):
    import requests

    class _Resp:
        def __init__(self, data): self._data = data
        def raise_for_status(self): pass
        def json(self): return self._data

    def fake_get(url, headers=None, timeout=None):
        if capture is not None:
            capture.update({"url": url, "headers": headers or {}})
        if boom:
            raise boom
        return _Resp(payload or {"data": []})

    monkeypatch.setattr(requests, "get", fake_get)


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    mc._discovery_cache.clear()
    yield
    mc._discovery_cache.clear()


def test_discovery_returns_sorted_unique_ids(monkeypatch):
    _stub_requests(monkeypatch, {"data": [{"id": "b"}, {"id": "a"}, {"id": "a"}, {}]})
    assert mc.discover_models("openrouter") == ["a", "b"]


def test_openrouter_needs_no_credential(monkeypatch):
    # Its catalogue is public — requiring a key would be a needless failure mode.
    cap = {}
    _stub_requests(monkeypatch, {"data": [{"id": "x/y"}]}, capture=cap)
    assert mc.discover_models("openrouter") == ["x/y"]
    assert "Authorization" not in cap["headers"] and "x-api-key" not in cap["headers"]


def test_openai_sends_a_bearer_token(monkeypatch):
    from prax.settings import settings
    monkeypatch.setattr(settings, "openai_key", "placeholder", raising=False)
    cap = {}
    _stub_requests(monkeypatch, {"data": [{"id": "gpt-x"}]}, capture=cap)
    assert mc.discover_models("openai") == ["gpt-x"]
    assert cap["headers"]["Authorization"] == "Bearer placeholder"


def test_anthropic_sends_its_own_header_and_version(monkeypatch):
    from prax.settings import settings
    monkeypatch.setattr(settings, "anthropic_key", "placeholder", raising=False)
    cap = {}
    _stub_requests(monkeypatch, {"data": [{"id": "claude-x"}]}, capture=cap)
    assert mc.discover_models("anthropic") == ["claude-x"]
    assert cap["headers"]["x-api-key"] == "placeholder"
    assert cap["headers"]["anthropic-version"]


def test_a_provider_with_no_key_is_not_called_at_all(monkeypatch):
    from prax.settings import settings
    monkeypatch.setattr(settings, "anthropic_key", None, raising=False)
    _stub_requests(monkeypatch, boom=AssertionError("should not have been called"))
    assert mc.discover_models("anthropic") == []


def test_a_network_failure_costs_a_shorter_list_not_an_exception(monkeypatch):
    # This feeds a badge in the chat header. A provider having a bad minute must
    # not raise into the UI.
    _stub_requests(monkeypatch, boom=RuntimeError("connection reset"))
    assert mc.discover_models("openrouter") == []


def test_results_are_cached_so_ui_polling_does_not_hammer_providers(monkeypatch):
    calls = {"n": 0}
    import requests

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"data": [{"id": "m"}]}

    def counting_get(url, headers=None, timeout=None):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(requests, "get", counting_get)
    assert mc.discover_models("openrouter") == ["m"]
    assert mc.discover_models("openrouter") == ["m"]
    assert calls["n"] == 1, "the second call should have been served from cache"


def test_a_failure_is_retried_sooner_than_a_success(monkeypatch):
    # Cache the failure briefly (so a down provider is not retried every poll)
    # but recover well before a successful lookup would expire.
    _stub_requests(monkeypatch, boom=RuntimeError("down"))
    mc.discover_models("openrouter")
    stamp, _ = mc._discovery_cache["openrouter"]
    import time
    age = time.time() - stamp
    assert age > mc._DISCOVERY_TTL_SECONDS - 120, "failure should expire in ~a minute"


def test_unknown_provider_is_not_probed(monkeypatch):
    _stub_requests(monkeypatch, boom=AssertionError("should not have been called"))
    assert mc.discover_models("nope") == []


def test_catalog_does_not_discover_unless_asked(monkeypatch):
    # The common call is a UI poll that only needs the current selection; three
    # provider round-trips per poll would be slow and rude.
    _stub_requests(monkeypatch, boom=AssertionError("discovery should be opt-in"))
    mc.catalog(None)


def test_catalog_with_discovery_keeps_configured_models_first_class(monkeypatch):
    # Discovery must ADD to what the deployment is configured to use, never
    # replace it — a tier's model has to stay pickable even if a provider omits
    # it or the lookup fails.
    _stub_requests(monkeypatch, {"data": [{"id": "brand-new-model"}]})
    from prax.settings import settings
    monkeypatch.setattr(settings, "openai_key", "placeholder", raising=False)
    cat = mc.catalog(None, discover=True)
    openai = next(p for p in cat["providers"] if p["id"] == "openai")
    assert "brand-new-model" in openai["models"]
    for tier in cat["tiers"].values():
        if tier["provider"] == "openai" and tier["model"]:
            assert tier["model"] in openai["models"], "a configured model vanished"
