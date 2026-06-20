"""MCP server core — protocol dispatch + per-caller curated tool exposure.

Transport-agnostic: :meth:`MCPServer.handle` takes a parsed JSON-RPC message and
the resolved :class:`~prax.mcp.clients.MCPClient`, and returns a response dict
(or ``None`` for notifications). The Flask blueprint in
:mod:`prax.mcp.blueprint` resolves the bearer token → client and adds HTTP
framing.

Security model:
- **Per-caller identity** — each client maps to a Prax ``user_id``; every tool
  call runs under THAT user's context (workspace, memory, approved secrets), so
  different callers get different, bounded identities.
- **Per-caller allowlist** — each client exposes only the tools in its own
  allowlist (the authorization boundary; a trusted client may be granted write
  (MEDIUM) tools by listing them).
- **Never HIGH** — a HIGH-risk tool is refused even if allowlisted: HIGH actions
  are destructive/irreversible and expect a human confirmation an external
  caller can't give.
- **Governance kept in front** — tools come from the governed registry, so risk
  classification + audit logging still apply.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prax.mcp.clients import MCPClient

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "prax-mcp"
SERVER_VERSION = "0.2.0"

# Small, safe, read-only default set used when a client has no explicit allowlist.
DEFAULT_ALLOWLIST = frozenset({
    "get_current_datetime",
    "memory_recall",
    "knowledge_search",
    "knowledge_namespaces",
    "conversation_search",
    "trace_search",
    "trace_detail",
})


class JsonRpcError(Exception):
    """A JSON-RPC error to return to the client."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _identity_for(client: MCPClient):
    from prax.agent.user_context import UserContextSnapshot
    return UserContextSnapshot(
        user_id=client.user_id,
        user=None,
        channel_id=None,
        channel_name="",
        user_message="",
        component=f"mcp:{client.name}",
        active_view="",
    )


def _stringify(result) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, default=str, ensure_ascii=False)
    except Exception:
        return str(result)


def _input_schema(tool) -> dict:
    try:
        schema = getattr(tool, "args_schema", None)
        if schema is not None:
            js = schema.model_json_schema()
            if isinstance(js, dict) and js.get("type") == "object":
                return js
    except Exception:
        logger.debug("Could not derive input schema for %s", getattr(tool, "name", "?"), exc_info=True)
    return {"type": "object", "properties": {}}


def _default_client():
    from prax.mcp.clients import legacy_client
    return legacy_client()


