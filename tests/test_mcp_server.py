"""Tests for the MCP server (prax.mcp): protocol dispatch, allowlist + HIGH
rejection, identity, and the bearer-auth Flask blueprint."""
from __future__ import annotations

import pytest
from flask import Flask

from prax.mcp.blueprint import make_mcp_blueprint
from prax.mcp.server import DEFAULT_ALLOWLIST, MCPServer


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    from prax.settings import settings
    monkeypatch.setattr(settings, "mcp_tool_allowlist", "")
    monkeypatch.setattr(settings, "mcp_user_id", "")
    monkeypatch.setattr(settings, "mcp_bearer_token", "test-secret")
    monkeypatch.setattr(settings, "mcp_clients_path", "")


# --------------------------------------------------------------------------- #
# Protocol dispatch
# --------------------------------------------------------------------------- #

def test_initialize_echoes_protocol_and_serverinfo():
    srv = MCPServer()
    resp = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18"}})
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == "2025-06-18"
    assert resp["result"]["serverInfo"]["name"] == "prax-mcp"
    assert "tools" in resp["result"]["capabilities"]


def test_ping():
    srv = MCPServer()
    assert srv.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})["result"] == {}


def test_notification_returns_none():
    srv = MCPServer()
    # No "id" → notification → no response.
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_errors():
    srv = MCPServer()
    resp = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "does/not/exist"})
    assert resp["error"]["code"] == -32601


# --------------------------------------------------------------------------- #
# Allowlist + HIGH-risk rejection
# --------------------------------------------------------------------------- #

def test_tools_list_only_exposes_allowlisted():
    srv = MCPServer()
    names = {t["name"] for t in srv.list_tools()}
    # Default allowlist members that exist are exposed; nothing outside it is.
    assert names <= set(DEFAULT_ALLOWLIST)
    assert "get_current_datetime" in names          # a known LOW read-only tool
    assert "plugin_write" not in names               # HIGH + not allowlisted
    assert "sandbox_shell" not in names              # not allowlisted


def test_high_risk_tool_refused_even_if_allowlisted(monkeypatch):
    from prax.settings import settings
    # Allowlist a HIGH-risk tool explicitly — it must still be refused.
    monkeypatch.setattr(settings, "mcp_tool_allowlist", "get_current_datetime,plugin_write")
    srv = MCPServer()
    names = {t["name"] for t in srv.list_tools()}
    assert "get_current_datetime" in names
    assert "plugin_write" not in names               # filtered at build time
    # And calling it directly is refused.
    from prax.mcp.server import JsonRpcError
    with pytest.raises(JsonRpcError):
        srv.call_tool("plugin_write", {})


def test_tools_have_input_schema():
    srv = MCPServer()
    for t in srv.list_tools():
        assert t["inputSchema"]["type"] == "object"


def test_call_unknown_tool_errors():
    srv = MCPServer()
    resp = srv.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "not_exposed_tool", "arguments": {}}})
    assert resp["error"]["code"] == -32602


def test_call_real_tool_returns_content():
    srv = MCPServer()
    resp = srv.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                       "params": {"name": "get_current_datetime", "arguments": {}}})
    result = resp["result"]
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"
    assert result["content"][0]["text"]  # non-empty datetime string


# --------------------------------------------------------------------------- #
# Bearer-auth blueprint
# --------------------------------------------------------------------------- #

@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(make_mcp_blueprint())
    return app.test_client()


def test_blueprint_rejects_missing_token(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == "Bearer"


def test_blueprint_rejects_wrong_token(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_blueprint_accepts_valid_token(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    headers={"Authorization": "Bearer test-secret"})
    assert r.status_code == 200
    assert r.get_json()["result"]["serverInfo"]["name"] == "prax-mcp"


def test_blueprint_notification_returns_202(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    headers={"Authorization": "Bearer test-secret"})
    assert r.status_code == 202


def test_blueprint_bad_json(client):
    r = client.post("/mcp", data="not json",
                    headers={"Authorization": "Bearer test-secret", "Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == -32700
