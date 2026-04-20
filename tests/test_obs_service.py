"""Tests for obs_service + obs_tools — LGTM query wrappers.

Contract:
- When the relevant URL setting is empty, every query returns
  ``status=not_available`` without touching the network.
- When the URL is set, HTTP errors become ``status=error`` results,
  not exceptions.
- Result size is capped per the SWE-agent pattern.
- ``build_obs_tools()`` returns [] in lite deployment (observability
  disabled) so the agent never sees the tools.
"""
from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

from prax.services import obs_service


@pytest.fixture
def full_mode(monkeypatch):
    """Simulate full-compose deployment with all three datasources set."""
    from prax.settings import settings
    monkeypatch.setattr(settings, "observability_enabled", True)
    monkeypatch.setattr(settings, "loki_url", "http://loki:3100")
    monkeypatch.setattr(settings, "prometheus_url", "http://prometheus:9090")
    monkeypatch.setattr(settings, "tempo_url", "http://tempo:3200")
    return settings


@pytest.fixture
def lite_mode(monkeypatch):
    """Simulate lite deployment — no LGTM, everything empty."""
    from prax.settings import settings
    monkeypatch.setattr(settings, "observability_enabled", False)
    monkeypatch.setattr(settings, "loki_url", "")
    monkeypatch.setattr(settings, "prometheus_url", "")
    monkeypatch.setattr(settings, "tempo_url", "")
    return settings


class TestLiteModeDegradation:
    def test_is_available_false_in_lite(self, lite_mode):
        assert obs_service.is_available() is False

    def test_query_logs_lite_returns_not_available(self, lite_mode):
        result = obs_service.query_logs('{job="prax"}')
        assert result["status"] == "not_available"
        assert "Loki" in result["message"]

    def test_query_metrics_lite_returns_not_available(self, lite_mode):
        result = obs_service.query_metrics("up")
        assert result["status"] == "not_available"
        assert "Prometheus" in result["message"]

    def test_query_traces_lite_returns_not_available(self, lite_mode):
        result = obs_service.query_traces("{}")
        assert result["status"] == "not_available"
        assert "Tempo" in result["message"]


class TestLokiQuery:
    def test_parses_loki_response(self, full_mode, monkeypatch):
        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "data": {
                "result": [
                    {
                        "stream": {"job": "prax", "level": "ERROR"},
                        "values": [
                            ["1700000000000000000", "oops"],
                            ["1700000001000000000", "retrying"],
                        ],
                    },
                ],
            },
        }
        fake_resp.raise_for_status = lambda: None
        monkeypatch.setattr(obs_service.requests, "get", lambda *a, **kw: fake_resp)
        result = obs_service.query_logs('{job="prax"}')
        assert result["status"] == "ok"
        assert result["matched"] == 2
        assert result["entries"][0]["line"] == "oops"
        assert result["entries"][0]["labels"]["level"] == "ERROR"

    def test_loki_http_error_becomes_structured_error(self, full_mode, monkeypatch):
        import requests
        def raise_err(*a, **kw):
            raise requests.ConnectionError("connection refused")
        monkeypatch.setattr(obs_service.requests, "get", raise_err)
        result = obs_service.query_logs('{job="prax"}')
        assert result["status"] == "error"
        assert "connection refused" in result["error"]

    def test_loki_limit_capped_at_max(self, full_mode, monkeypatch):
        captured = {}
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"data": {"result": []}}
        fake_resp.raise_for_status = lambda: None

        def capture(url, params=None, timeout=None):
            captured["params"] = params
            return fake_resp

        monkeypatch.setattr(obs_service.requests, "get", capture)
        obs_service.query_logs('{job="prax"}', limit=99999)
        assert int(captured["params"]["limit"]) == obs_service.MAX_LOG_ENTRIES


class TestPrometheusQuery:
    def test_parses_prometheus_response(self, full_mode, monkeypatch):
        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "data": {
                "result": [
                    {
                        "metric": {"__name__": "up", "job": "prax"},
                        "values": [[1700000000, "1"], [1700000030, "1"]],
                    },
                ],
            },
        }
        fake_resp.raise_for_status = lambda: None
        monkeypatch.setattr(obs_service.requests, "get", lambda *a, **kw: fake_resp)
        result = obs_service.query_metrics("up")
        assert result["status"] == "ok"
        assert len(result["series"]) == 1
        assert result["series"][0]["metric"]["job"] == "prax"
        assert len(result["series"][0]["points"]) == 2


class TestTempoQuery:
    def test_trace_id_shortcut(self, full_mode, monkeypatch):
        captured_urls = []
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"batches": []}
        fake_resp.raise_for_status = lambda: None

        def capture(url, params=None, timeout=None):
            captured_urls.append(url)
            return fake_resp

        monkeypatch.setattr(obs_service.requests, "get", capture)
        trace_id = "a" * 32
        result = obs_service.query_traces(trace_id)
        assert result["status"] == "ok"
        assert any(f"/api/traces/{trace_id}" in u for u in captured_urls)

    def test_search_query_hits_search_endpoint(self, full_mode, monkeypatch):
        captured_urls = []
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"traces": []}
        fake_resp.raise_for_status = lambda: None

        def capture(url, params=None, timeout=None):
            captured_urls.append(url)
            return fake_resp

        monkeypatch.setattr(obs_service.requests, "get", capture)
        obs_service.query_traces('{duration > 1s}')
        assert any("/api/search" in u for u in captured_urls)


class TestBuildObsTools:
    def test_lite_mode_returns_no_tools(self, lite_mode):
        module = importlib.reload(importlib.import_module("prax.agent.obs_tools"))
        assert module.build_obs_tools() == []

    def test_full_mode_registers_three_tools(self, full_mode):
        module = importlib.reload(importlib.import_module("prax.agent.obs_tools"))
        tools = module.build_obs_tools()
        names = {t.name for t in tools}
        assert names == {"obs_query_logs", "obs_query_metrics", "obs_query_traces"}


class TestObsToolOutput:
    def test_lite_mode_tool_call_renders_warning(self, lite_mode):
        module = importlib.reload(importlib.import_module("prax.agent.obs_tools"))
        result = module.obs_query_logs.invoke({"logql": '{job="prax"}'})
        assert "not configured" in result.lower()
        assert "lite" in result.lower() or "full" in result.lower()

    def test_full_mode_tool_renders_results(self, full_mode, monkeypatch):
        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "data": {"result": [
                {"stream": {"job": "prax"}, "values": [["1", "hello"]]},
            ]},
        }
        fake_resp.raise_for_status = lambda: None
        monkeypatch.setattr(obs_service.requests, "get", lambda *a, **kw: fake_resp)
        module = importlib.reload(importlib.import_module("prax.agent.obs_tools"))
        result = module.obs_query_logs.invoke({"logql": '{job="prax"}'})
        assert "hello" in result
        assert "Matched" in result
