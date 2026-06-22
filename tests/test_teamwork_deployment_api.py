"""Tests for the /teamwork/deployment endpoint (Settings-panel reachability)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from flask import Flask

from prax.blueprints.teamwork_routes import teamwork_routes


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(teamwork_routes)
    with app.test_client() as c:
        yield c


_INFO = {
    "in_docker": False,
    "tailscale": {"available": True, "hostname": "host.tail9eb7b0.ts.net"},
    "ts_hostname_env": None,
    "ngrok_url": None,
    "public_base_url": "https://host.tail9eb7b0.ts.net",
    "public_via": "tailscale",
    "teamwork_base_url": "http://localhost:8000",
    "effective_base_url": "https://host.tail9eb7b0.ts.net",
    "effective_via": "auto:tailscale",
    "autodetect": True,
    "advisories": [],
}


def test_deployment_endpoint_shapes_payload(client):
    with patch("prax.services.deployment_info.get_deployment_info", return_value=_INFO):
        resp = client.get("/teamwork/deployment")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["available"] is True
    assert data["tailscale_active"] is True
    assert data["tailscale_hostname"] == "host.tail9eb7b0.ts.net"
    assert data["effective_base_url"] == "https://host.tail9eb7b0.ts.net"
    assert data["effective_via"] == "auto:tailscale"
    assert data["advisories"] == []


def test_deployment_endpoint_degrades_on_error(client):
    with patch("prax.services.deployment_info.get_deployment_info", side_effect=RuntimeError("boom")):
        resp = client.get("/teamwork/deployment")
    assert resp.status_code == 200
    assert resp.get_json() == {"available": False}
