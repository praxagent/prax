"""Flask blueprint for the MCP endpoint — bearer auth + JSON-RPC over HTTP POST.

Implements the request/response slice of MCP's Streamable HTTP transport: a
client POSTs a JSON-RPC message to ``/mcp`` and gets a single JSON response.
Registered by ``app.create_app`` ONLY when ``settings.mcp_server_enabled`` AND a
bearer token are set (fail-closed — never expose an unauthenticated endpoint).
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from prax.mcp.server import MCPServer

logger = logging.getLogger(__name__)


def _presented_token(authorization: str | None) -> str | None:
    """Extract the bearer token from an Authorization header, or None."""
    if not authorization:
        return None
    scheme, _, presented = authorization.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return None
    return presented


def make_mcp_blueprint(server: MCPServer | None = None) -> Blueprint:
    """Build the MCP blueprint. A server is constructed lazily on first request
    so tool registry import cost isn't paid at app-construction time."""
    bp = Blueprint("mcp", __name__)
    state: dict = {"server": server}

    def _server() -> MCPServer:
        if state["server"] is None:
            state["server"] = MCPServer()
        return state["server"]

    @bp.route("/mcp", methods=["POST"])
    def mcp_endpoint():
        from prax.mcp.clients import resolve_client

        # Resolve the bearer token to a specific client (identity + allowlist).
        client = resolve_client(_presented_token(request.headers.get("Authorization")))
        if client is None:
            resp = jsonify({"error": "missing or invalid bearer token"})
            resp.status_code = 401
            resp.headers["WWW-Authenticate"] = "Bearer"
            return resp

        message = request.get_json(silent=True)
        if message is None:
            return jsonify({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "parse error: body is not JSON"},
            }), 400

        # JSON-RPC batch (a list) — handle each as this client, drop notifications.
        if isinstance(message, list):
            responses = [r for r in (_server().handle(m, client) for m in message) if r is not None]
            return ("", 202) if not responses else jsonify(responses)

        response = _server().handle(message, client)
        if response is None:
            return ("", 202)  # notification — no body
        return jsonify(response)

    return bp