class MCPServer:
    """Exposes a per-caller curated, governed subset of Prax tools over MCP."""

    def __init__(self):
        # Built tool maps cached per client name (tool construction is the cost).
        self._tools_by_client: dict[str, dict] = {}

    # -- tool exposure ------------------------------------------------------

    def _candidate_tools(self) -> list:
        """Governance-wrapped pool of tools eligible for exposure (registry +
        read-only spoke builders). The per-client allowlist + non-HIGH filter
        decide what is actually exposed, so widening the pool is safe."""
        from prax.agent.governed_tool import wrap_with_governance
        from prax.agent.tool_registry import get_registered_tools

        pool = list(get_registered_tools())  # already governance-wrapped
        seen = {t.name for t in pool}
        for builder in _read_only_spoke_builders():
            try:
                for raw in builder():
                    if raw.name not in seen:
                        pool.append(wrap_with_governance(raw))
                        seen.add(raw.name)
            except Exception:
                logger.debug("MCP: spoke tool builder failed", exc_info=True)
        return pool

    def _tools_for(self, client: MCPClient) -> dict:
        """Exposed tool-name → governed BaseTool for *client* (cached by name).

        Built while the client's identity is active so the governed wrappers'
        captured user-context binds to the client's ``user_id``.
        """
        cached = self._tools_by_client.get(client.name)
        if cached is not None:
            return cached

        from prax.agent.action_policy import RiskLevel, get_risk_level
        from prax.agent.user_context import use_user_context

        allow = client.allow if client.allow is not None else DEFAULT_ALLOWLIST
        out: dict = {}
        try:
            with use_user_context(_identity_for(client)):
                candidates = self._candidate_tools()
        except Exception:
            logger.exception("MCP: could not build tool registry for client %s", client.name)
            self._tools_by_client[client.name] = out
            return out
        for tool in candidates:
            name = tool.name
            if name not in allow:
                continue
            if get_risk_level(name) == RiskLevel.HIGH:
                logger.warning("MCP[%s]: refusing HIGH-risk tool %r (allowlisted)", client.name, name)
                continue
            out[name] = tool
        logger.info("MCP[%s] exposing %d tool(s) as user=%s: %s",
                    client.name, len(out), client.user_id, sorted(out))
        self._tools_by_client[client.name] = out
        return out

    def list_tools(self, client: MCPClient | None = None) -> list[dict]:
        client = client or _default_client()
        return [
            {
                "name": t.name,
                "description": (t.description or "").strip(),
                "inputSchema": _input_schema(t),
            }
            for t in self._tools_for(client).values()
        ]

    def call_tool(self, name: str, arguments: dict | None, client: MCPClient | None = None) -> dict:
        from prax.agent.action_policy import RiskLevel, get_risk_level
        from prax.agent.user_context import use_user_context

        client = client or _default_client()
        tool = self._tools_for(client).get(name)
        if tool is None:
            raise JsonRpcError(-32602, f"unknown or non-exposed tool: {name!r}")
        if get_risk_level(name) == RiskLevel.HIGH:  # defense-in-depth
            raise JsonRpcError(-32602, f"tool {name!r} is HIGH-risk and not callable over MCP")
        try:
            with use_user_context(_identity_for(client)):
                result = tool.invoke(arguments or {})
            return {"content": [{"type": "text", "text": _stringify(result)}], "isError": False}
        except Exception as exc:
            logger.warning("MCP[%s] tool %s failed: %s", client.name, name, exc)
            return {
                "content": [{"type": "text", "text": f"Tool error: {type(exc).__name__}: {exc}"}],
                "isError": True,
            }

    # -- JSON-RPC dispatch --------------------------------------------------

    def _dispatch(self, method: str, params: dict, client: MCPClient):
        if method == "initialize":
            client_version = (params or {}).get("protocolVersion") or PROTOCOL_VERSION
            return {
                "protocolVersion": client_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.list_tools(client)}
        if method == "tools/call":
            name = (params or {}).get("name")
            if not name:
                raise JsonRpcError(-32602, "missing tool name")
            return self.call_tool(name, (params or {}).get("arguments") or {}, client)
        raise JsonRpcError(-32601, f"method not found: {method}")

    def handle(self, message: dict, client: MCPClient | None = None) -> dict | None:
        """Dispatch one JSON-RPC message for *client*. Returns a response dict,
        or None for notifications (caller replies 202 with no body)."""
        client = client or _default_client()
        if not isinstance(message, dict):
            return _error_response(None, -32600, "invalid request")
        method = message.get("method")
        msg_id = message.get("id")
        is_notification = "id" not in message
        if not method:
            return None if is_notification else _error_response(msg_id, -32600, "missing method")
        if is_notification:
            return None
        try:
            result = self._dispatch(method, message.get("params") or {}, client)
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except JsonRpcError as exc:
            return _error_response(msg_id, exc.code, exc.message)
        except Exception as exc:
            logger.exception("MCP dispatch error for method %s", method)
            return _error_response(msg_id, -32603, f"internal error: {exc}")


def _read_only_spoke_builders() -> list:
    """Tool-builders for read-only spoke tools worth exposing over MCP.

    Returned lazily so the import cost is only paid when the MCP server builds.
    The allowlist still gates which of these are actually exposed.
    """
    builders = []
    try:
        from prax.agent.memory_tools import build_memory_tools
        builders.append(build_memory_tools)
    except Exception:
        pass
    try:
        from prax.agent.knowledge_tools import build_knowledge_tools
        builders.append(build_knowledge_tools)
    except Exception:
        pass
    return builders


def _error_response(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
