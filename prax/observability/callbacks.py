"""LangChain callback handler that creates OpenTelemetry spans for LLM calls.

Provides LangSmith-like tracing: every LLM invocation gets an OTel span with
GenAI semantic convention attributes (model, tokens, latency).  Also records
Prometheus metrics for each call.

Usage::

    from prax.observability.callbacks import get_otel_callbacks

    llm = ChatOpenAI(model="gpt-5.4", callbacks=get_otel_callbacks())
"""
from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)


class OTelLLMCallback(BaseCallbackHandler):
    """Creates OTel spans and records Prometheus metrics for LLM calls."""

    def __init__(self):
        super().__init__()
        self._spans: dict[UUID, Any] = {}
        self._start_times: dict[UUID, float] = {}
        # Provider tracked per run_id at start so the circuit-breaker
        # attribution is correct on BOTH the success and error paths — the
        # error callback has no model/serialized to infer from.
        self._providers: dict[UUID, str] = {}

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._start_times[run_id] = time.monotonic()
        # Track provider for circuit-breaker attribution BEFORE the tracer
        # early-return — breaker accounting must work even when OTel tracing
        # is unconfigured (lite deployments, tests).
        self._providers[run_id] = _infer_provider(serialized)

        tracer = _get_tracer()
        if not tracer:
            return

        model = kwargs.get("invocation_params", {}).get("model_name") or \
                serialized.get("kwargs", {}).get("model", "unknown")

        span = tracer.start_span(
            name=f"llm.{model}",
            attributes={
                "gen_ai.system": "langchain",
                "gen_ai.request.model": model,
                "gen_ai.request.temperature": kwargs.get("invocation_params", {}).get("temperature", 0),
                "prax.prompt_count": len(prompts),
            },
        )
        self._spans[run_id] = span

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._start_times[run_id] = time.monotonic()
        provider = _infer_provider(serialized)
        # Track provider for circuit-breaker attribution BEFORE the tracer
        # early-return (see on_llm_start).
        self._providers[run_id] = provider

        tracer = _get_tracer()
        if not tracer:
            return

        model = kwargs.get("invocation_params", {}).get("model_name") or \
                serialized.get("kwargs", {}).get("model", "unknown")

        # Resolve tier from the factory's latest choice for this model
        tier = _infer_tier_for_model(model)

        span = tracer.start_span(
            name=f"chat.{model}",
            attributes={
                "gen_ai.system": provider,
                "gen_ai.request.model": model,
                "gen_ai.request.temperature": kwargs.get("invocation_params", {}).get("temperature", 0),
                "prax.message_count": sum(len(batch) for batch in messages),
                "prax.tier": tier,
            },
        )
        self._spans[run_id] = span

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        elapsed = time.monotonic() - self._start_times.pop(run_id, time.monotonic())

        # Extract token usage from response.
        #
        # `.get(key, default)` returns None when the key EXISTS with value
        # None — the default only covers an ABSENT key — and some providers
        # send an explicit `"token_usage": null`. That made `usage` None and
        # the next line raise, aborting this callback BEFORE both the
        # Prometheus metrics and the trace cost attribution below: the call's
        # accounting vanished silently, because LangChain swallows callback
        # errors. Hence the `or {}` on every extraction.
        # Three shapes carry usage, and which one arrives depends on the
        # provider and the LangChain integration version:
        #
        #   1. `AIMessage.usage_metadata`  {input_tokens, output_tokens}
        #      — the MODERN, provider-normalised shape. Checked FIRST.
        #   2. `llm_output["token_usage"]` {prompt_tokens, completion_tokens}
        #      — the legacy OpenAI-completions shape.
        #   3. `generation_info["token_usage"]` — same legacy keys, older path.
        #
        # The previous branch structure lost shape 1 entirely: `llm_output` is
        # commonly a truthy `{"model_name": ...}` with NO `token_usage`, so the
        # first branch matched, produced `{}`, and the `elif` never ran — a
        # call carrying 1200 in / 40 out was recorded as 0/0 while still
        # counting as an LLM call. Observed live as an orchestrator span
        # reporting 159 in / 24 out across a 12-tool-call turn, with every
        # `cost_estimate_usd` null.
        llm_output = getattr(response, "llm_output", None) or {}
        input_tokens, output_tokens = _usage_from_message(response)
        if not (input_tokens or output_tokens):
            usage = llm_output.get("token_usage") or {}
            if not usage and getattr(response, "generations", None):
                gen = response.generations[0][0] if response.generations[0] else None
                info = getattr(gen, "generation_info", None) or {}
                usage = info.get("token_usage") or {}
            input_tokens = usage.get("prompt_tokens") or 0
            output_tokens = usage.get("completion_tokens") or 0
            model = usage.get("model_name") or llm_output.get("model_name") or "unknown"
        else:
            model = llm_output.get("model_name") or _response_model(response) or "unknown"

        # Record Prometheus metrics
        _record_metrics(model, input_tokens, output_tokens, elapsed)

        # Attribute the usage to the active trace span, so the trace itself can
        # answer "what did this turn cost" — Prometheus aggregates can't be
        # joined back to one conversation after the fact.
        try:
            from prax.agent.trace import get_current_trace
            ctx = get_current_trace()
            if ctx:
                cached_in, cache_write = _cache_tokens_from_message(response)
                ctx.graph.add_llm_usage(ctx.span_id, model,
                                        input_tokens, output_tokens,
                                        cached_in, cache_write)
        except Exception:  # noqa: BLE001 - accounting must never break a call
            pass

        # Circuit breaker: record success.  Prefer the provider captured at
        # start; fall back to model-name inference for older call paths.
        try:
            from prax.agent.circuit_breaker import get_breaker
            provider = self._providers.pop(run_id, None) or _infer_provider_from_model(model)
            get_breaker(f"llm:{provider}").record_success()
        except Exception:
            pass

        # Complete OTel span
        span = self._spans.pop(run_id, None)
        if span:
            span.set_attribute("gen_ai.response.model", model)
            span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
            span.set_attribute("prax.duration_seconds", elapsed)
            span.end()

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._start_times.pop(run_id, None)
        # Provider captured at start — the only reliable attribution on the
        # error path (no model/serialized is passed to this callback).
        provider = self._providers.pop(run_id, None)
        span = self._spans.pop(run_id, None)
        if span:
            span.set_attribute("error", True)
            span.set_attribute("error.message", str(error)[:500])
            span.end()

        # Record error metric
        _record_error_metric()

        # Circuit breaker: record failure against the REAL provider so each
        # provider's breaker reflects its own failure rate (previously this
        # always blamed 'openai').
        try:
            from prax.agent.circuit_breaker import get_breaker
            get_breaker(f"llm:{provider or 'unknown'}").record_failure()
        except Exception:
            pass


