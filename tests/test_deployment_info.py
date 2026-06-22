"""Tests for deployment / Tailscale reachability detection (prax.services.deployment_info)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from prax.services import deployment_info as di

_STATUS_JSON = json.dumps({
    "BackendState": "Running",
    "MagicDNSSuffix": "tail9eb7b0.ts.net",
    "Self": {
        "DNSName": "ip-172-26-0-6.tail9eb7b0.ts.net.",
        "TailscaleIPs": ["100.86.74.77"],
    },
})


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    di.clear_cache()
    from prax.settings import settings
    monkeypatch.setattr(settings, "ngrok_url", None, raising=False)
    monkeypatch.setattr(settings, "teamwork_base_url", "http://localhost:8000", raising=False)
    monkeypatch.setattr(settings, "running_in_docker", False, raising=False)
    # These tests exercise the auto-detect path explicitly (the suite-wide
    # default in conftest is OFF for deterministic link building).
    monkeypatch.setattr(settings, "public_url_autodetect", True, raising=False)
    monkeypatch.delenv("TS_HOSTNAME", raising=False)


def _mock_tailscale(monkeypatch, *, present=True, status_json=_STATUS_JSON, serve_text=""):
    monkeypatch.setattr(di.shutil, "which", lambda name: "/usr/bin/tailscale" if present else None)

    def fake_run(cmd, **kw):
        if cmd[:2] == ["tailscale", "status"]:
            return SimpleNamespace(returncode=0, stdout=status_json, stderr="")
        if cmd[:2] == ["tailscale", "serve"]:
            return SimpleNamespace(returncode=0, stdout=serve_text, stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")
    monkeypatch.setattr(di.subprocess, "run", fake_run)


# --------------------------------------------------------------------------- #
# tailscale_status
# --------------------------------------------------------------------------- #

def test_status_active(monkeypatch):
    _mock_tailscale(monkeypatch)
    s = di.tailscale_status()
    assert s["available"] is True
    assert s["hostname"] == "ip-172-26-0-6.tail9eb7b0.ts.net"   # trailing dot stripped
    assert s["ips"] == ["100.86.74.77"]


def test_status_no_cli(monkeypatch):
    _mock_tailscale(monkeypatch, present=False)
    s = di.tailscale_status()
    assert s["available"] is False
    assert "not installed" in s["reason"]


def test_status_stopped(monkeypatch):
    _mock_tailscale(monkeypatch, status_json=json.dumps({"BackendState": "Stopped", "Self": {}}))
    assert di.tailscale_status()["available"] is False


# --------------------------------------------------------------------------- #
# get_deployment_info + advisories
# --------------------------------------------------------------------------- #

def test_public_url_autoderived_from_tailscale(monkeypatch):
    _mock_tailscale(monkeypatch)
    info = di.get_deployment_info()
    assert info["public_base_url"] == "https://ip-172-26-0-6.tail9eb7b0.ts.net"
    assert info["public_via"] == "tailscale"
    # Auto-detect on (the prod default) → links use the tailnet URL automatically
    # even though TEAMWORK_BASE_URL is localhost, so there's nothing to nag about.
    assert info["effective_base_url"] == "https://ip-172-26-0-6.tail9eb7b0.ts.net"
    assert info["effective_via"] == "auto:tailscale"
    assert info["advisories"] == []
    # The standalone helper agrees (this is what link-building code calls).
    assert di.effective_base_url() == "https://ip-172-26-0-6.tail9eb7b0.ts.net"


def test_advisory_only_when_autodetect_off(monkeypatch):
    _mock_tailscale(monkeypatch)
    from prax.settings import settings
    monkeypatch.setattr(settings, "public_url_autodetect", False, raising=False)
    di.clear_cache()
    info = di.get_deployment_info()
    # Auto-detect off + localhost config → effective stays local, advisory returns.
    assert info["effective_base_url"] == "http://localhost:8000"
    assert info["advisories"]
    assert "TEAMWORK_BASE_URL" in info["advisories"][0]
    assert "PUBLIC_URL_AUTODETECT" in info["advisories"][0]
    # And the helper honours the strict config-only mode.
    assert di.effective_base_url() == "http://localhost:8000"


def test_no_advisory_when_teamwork_base_is_public(monkeypatch):
    _mock_tailscale(monkeypatch)
    from prax.settings import settings
    monkeypatch.setattr(settings, "teamwork_base_url", "https://ip-172-26-0-6.tail9eb7b0.ts.net", raising=False)
    di.clear_cache()
    assert di.get_deployment_info()["advisories"] == []


def test_ngrok_preferred_over_tailscale(monkeypatch):
    _mock_tailscale(monkeypatch)
    from prax.settings import settings
    monkeypatch.setattr(settings, "ngrok_url", "https://abc123.ngrok.app", raising=False)
    di.clear_cache()
    info = di.get_deployment_info()
    assert info["public_base_url"] == "https://abc123.ngrok.app"
    assert info["public_via"] == "ngrok"


def test_sidecar_hostname_env(monkeypatch):
    _mock_tailscale(monkeypatch, present=False)   # process can't query the sidecar
    monkeypatch.setenv("TS_HOSTNAME", "prax")
    di.clear_cache()
    info = di.get_deployment_info()
    assert info["ts_hostname_env"] == "prax"
    assert info["public_via"] == "tailscale-sidecar"
    assert info["advisories"]   # localhost base URL + a proxy configured


def test_local_only(monkeypatch):
    _mock_tailscale(monkeypatch, present=False)
    info = di.get_deployment_info()
    assert info["public_base_url"] is None
    assert info["advisories"] == []   # nothing public → no off-network advisory
    assert "local only" in di.summary_line()


# --------------------------------------------------------------------------- #
# summary_line + report + tool
# --------------------------------------------------------------------------- #

def test_summary_line_mentions_public_url(monkeypatch):
    _mock_tailscale(monkeypatch)
    line = di.summary_line()
    assert "ip-172-26-0-6.tail9eb7b0.ts.net" in line and "via tailscale" in line


def test_format_report_and_tool(monkeypatch):
    _mock_tailscale(monkeypatch, serve_text="https://ip-172-26-0-6.tail9eb7b0.ts.net\n|-- / proxy http://localhost:5173\n")
    report = di.format_report()
    assert "Tailscale: ACTIVE" in report
    assert "proxy http://localhost:5173" in report
    # The agent tool returns the same report.
    from prax.agent.deployment_tools import deployment_info as tool
    assert "Deployment / reachability" in tool.invoke({})
