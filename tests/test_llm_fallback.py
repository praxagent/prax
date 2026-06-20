"""Tests for cross-provider LLM failover (prax.agent.llm_fallback) and the
circuit-breaker provider-attribution fix in the OTel callback."""
from __future__ import annotations

import uuid

import pytest

from prax.agent import llm_fallback
from prax.agent.llm_fallback import (
    get_fallback_providers,
    is_provider_error,
    parse_fallback_chain,
)

# --------------------------------------------------------------------------- #
# is_provider_error
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("exc", [
    ConnectionError("Circuit breaker OPEN for LLM provider 'openai'"),
    TimeoutError("request timed out"),
    RuntimeError("429 rate limit exceeded"),
    RuntimeError("upstream overloaded, please retry"),
    RuntimeError("503 Service Unavailable"),
    RuntimeError("Anthropic API connection error"),
])
def test_is_provider_error_true(exc):
    assert is_provider_error(exc) is True


@pytest.mark.parametrize("exc", [
    ValueError("invalid tool argument: missing 'path'"),
    KeyError("foo"),
    RuntimeError("the model produced malformed JSON"),
])
def test_is_provider_error_false(exc):
    assert is_provider_error(exc) is False


def test_is_provider_error_by_type_name():
    class RateLimitError(Exception):
        pass

    assert is_provider_error(RateLimitError("slow down")) is True


# --------------------------------------------------------------------------- #
# parse_fallback_chain
# --------------------------------------------------------------------------- #

