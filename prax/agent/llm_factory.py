"""Factory helpers to create LangChain chat models for multiple providers."""
from __future__ import annotations

import logging
import threading
import time as _time

from langchain_anthropic import ChatAnthropic
from langchain_community.chat_models import ChatOllama
from langchain_core.language_models import BaseLanguageModel
from langchain_google_vertexai import ChatVertexAI
from langchain_openai import ChatOpenAI

from prax.settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tier choice ledger — every build_llm() call that resolves a tier records
# the choice here.  Callers (orchestrator, integration tests) drain the log
# to persist it in the execution trace.
# ---------------------------------------------------------------------------

_tier_choice_log: list[dict] = []
_tier_lock = threading.Lock()


def _record_tier_choice(
    *,
    tier_requested: str | None,
    tier_resolved: str | None,
    model: str,
    provider: str,
    span_id: str | None = None,
    span_name: str | None = None,
) -> dict:
    """Append a tier choice to the in-memory ledger and return it."""
    entry = {
        "ts": _time.time(),
        "tier_requested": tier_requested or "default",
        "tier_resolved": tier_resolved or tier_requested or "default",
        "model": model,
        "provider": provider,
        "span_id": span_id,
        "span_name": span_name,
    }
    with _tier_lock:
        _tier_choice_log.append(entry)
    return entry


def drain_tier_choices() -> list[dict]:
    """Return and clear all accumulated tier choice entries (thread-safe)."""
    with _tier_lock:
        entries = list(_tier_choice_log)
        _tier_choice_log.clear()
    return entries


def peek_tier_choices() -> list[dict]:
    """Return a snapshot without clearing — useful for diagnostics."""
    with _tier_lock:
        return list(_tier_choice_log)


def build_llm(
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    tier: str | None = None,
) -> BaseLanguageModel:
    """Return a configured LLM instance for the requested provider.

    Args:
        provider: LLM provider name (openai, anthropic, google, ollama, vllm).
        model: Explicit model name — takes precedence over *tier*.
        temperature: Sampling temperature.
        tier: Model tier (low, medium, high, pro).  Resolved to a concrete
              model name via :mod:`prax.agent.model_tiers`.  Ignored if
              *model* is explicitly provided.
    """
    provider_name = (provider or settings.default_llm_provider).lower()

    # Resolve model: explicit model > tier > BASE_MODEL
    resolved_tier = tier
    if model:
        model_name = model
    elif tier:
        from prax.agent.model_tiers import resolve_model
        model_name = resolve_model(tier)
    else:
        model_name = settings.base_model

    temp = temperature if temperature is not None else settings.agent_temperature

    logger.info("build_llm → provider=%s model=%s tier=%s temp=%s", provider_name, model_name, tier, temp)

    # Record tier choice for execution trace / A/B analysis
    span_id = None
    span_name = None
    try:
        from prax.agent.trace import get_current_trace
        ctx = get_current_trace()
        if ctx:
            span_id = ctx.span_id
            span_name = ctx.origin
    except Exception:
        pass

    _record_tier_choice(
        tier_requested=tier,
        tier_resolved=resolved_tier,
        model=model_name,
        provider=provider_name,
        span_id=span_id,
        span_name=span_name,
    )

    # Attach OTel callbacks for tracing and metrics on every LLM instance.
    try:
        from prax.observability.callbacks import get_otel_callbacks
        callbacks = get_otel_callbacks()
    except Exception:
        callbacks = []

    if provider_name == "openai":
        if not settings.openai_key:
            raise ValueError("OPENAI_KEY is required for OpenAI provider")
        return ChatOpenAI(model=model_name, api_key=settings.openai_key, temperature=temp, callbacks=callbacks)

    if provider_name == "anthropic":
        if not settings.anthropic_key:
            raise ValueError("ANTHROPIC_KEY is required for Anthropic provider")
        return ChatAnthropic(model=model_name, api_key=settings.anthropic_key, temperature=temp, callbacks=callbacks)

    if provider_name in {"google", "google-vertex"}:
        if not settings.google_vertex_project or not settings.google_vertex_location:
            raise ValueError("GOOGLE_VERTEX_PROJECT and GOOGLE_VERTEX_LOCATION are required for Vertex AI")
        return ChatVertexAI(
            model=model_name,
            temperature=temp,
            project=settings.google_vertex_project,
            location=settings.google_vertex_location,
            callbacks=callbacks,
        )

    if provider_name in {"ollama", "local"}:
        return ChatOllama(model=model_name, temperature=temp, callbacks=callbacks)

    if provider_name == "vllm":
        return ChatOpenAI(
            model=model_name,
            api_key="not-needed",
            base_url=settings.vllm_base_url,
            temperature=temp,
            callbacks=callbacks,
        )

    raise ValueError(f"Unsupported LLM provider: {provider}")
