"""Factory helpers to create LangChain chat models for multiple providers."""
from __future__ import annotations

import logging
import threading
import time as _time

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseLanguageModel
from langchain_google_vertexai import ChatVertexAI
from langchain_ollama import ChatOllama
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
    config_key: str | None = None,
    default_tier: str | None = None,
) -> BaseLanguageModel:
    """Return a configured LLM instance for the requested provider.

    Args:
        provider: LLM provider name (openai, anthropic, google, ollama, vllm).
        model: Explicit model name — takes precedence over *tier*.
        temperature: Sampling temperature.
        tier: Model tier (low, medium, high, pro).  Resolved to a concrete
              model name via :mod:`prax.agent.model_tiers`.  Ignored if
              *model* is explicitly provided.
        config_key: Per-component routing key (e.g., ``"context_compaction"``,
              ``"memory_compact"``, ``"session_classifier"``).  Looks up the
              matching entry in the LLM routing config and uses its
              provider/model/tier/temperature values.  Explicit args
              passed alongside take precedence over the config.
        default_tier: Fallback tier when ``config_key`` doesn't specify one
              and neither ``tier`` nor ``model`` is set.
    """
    # Resolve per-component config if a routing key was supplied.
    # Explicit args passed by the caller still win over the config.
    if config_key:
        try:
            from prax.plugins.llm_config import get_component_config
            cfg = get_component_config(config_key) or {}
        except Exception:
            cfg = {}
        if provider is None:
            provider = cfg.get("provider")
        if model is None:
            model = cfg.get("model")
        if temperature is None:
            temperature = cfg.get("temperature")
        if tier is None:
            tier = cfg.get("tier")

    # Fall back to default_tier if still unresolved.
    if tier is None and model is None and default_tier is not None:
        tier = default_tier

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

    # Circuit breaker: fail fast if this provider has been tripping.
    try:
        from prax.agent.circuit_breaker import get_breaker
        breaker = get_breaker(f"llm:{provider_name}")
        if not breaker.is_allowed():
            raise ConnectionError(
                f"Circuit breaker OPEN for LLM provider '{provider_name}' — "
                f"too many recent failures. Will retry automatically in "
                f"{breaker.recovery_timeout:.0f}s."
            )
    except (ConnectionError, ImportError):
        raise
    except Exception:
        pass  # Breaker system failure should never block LLM usage

    if provider_name == "openai":
        if not settings.openai_key:
            raise ValueError("OPENAI_KEY is required for OpenAI provider")
        # The "pro"/reasoning models route through OpenAI's **Responses API**,
        # which (a) rejects `logprobs`/`top_logprobs` — raising
        # `Responses.create() got an unexpected keyword argument 'logprobs'` and
        # crashing the spoke (this is what killed the gpt-5.4-pro professor) — and
        # (b) only accepts the default temperature. Detect them and skip both the
        # logprobs entropy machinery and the custom temperature.
        _ml = (model_name or "").lower()
        # Reasoning / "pro" models reject `logprobs` (a hard 400) and a custom
        # temperature. VERIFIED rejecters: any `-pro` (gpt-5.4-pro, gpt-5.5-pro),
        # the `o*` series, and **gpt-5.5** (full) — `gpt-5.5` + logprobs returns
        # `400 'logprobs' is not supported with this model`. gpt-5.4 and below
        # (nano/mini/full) DO support logprobs. NOTE: this is a name denylist; the
        # robust long-term fix is to catch the 400 and auto-disable logprobs for
        # the offending model (see model-routing.md §14).
        _responses_api = (
            ("-pro" in _ml)
            or _ml.startswith(("o1", "o3", "o4"))
            or _ml.startswith("gpt-5.5")
        )
        # A NARROWER set actually REQUIRES the /v1/responses endpoint (they 404 on
        # chat-completions): the "-pro" reasoning models and the o-series. Plain
        # gpt-5.5 (full) works fine on chat-completions — it only rejects logprobs
        # + a custom temperature. Routing gpt-5.5 through the Responses API made it
        # return LIST content-blocks, which crashed str-only downstream code (e.g.
        # the note pipeline: 'list' object has no attribute 'lower'). So only send
        # the true responses-only models to that endpoint.
        _needs_responses_endpoint = (
            ("-pro" in _ml) or _ml.startswith(("o1", "o3", "o4"))
        )
        # Phase 3: logprobs for entropy analysis — Chat Completions models only.
        # The LogprobCallbackHandler silently no-ops if there's no logprob data.
        if not _responses_api:
            try:
                from prax.agent.logprob_analyzer import get_logprob_callback
                callbacks = list(callbacks) + [get_logprob_callback()]
            except Exception:
                pass
        return ChatOpenAI(
            model=model_name,
            api_key=settings.openai_key,
            temperature=(1.0 if _responses_api else temp),
            callbacks=callbacks,
            # Per-request HTTP timeout — prevents a stalled OpenAI
            # connection from hanging the orchestrator forever.  Without
            # this, langchain_openai's default is no timeout, and a
            # hung POST /v1/chat/completions blocks the agent turn even
            # though agent_run_timeout exists (that timeout is only
            # checked AFTER graph.invoke returns).
            timeout=settings.llm_request_timeout,
            model_kwargs=({} if _responses_api else {"logprobs": True, "top_logprobs": 5}),
            # Reasoning/"pro" models (gpt-5.5-pro, o-series) are served ONLY by the
            # Responses API — calling them on /v1/chat/completions 404s with "not a
            # chat model" (this is what failed the professor spoke). Route ONLY
            # those through /v1/responses; plain gpt-5.5 and chat models keep
            # chat-completions (string content, no list-block downstream crashes).
            use_responses_api=_needs_responses_endpoint,
        )

    if provider_name == "anthropic":
        if not settings.anthropic_key:
            raise ValueError("ANTHROPIC_KEY is required for Anthropic provider")
        return ChatAnthropic(
            model=model_name, api_key=settings.anthropic_key,
            temperature=temp, callbacks=callbacks,
            timeout=settings.llm_request_timeout,
        )

    if provider_name in {"google", "google-vertex"}:
        if not settings.google_vertex_project or not settings.google_vertex_location:
            raise ValueError("GOOGLE_VERTEX_PROJECT and GOOGLE_VERTEX_LOCATION are required for Vertex AI")
        return ChatVertexAI(
            model=model_name,
            temperature=temp,
            project=settings.google_vertex_project,
            location=settings.google_vertex_location,
            callbacks=callbacks,
            # langchain-google-vertexai 3.x renamed request_timeout -> timeout;
            # the old kwarg was silently swallowed, so no timeout applied.
            timeout=settings.llm_request_timeout,
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
            timeout=settings.llm_request_timeout,
        )

    raise ValueError(f"Unsupported LLM provider: {provider}")
