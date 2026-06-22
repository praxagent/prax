"""Tests for MCP per-caller identity & authorization (prax.mcp.clients) and
per-client tool scoping in the server."""
from __future__ import annotations

import hashlib
import json

import pytest
from flask import Flask

from prax.mcp import clients as mcp_clients
from prax.mcp.blueprint import make_mcp_blueprint
from prax.mcp.server import MCPServer, _identity_for


@pytest.fixture
def two_client_registry(tmp_path, monkeypatch):
    """A clients.json with a read-only 'research' client and a default 'ops'
    client (whose token is stored hashed). No legacy single-token client."""
    path = tmp_path / "mcp_clients.json"
    path.write_text(json.dumps({"clients": [
        {"name": "research", "token": "tok-r", "user_id": "u_research",
         "allow": ["get_current_datetime"]},
        {"name": "ops", "token_sha256": hashlib.sha256(b"tok-o").hexdigest(),
         "user_id": "u_ops", "allow": "default"},
    ]}))
    from prax.settings import settings
    monkeypatch.setattr(settings, "mcp_clients_path", str(path))
    monkeypatch.setattr(settings, "mcp_bearer_token", "")   # file clients only
    monkeypatch.setattr(settings, "mcp_user_id", "")
    monkeypatch.setattr(settings, "mcp_tool_allowlist", "")
    return path


# --------------------------------------------------------------------------- #
# Registry loading + resolution
# --------------------------------------------------------------------------- #

def test_resolve_by_plaintext_token(two_client_registry):
    c = mcp_clients.resolve_client("tok-r")
    assert c is not None and c.name == "research" and c.user_id == "u_research"
    assert c.allow == frozenset({"get_current_datetime"})


def test_resolve_by_hashed_token(two_client_registry):
    c = mcp_clients.resolve_client("tok-o")
    assert c is not None and c.name == "ops" and c.user_id == "u_ops"
    assert c.allow is None   # "default" → server default allowlist


def test_resolve_unknown_token_is_none(two_client_registry):
    assert mcp_clients.resolve_client("nope") is None
    assert mcp_clients.resolve_client(None) is None


def test_legacy_fallback_client(monkeypatch):
    from prax.settings import settings
    monkeypatch.setattr(settings, "mcp_clients_path", "")
    monkeypatch.setattr(settings, "mcp_bearer_token", "legacy-tok")
    monkeypatch.setattr(settings, "mcp_user_id", "u_legacy")
    monkeypatch.setattr(settings, "mcp_tool_allowlist", "")
    c = mcp_clients.resolve_client("legacy-tok")
    assert c is not None and c.name == "default" and c.user_id == "u_legacy"


# --------------------------------------------------------------------------- #
# Token expiry (MCP_TOKEN_EXPIRY_ENABLED, default off)
# --------------------------------------------------------------------------- #

def _expiry_registry(tmp_path, monkeypatch, expires_at):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"clients": [
        {"name": "temp", "token": "tok-t", "user_id": "u", "allow": "default",
         "expires_at": expires_at},
    ]}))
    from prax.settings import settings
    monkeypatch.setattr(settings, "mcp_clients_path", str(path))
    monkeypatch.setattr(settings, "mcp_bearer_token", "")
    return path


def test_expired_token_ignored_when_flag_off(tmp_path, monkeypatch):
    _expiry_registry(tmp_path, monkeypatch, "2000-01-01T00:00:00Z")
    from prax.settings import settings
    monkeypatch.setattr(settings, "mcp_token_expiry_enabled", False)
    # Flag off → expiry not enforced; the token still resolves (back-compat).
    assert mcp_clients.resolve_client("tok-t") is not None


def test_expired_token_rejected_when_flag_on(tmp_path, monkeypatch):
    _expiry_registry(tmp_path, monkeypatch, "2000-01-01T00:00:00Z")
    from prax.settings import settings
    monkeypatch.setattr(settings, "mcp_token_expiry_enabled", True)
    assert mcp_clients.resolve_client("tok-t") is None


def test_future_expiry_still_valid_when_flag_on(tmp_path, monkeypatch):
    _expiry_registry(tmp_path, monkeypatch, "2999-01-01T00:00:00Z")
    from prax.settings import settings
    monkeypatch.setattr(settings, "mcp_token_expiry_enabled", True)
    c = mcp_clients.resolve_client("tok-t")
    assert c is not None and c.name == "temp"