def test_parse_fallback_chain_with_models():
    chain = parse_fallback_chain("anthropic:claude-sonnet-4-20250514, google:gemini-2.5-pro")
    assert chain == [
        {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
        {"provider": "google", "model": "gemini-2.5-pro"},
    ]


def test_parse_fallback_chain_without_models_and_blanks():
    chain = parse_fallback_chain("anthropic, , google:")
    assert chain == [
        {"provider": "anthropic", "model": None},
        {"provider": "google", "model": None},
    ]


# --------------------------------------------------------------------------- #
# get_fallback_providers
# --------------------------------------------------------------------------- #

def test_get_fallback_providers_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(llm_fallback.settings, "llm_fallback_enabled", False)
    monkeypatch.setattr(llm_fallback.settings, "llm_fallback_chain", "anthropic:x")
    assert get_fallback_providers("openai") == []


def test_get_fallback_providers_excludes_primary(monkeypatch):
    monkeypatch.setattr(llm_fallback.settings, "llm_fallback_enabled", True)
    monkeypatch.setattr(
        llm_fallback.settings, "llm_fallback_chain",
        "openai:gpt-x, anthropic:claude-y, google:gemini-z",
    )
    out = get_fallback_providers("openai")
    assert [e["provider"] for e in out] == ["anthropic", "google"]


def test_get_fallback_providers_auto_derives_and_dedupes(monkeypatch):
    monkeypatch.setattr(llm_fallback.settings, "llm_fallback_enabled", True)
    monkeypatch.setattr(llm_fallback.settings, "llm_fallback_chain", "")
    monkeypatch.setattr(
        "prax.agent.multi_model._available_providers",
        lambda: [
            {"provider": "openai", "model": "gpt-x"},
            {"provider": "anthropic", "model": "claude-y"},
            {"provider": "anthropic", "model": "dup"},
        ],
    )
    out = get_fallback_providers("openai")
    assert out == [{"provider": "anthropic", "model": "claude-y"}]


# --------------------------------------------------------------------------- #
# Circuit-breaker provider attribution on the OTel callback
# --------------------------------------------------------------------------- #

def test_on_llm_error_attributes_to_real_provider():
    """A failure on a non-OpenAI provider must trip THAT provider's breaker,
    not OpenAI's (the bug this fix addresses)."""
    from prax.agent.circuit_breaker import get_breaker
    from prax.observability.callbacks import OTelLLMCallback

    cb = OTelLLMCallback()
    run_id = uuid.uuid4()

    anthropic_before = get_breaker("llm:anthropic").failure_count
    openai_before = get_breaker("llm:openai").failure_count

    cb.on_chat_model_start(
        {"id": ["langchain", "chat_models", "anthropic", "ChatAnthropic"]},
        [[]],
        run_id=run_id,
    )
    cb.on_llm_error(RuntimeError("overloaded"), run_id=run_id)

    assert get_breaker("llm:anthropic").failure_count == anthropic_before + 1
    # OpenAI's breaker must be untouched.
    assert get_breaker("llm:openai").failure_count == openai_before


# --------------------------------------------------------------------------- #
# Orchestrator failover + recovery-context injection (integration via the real
# _invoke_with_retry, with the LLM/graph stubbed out)
# --------------------------------------------------------------------------- #

def _bare_agent():
    """A ConversationAgent with __init__ bypassed and just the failover state."""
    from prax.agent.checkpoint import CheckpointManager
    from prax.agent.orchestrator import ConversationAgent

    agent = object.__new__(ConversationAgent)
    agent._primary_provider = "openai"
    agent._active_provider = "openai"
    agent._tried_providers = {"openai"}
    agent._orchestrator_tier = "medium"
    agent.tools = []
    agent.checkpoint_mgr = CheckpointManager()
    return agent


def test_maybe_failover_binds_next_provider(monkeypatch):
    monkeypatch.setattr(llm_fallback.settings, "llm_fallback_enabled", True)
    monkeypatch.setattr(llm_fallback.settings, "llm_fallback_chain", "anthropic:claude-x")
    agent = _bare_agent()
    bound = []
    monkeypatch.setattr(type(agent), "_bind_provider",
                        lambda self, p, m: (bound.append((p, m)), setattr(self, "_active_provider", p)))

    assert agent._maybe_failover(ConnectionError("Circuit breaker OPEN"), "u1") is True
    assert bound == [("anthropic", "claude-x")]
    assert agent._active_provider == "anthropic"


def test_maybe_failover_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(llm_fallback.settings, "llm_fallback_enabled", False)
    agent = _bare_agent()
    assert agent._maybe_failover(ConnectionError("overloaded"), "u1") is False


def test_maybe_failover_ignores_non_provider_error(monkeypatch):
    monkeypatch.setattr(llm_fallback.settings, "llm_fallback_enabled", True)
    monkeypatch.setattr(llm_fallback.settings, "llm_fallback_chain", "anthropic:claude-x")
    agent = _bare_agent()
    assert agent._maybe_failover(ValueError("bad tool arg"), "u1") is False


def test_invoke_with_retry_fails_over_and_injects_recovery(monkeypatch):
    """First attempt hits a provider error → fail over to the next provider,
    retry on a clean thread, and the retry must see the recovery guidance."""
    from langchain_core.messages import HumanMessage

    import prax.agent.orchestrator as orch_mod
    monkeypatch.setattr(orch_mod.settings, "llm_fallback_enabled", True)
    monkeypatch.setattr(orch_mod.settings, "llm_fallback_chain", "anthropic:claude-x")
    monkeypatch.setattr(orch_mod.settings, "recovery_context_injection_enabled", True)

    agent = _bare_agent()
    agent._plugin_version = 0
    agent.checkpoint_mgr.start_turn("u1")
    monkeypatch.setattr(type(agent), "_bind_provider",
                        lambda self, p, m: setattr(self, "_active_provider", p))
    monkeypatch.setattr(type(agent), "_rebuild_if_needed", lambda self: None)

    calls: list[list] = []

    def fake_invoke_once(_self, messages, config, user_id, heartbeat=None):
        calls.append(list(messages))
        if len(calls) == 1:
            raise ConnectionError("Circuit breaker OPEN for provider 'openai'")
        return {"messages": [HumanMessage(content="ok")]}

    monkeypatch.setattr(type(agent), "_invoke_graph_once", fake_invoke_once)

    result = agent._invoke_with_retry([HumanMessage(content="hi")], {"callbacks": []}, "u1")

    assert result == {"messages": [HumanMessage(content="ok")]}
    assert len(calls) == 2  # failed once, succeeded on the fallback
    assert agent._active_provider == "anthropic"
    # The retry attempt must carry the injected recovery guidance.
    second = calls[1]
    assert any(
        isinstance(m, HumanMessage) and "recovery guidance" in m.content
        for m in second
    )
