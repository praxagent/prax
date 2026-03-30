"""Observability package — OpenTelemetry traces, Prometheus metrics, LLM callbacks."""

from prax.observability.setup import get_tracer, init_observability

__all__ = ["init_observability", "get_tracer"]
