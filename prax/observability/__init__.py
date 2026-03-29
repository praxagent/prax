"""Observability package — OpenTelemetry traces, Prometheus metrics, LLM callbacks."""

from prax.observability.setup import init_observability, get_tracer

__all__ = ["init_observability", "get_tracer"]
