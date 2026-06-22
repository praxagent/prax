"""Cross-provider failover: terminal-error classification + provider denylist.

Covers the "give up on a model, denylist it from the pool, and tell the user why"
behavior: terminal failures (auth/billing/access/decommissioned) get the provider
denylisted (not re-hit every turn) with a user-facing heads-up, while transient
failures keep the old retry/failover behavior. Pure helpers are tested directly;
the orchestrator paths are exercised on a bare instance with a stubbed
``_bind_provider`` so no real LLM is built.
"""
from __future__ import annotations

import time

import pytest

from prax.agent import llm_fallback as fb
from prax.agent.orchestrator import ConversationAgent


def _live_settings():
    # conftest's autouse fixture reloads prax.settings per test and re-points
    # every module's `settings` global to a fresh object — so a top-level
    # `from prax.settings import settings` would be stale. Fetch the live one.
    import prax.settings as _ps
    return _ps.settings


# --------------------------------------------------------------------------- #
# classify_provider_error
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("msg, expected", [
    ("Error code: 401 - invalid api key provided", "auth"),
    ("AuthenticationError: incorrect API key", "auth"),
    ("429 insufficient_quota: You exceeded your current quota, check billing", "billing"),
    ("402 Payment Required: credit balance is too low", "billing"),
    ("403 Forbidden: this model is not available in your region", "access"),
    ("You do not have access to model gpt-x", "access"),
    ("The model `gpt-foo` does not exist or has been deprecated", "decommissioned"),
    ("model_not_found", "decommissioned"),
    ("Rate limit reached for requests", "transient"),
    ("503 Service Unavailable", "transient"),
    ("upstream connection error", "transient"),
])
def test_classify_marks_kind(msg, expected):
    assert fb.classify_provider_error(Exception(msg)) == expected


def test_classify_connection_and_timeout_types_are_transient():
    assert fb.classify_provider_error(ConnectionError("boom")) == "transient"
    assert fb.classify_provider_error(TimeoutError("slow")) == "transient"


def test_classify_non_provider_error_is_none():
    # A logic/validation/tool error a second provider can't fix.
    assert fb.classify_provider_error(ValueError("bad tool argument")) is None
    assert fb.classify_provider_error(KeyError("missing field")) is None


def test_terminal_beats_transient_when_both_present():
    # An insufficient_quota 429 is billing (terminal), not a transient rate limit.
    assert fb.classify_provider_error(
        Exception("429 Too Many Requests: insufficient_quota / billing hard limit")
    ) == "billing"


# --------------------------------------------------------------------------- #
# terminal_user_notice
# --------------------------------------------------------------------------- #
def test_notice_names_provider_cause_and_fallback():
    n = fb.terminal_user_notice("openai", "billing", "RateLimitError",
                                continuing="anthropic", cooldown_seconds=1800)
    assert "openai" in n
    assert "billing" in n.lower()
    assert "anthropic" in n          # continuing on the fallback
    assert "30 min" in n             # cooldown re-probe hint
    assert "RateLimitError" in n     # detail is the type name…
    # …never the raw message (which can echo the API key) — caller passes type name only.


def test_notice_when_no_fallback_left():
    n = fb.terminal_user_notice("openai", "auth", "AuthenticationError", continuing=None)
    assert "no other provider" in n.lower()


# --------------------------------------------------------------------------- #
# Orchestrator denylist integration (bare instance, stubbed _bind_provider)
# --------------------------------------------------------------------------- #
def _make_orch(monkeypatch, primary="openai", chain="anthropic,google",
               cooldown=1800, denylist=True):
    s = _live_settings()
    monkeypatch.setattr(s, "llm_fallback_enabled", True)
    monkeypatch.setattr(s, "llm_provider_denylist_enabled", denylist)
    monkeypatch.setattr(s, "llm_fallback_chain", chain)
    monkeypatch.setattr(s, "llm_provider_denylist_cooldown_seconds", cooldown)
    o = ConversationAgent.__new__(ConversationAgent)
    o._primary_provider = primary
    o._active_provider = primary
    o._tried_providers = {primary}
    o._denylisted = {}
    o._pending_denylist_notices = []
    o._bind_provider = lambda provider, model: setattr(o, "_active_provider", provider.lower())
    return o


def test_terminal_error_denylists_failovers_and_notifies(monkeypatch):
    o = _make_orch(monkeypatch)
    assert o._maybe_failover(Exception("401 unauthorized: invalid api key"), "u") is True
    assert o._is_denylisted("openai")          # the dead provider is out of the pool
    assert o._active_provider == "anthropic"   # failed over to the next healthy one
    notice = o._drain_denylist_notice()
    assert "openai" in notice and "anthropic" in notice
    assert o._drain_denylist_notice() == ""    # drained once


def test_transient_error_failovers_without_denylist_or_notice(monkeypatch):
    o = _make_orch(monkeypatch)
    assert o._maybe_failover(Exception("rate limit reached"), "u") is True
    assert not o._is_denylisted("openai")
    assert o._active_provider == "anthropic"
    assert o._drain_denylist_notice() == ""     # no user notice for transient


def test_failover_skips_already_denylisted_provider(monkeypatch):
    o = _make_orch(monkeypatch)
    o._denylist_provider("anthropic", "billing", "X")   # anthropic already out
    assert o._maybe_failover(Exception("401 invalid api key"), "u") is True
    assert o._active_provider == "google"               # skipped anthropic, used google


def test_no_healthy_fallback_still_notifies(monkeypatch):
    o = _make_orch(monkeypatch)
    o._denylist_provider("anthropic", "auth", "X")
    o._denylist_provider("google", "auth", "X")
    assert o._maybe_failover(Exception("402 payment required"), "u") is False
    assert o._is_denylisted("openai")
    assert "no other provider" in o._drain_denylist_notice().lower()


def test_reset_skips_denylisted_primary(monkeypatch):
    o = _make_orch(monkeypatch)
    o._denylist_provider("openai", "billing", "X")
    o._reset_to_primary_provider()
    assert o._active_provider == "anthropic"   # didn't snap back to the dead primary


def test_denylist_cooldown_expires(monkeypatch):
    o = _make_orch(monkeypatch, cooldown=1800)
    o._denylist_provider("openai", "auth", "X")
    assert o._is_denylisted("openai")
    # Backdate the entry past the cooldown → re-probed (and dropped).
    o._denylisted["openai"]["ts"] = time.time() - 10_000
    assert o._is_denylisted("openai") is False
    assert "openai" not in o._denylisted


def test_cooldown_zero_means_until_restart(monkeypatch):
    o = _make_orch(monkeypatch, cooldown=0)
    o._denylist_provider("openai", "auth", "X")
    o._denylisted["openai"]["ts"] = time.time() - 10_000
    assert o._is_denylisted("openai") is True   # never auto-expires when cooldown=0


def test_denylist_disabled_treats_terminal_as_transient(monkeypatch):
    o = _make_orch(monkeypatch, denylist=False)
    assert o._maybe_failover(Exception("401 invalid api key"), "u") is True
    assert not o._is_denylisted("openai")        # no denylist when the kill-switch is off
    assert o._drain_denylist_notice() == ""


def test_failover_disabled_is_noop(monkeypatch):
    o = _make_orch(monkeypatch)
    monkeypatch.setattr(_live_settings(), "llm_fallback_enabled", False)
    assert o._maybe_failover(Exception("401 invalid api key"), "u") is False
    assert not o._denylisted
    assert o._active_provider == "openai"