class OTelToolCallback(BaseCallbackHandler):
    """Creates OTel spans for tool invocations."""

    def __init__(self):
        super().__init__()
        self._spans: dict[UUID, Any] = {}
        self._start_times: dict[UUID, float] = {}

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._start_times[run_id] = time.monotonic()
        tool_name = serialized.get("name", "unknown_tool")

        tracer = _get_tracer()
        if tracer:
            span = tracer.start_span(
                name=f"tool.{tool_name}",
                attributes={
                    "prax.tool.name": tool_name,
                    "prax.tool.input_preview": input_str[:200],
                },
            )
            self._spans[run_id] = span

        _record_tool_metric(tool_name)

    def on_tool_end(
        self,
        output: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._start_times.pop(run_id, None)
        span = self._spans.pop(run_id, None)
        if span:
            span.set_attribute("prax.tool.output_preview", (output or "")[:200])
            span.end()

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._start_times.pop(run_id, None)
        span = self._spans.pop(run_id, None)
        if span:
            span.set_attribute("error", True)
            span.set_attribute("error.message", str(error)[:500])
            span.end()


def _get_tracer():
    from prax.observability.setup import get_tracer
    return get_tracer()


def _infer_tier_for_model(model: str) -> str:
    """Look up the tier that resolved to *model* from the factory's log."""
    try:
        from prax.agent.llm_factory import peek_tier_choices
        for entry in reversed(peek_tier_choices()):
            if entry["model"] == model:
                return entry.get("tier_requested") or "default"
    except Exception:
        pass
    return "unknown"


def _first_message(response: Any):
    """The AIMessage of the first generation, or None."""
    gens = getattr(response, "generations", None)
    if not gens or not gens[0]:
        return None
    return getattr(gens[0][0], "message", None)


def _usage_from_message(response: Any) -> tuple[int, int]:
    """Read ``AIMessage.usage_metadata`` — the provider-normalised shape.

    Returns ``(0, 0)`` when absent, so callers can fall back to the legacy
    ``token_usage`` dicts. Never raises: accounting must not break a call.
    """
    try:
        meta = getattr(_first_message(response), "usage_metadata", None) or {}
        return int(meta.get("input_tokens") or 0), int(meta.get("output_tokens") or 0)
    except Exception:  # noqa: BLE001 - accounting is best-effort
        return 0, 0


def _cache_tokens_from_message(response: Any) -> tuple[int, int]:
    """``(cache_read, cache_creation)`` input tokens, or ``(0, 0)``.

    An agent turn is a loop of model calls that each re-send the system prompt,
    the tool definitions and the whole accumulated conversation — so most input
    tokens are a re-sent prefix, and whether the provider served them from cache
    is the single largest cost lever there is. `input_tokens` alone cannot tell
    you: a cached prefix and an uncached one look identical in that number.

    Reads the LangChain-normalised `input_token_details`, which both the
    Anthropic and OpenAI integrations populate. Best-effort by design — a
    provider that reports nothing yields zeros and is indistinguishable from
    "no caching", which is why the flag that consumes this must be judged on a
    measured delta rather than on these fields being non-zero.
    """
    try:
        meta = getattr(_first_message(response), "usage_metadata", None) or {}
        details = meta.get("input_token_details") or {}
        return (int(details.get("cache_read") or 0),
                int(details.get("cache_creation") or 0))
    except Exception:  # noqa: BLE001 - accounting is best-effort
        return 0, 0


def _response_model(response: Any) -> str:
    """Model name from response metadata, when the shape carries one."""
    try:
        meta = getattr(_first_message(response), "response_metadata", None) or {}
        return str(meta.get("model_name") or meta.get("model") or "")
    except Exception:  # noqa: BLE001
        return ""


def _infer_provider(serialized: dict) -> str:
    """Infer the LLM provider from the serialized class hierarchy."""
    class_id = serialized.get("id", [])
    class_str = ".".join(class_id).lower() if class_id else ""
    if "anthropic" in class_str:
        return "anthropic"
    if "google" in class_str or "vertex" in class_str:
        return "google"
    if "ollama" in class_str:
        return "ollama"
    return "openai"


def _infer_provider_from_model(model: str) -> str:
    """Best-effort provider inference from model name."""
    m = model.lower()
    if "claude" in m or "anthropic" in m:
        return "anthropic"
    if "gemini" in m:
        return "google"
    if "qwen" in m or "llama" in m or "mistral" in m:
        return "ollama"
    return "openai"


def _record_metrics(model: str, input_tokens: int, output_tokens: int, duration: float) -> None:
    """Record Prometheus metrics for an LLM call."""
    try:
        from prax.observability.metrics import (
            LLM_CALLS,
            LLM_DURATION,
            LLM_TOKENS,
        )
        LLM_CALLS.labels(model=model, status="success").inc()
        if input_tokens:
            LLM_TOKENS.labels(model=model, type="input").inc(input_tokens)
        if output_tokens:
            LLM_TOKENS.labels(model=model, type="output").inc(output_tokens)
        LLM_DURATION.labels(model=model).observe(duration)
    except Exception:
        pass  # Metrics not available


def _record_error_metric() -> None:
    try:
        from prax.observability.metrics import LLM_CALLS
        LLM_CALLS.labels(model="unknown", status="error").inc()
    except Exception:
        pass


def _record_tool_metric(tool_name: str) -> None:
    try:
        from prax.observability.metrics import TOOL_CALLS
        TOOL_CALLS.labels(tool=tool_name).inc()
    except Exception:
        pass


# Singleton callbacks — reused across all LLM instances
_llm_callback = None
_tool_callback = None


def get_otel_callbacks() -> list[BaseCallbackHandler]:
    """Return the singleton OTel callback handlers for LLM + tool tracing."""
    global _llm_callback, _tool_callback
    if _llm_callback is None:
        _llm_callback = OTelLLMCallback()
    if _tool_callback is None:
        _tool_callback = OTelToolCallback()
    return [_llm_callback, _tool_callback]
