"""OpenTelemetry SDK initialization.

Configures a TracerProvider that exports spans via OTLP/HTTP to Tempo.
Degrades gracefully when OTel packages are not installed or the collector
is unreachable — Prax keeps running, just without distributed traces.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_tracer = None  # lazily set by init_observability()


def init_observability(service_name: str = "prax") -> None:
    """Initialize OpenTelemetry tracing with OTLP exporter.

    Call once at application startup.  If the OTel SDK is not installed,
    logs a warning and returns silently.
    """
    global _tracer

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.info("OpenTelemetry SDK not installed — tracing disabled")
        return

    endpoint = os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "http://tempo:4318"
    )

    resource = Resource.create({
        "service.name": service_name,
        "service.version": os.environ.get("PRAX_VERSION", "dev"),
    })

    # Probe the endpoint before committing to the exporter.  If Tempo isn't
    # running (e.g. observability profile not active), skip initialization so
    # the BatchSpanProcessor doesn't queue spans to a dead host and OOM.
    import urllib.error
    import urllib.request
    try:
        probe_url = endpoint.rstrip("/")
        urllib.request.urlopen(f"{probe_url}/v1/traces", timeout=3)
    except urllib.error.HTTPError:
        pass  # 4xx/5xx means the endpoint is up — just rejecting empty POSTs
    except Exception:
        logger.warning(
            "Tempo endpoint unreachable at %s — tracing disabled. "
            "Start the observability profile: docker compose --profile observability up",
            endpoint,
        )
        return

    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
    # Cap the queue so a slow/dead exporter can't accumulate unbounded memory.
    provider.add_span_processor(BatchSpanProcessor(
        exporter,
        max_queue_size=2048,
        max_export_batch_size=512,
    ))

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)

    logger.info("OpenTelemetry initialized — exporting to %s", endpoint)


def get_tracer():
    """Return the OTel tracer, or None if not initialized."""
    return _tracer


def shutdown() -> None:
    """Flush and shut down the tracer provider."""
    try:
        from opentelemetry import trace
        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()
    except Exception:
        pass