def test_malformed_expiry_is_failclosed_when_flag_on(tmp_path, monkeypatch):
    _expiry_registry(tmp_path, monkeypatch, "not-a-date")
    from prax.settings import settings
    monkeypatch.setattr(settings, "mcp_token_expiry_enabled", True)
    assert mcp_clients.resolve_client("tok-t") is None


def test_client_without_expiry_never_expires(tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"clients": [
        {"name": "perm", "token": "tok-p", "user_id": "u", "allow": "default"},
    ]}))
    from prax.settings import settings
    monkeypatch.setattr(settings, "mcp_clients_path", str(path))
    monkeypatch.setattr(settings, "mcp_bearer_token", "")
    monkeypatch.setattr(settings, "mcp_token_expiry_enabled", True)
    assert mcp_clients.resolve_client("tok-p") is not None


def test_legacy_token_expiry(monkeypatch):
    from prax.settings import settings
    monkeypatch.setattr(settings, "mcp_clients_path", "")
    monkeypatch.setattr(settings, "mcp_bearer_token", "legacy-tok")
    monkeypatch.setattr(settings, "mcp_user_id", "u_legacy")
    monkeypatch.setattr(settings, "mcp_tool_allowlist", "")
    monkeypatch.setattr(settings, "mcp_token_expires_at", "2000-01-01T00:00:00Z")
    monkeypatch.setattr(settings, "mcp_token_expiry_enabled", True)
    assert mcp_clients.resolve_client("legacy-tok") is None
    # The same token resolves once expiry enforcement is switched off.
    monkeypatch.setattr(settings, "mcp_token_expiry_enabled", False)
    assert mcp_clients.resolve_client("legacy-tok") is not None


# --------------------------------------------------------------------------- #
# Per-client tool scoping + identity
# --------------------------------------------------------------------------- #

def test_per_client_tool_scoping(two_client_registry):
    srv = MCPServer()
    research = mcp_clients.resolve_client("tok-r")
    ops = mcp_clients.resolve_client("tok-o")

    research_tools = {t["name"] for t in srv.list_tools(research)}
    ops_tools = {t["name"] for t in srv.list_tools(ops)}

    assert research_tools == {"get_current_datetime"}      # narrow allowlist
    assert "get_current_datetime" in ops_tools
    assert len(ops_tools) > len(research_tools)             # ops gets the default set


def test_identity_is_per_client(two_client_registry):
    research = mcp_clients.resolve_client("tok-r")
    ops = mcp_clients.resolve_client("tok-o")
    assert _identity_for(research).user_id == "u_research"
    assert _identity_for(ops).user_id == "u_ops"
    assert _identity_for(research).component == "mcp:research"


def test_call_tool_respects_client_allowlist(two_client_registry):
    srv = MCPServer()
    research = mcp_clients.resolve_client("tok-r")
    # 'conversation_search' is in the default set but NOT in research's allowlist.
    resp = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "conversation_search", "arguments": {"query": "x"}}},
                      research)
    assert resp["error"]["code"] == -32602   # not exposed to this client


def test_high_risk_never_exposed_even_if_allowlisted(tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"clients": [
        {"name": "trusted", "token": "t", "user_id": "u",
         "allow": ["get_current_datetime", "plugin_write"]},
    ]}))
    from prax.settings import settings
    monkeypatch.setattr(settings, "mcp_clients_path", str(path))
    monkeypatch.setattr(settings, "mcp_bearer_token", "")
    srv = MCPServer()
    trusted = mcp_clients.resolve_client("t")
    names = {t["name"] for t in srv.list_tools(trusted)}
    assert "plugin_write" not in names          # HIGH filtered out
    assert "get_current_datetime" in names


# --------------------------------------------------------------------------- #
# Blueprint routes by token to the right client
# --------------------------------------------------------------------------- #

def test_blueprint_routes_per_client(two_client_registry):
    app = Flask(__name__)
    app.register_blueprint(make_mcp_blueprint())
    client = app.test_client()

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    headers={"Authorization": "Bearer tok-r"})
    research_names = {t["name"] for t in r.get_json()["result"]["tools"]}
    assert research_names == {"get_current_datetime"}

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    headers={"Authorization": "Bearer tok-o"})
    ops_names = {t["name"] for t in r.get_json()["result"]["tools"]}
    assert len(ops_names) > 1

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
                    headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
