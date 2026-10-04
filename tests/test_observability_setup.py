"""Tracing setup (prax/observability/setup.py): where spans go, and when the
collector counts as reachable.

Production (native deploy) set OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
in .env, but the code read os.environ, which never sees .env values, so spans
went to the compose default http://tempo:4318 through HTTP_PROXY. The secrets
proxy answered 502 for a name it couldn't resolve, the probe counted any HTTP
error as "up", and every export failed.
"""
from __future__ import annotations

import io
import urllib.error

import pytest

from prax.observability import setup as obs


def _live():
    import prax.settings
    return prax.settings.settings


@pytest.fixture
def otel(monkeypatch):
    """Fakes for the exporter and the global provider; records what was set up."""
    seen = {"probe": None, "exporter": None, "provider": None}
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http import trace_exporter

    class FakeExporter:
        def __init__(self, endpoint=None, **kw):
            seen["exporter"] = endpoint

        def shutdown(self):
            pass

    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", FakeExporter)
    monkeypatch.setattr(trace, "set_tracer_provider", lambda p: seen.__setitem__("provider", p))
    monkeypatch.setattr(obs, "_tracer", None)
    return seen


def _probe_answers(monkeypatch, seen, outcome):
    def fake_urlopen(url, timeout=None):
        seen["probe"] = url
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(b"")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def _http_error(code):
    return urllib.error.HTTPError("http://x/v1/traces", code, "msg", {}, None)


def test_the_endpoint_set_in_dotenv_is_used(otel, monkeypatch):
    monkeypatch.setattr(_live(), "otel_exporter_otlp_endpoint", "http://127.0.0.1:4318/")
    _probe_answers(monkeypatch, otel, _http_error(405))      # Tempo: GET not allowed
    obs.init_observability()
    assert otel["probe"] == "http://127.0.0.1:4318/v1/traces"
    assert otel["exporter"] == "http://127.0.0.1:4318/v1/traces"
    assert otel["provider"] is not None


def test_the_compose_default_when_nothing_is_set(otel, monkeypatch):
    monkeypatch.setattr(_live(), "otel_exporter_otlp_endpoint", "")
    _probe_answers(monkeypatch, otel, _http_error(405))
    obs.init_observability()
    assert otel["exporter"] == "http://tempo:4318/v1/traces"


@pytest.mark.parametrize("code", [502, 503, 504])
def test_a_proxys_gateway_error_means_unreachable(otel, monkeypatch, caplog, code):
    monkeypatch.setattr(_live(), "otel_exporter_otlp_endpoint", "http://tempo:4318")
    _probe_answers(monkeypatch, otel, _http_error(code))
    obs.init_observability()
    assert otel["exporter"] is None and otel["provider"] is None
    assert "Tracing disabled" in caplog.text and "NO_PROXY" in caplog.text


@pytest.mark.parametrize("code", [400, 404, 405, 415])
def test_the_collector_answering_at_all_means_it_is_up(otel, monkeypatch, code):
    monkeypatch.setattr(_live(), "otel_exporter_otlp_endpoint", "http://127.0.0.1:4318")
    _probe_answers(monkeypatch, otel, _http_error(code))
    obs.init_observability()
    assert otel["exporter"] == "http://127.0.0.1:4318/v1/traces"


def test_no_answer_at_all_disables_tracing(otel, monkeypatch):
    monkeypatch.setattr(_live(), "otel_exporter_otlp_endpoint", "http://127.0.0.1:4318")
    _probe_answers(monkeypatch, otel, urllib.error.URLError("connection refused"))
    obs.init_observability()
    assert otel["exporter"] is None
