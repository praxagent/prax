"""Factory helpers to create LangChain chat models for multiple providers."""
from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_community.chat_models import ChatOllama
from langchain_core.language_models import BaseLanguageModel
from langchain_google_vertexai import ChatVertexAI
from langchain_openai import ChatOpenAI

from prax.settings import settings


def build_llm(
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> BaseLanguageModel:
    """Return a configured LLM instance for the requested provider."""

    provider_name = (provider or settings.default_llm_provider).lower()
    model_name = model or settings.base_model
    temp = temperature if temperature is not None else settings.agent_temperature

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
