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

        tracer = _get_tracer()
        if not tracer:
            return

        model = kwargs.get("invocation_params", {}).get("model_name") or \
                serialized.get("kwargs", {}).get("model", "unknown")
        provider = _infer_provider(serialized)

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

        # Extract token usage from response
        usage = {}
        if hasattr(response, "llm_output") and response.llm_output:
            usage = response.llm_output.get("token_usage", {})
        elif response.generations:
            gen = response.generations[0][0] if response.generations[0] else None
            if gen and hasattr(gen, "generation_info") and gen.generation_info:
                usage = gen.generation_info.get("token_usage", {})

        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        model = usage.get("model_name") or \
                (response.llm_output or {}).get("model_name", "unknown")

        # Record Prometheus metrics
        _record_metrics(model, input_tokens, output_tokens, elapsed)

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
        span = self._spans.pop(run_id, None)
        if span:
            span.set_attribute("error", True)
            span.set_attribute("error.message", str(error)[:500])
            span.end()

        # Record error metric
        _record_error_metric()


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
