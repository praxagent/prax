"""Factory helpers to create LangChain chat models for multiple providers."""
from __future__ import annotations

import logging

from langchain_anthropic import ChatAnthropic
from langchain_community.chat_models import ChatOllama
from langchain_core.language_models import BaseLanguageModel
from langchain_google_vertexai import ChatVertexAI
from langchain_openai import ChatOpenAI

from prax.settings import settings

logger = logging.getLogger(__name__)


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
    if model:
        model_name = model
    elif tier:
        from prax.agent.model_tiers import resolve_model
        model_name = resolve_model(tier)
    else:
        model_name = settings.base_model

    temp = temperature if temperature is not None else settings.agent_temperature

    logger.info("build_llm → provider=%s model=%s tier=%s temp=%s", provider_name, model_name, tier, temp)

    if provider_name == "openai":
        if not settings.openai_key:
            raise ValueError("OPENAI_KEY is required for OpenAI provider")
        return ChatOpenAI(model=model_name, api_key=settings.openai_key, temperature=temp)

    if provider_name == "anthropic":
        if not settings.anthropic_key:
            raise ValueError("ANTHROPIC_KEY is required for Anthropic provider")
        return ChatAnthropic(model=model_name, api_key=settings.anthropic_key, temperature=temp)

    if provider_name in {"google", "google-vertex"}:
        if not settings.google_vertex_project or not settings.google_vertex_location:
            raise ValueError("GOOGLE_VERTEX_PROJECT and GOOGLE_VERTEX_LOCATION are required for Vertex AI")
        return ChatVertexAI(
            model=model_name,
            temperature=temp,
            project=settings.google_vertex_project,
            location=settings.google_vertex_location,
        )

    if provider_name in {"ollama", "local"}:
        return ChatOllama(model=model_name, temperature=temp)

    if provider_name == "vllm":
        return ChatOpenAI(
            model=model_name,
            api_key="not-needed",
            base_url=settings.vllm_base_url,
            temperature=temp,
        )

    raise ValueError(f"Unsupported LLM provider: {provider}")
