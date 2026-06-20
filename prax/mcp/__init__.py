"""Model Context Protocol (MCP) server — expose curated Prax tools to other agents.

A dependency-free, spec-aligned MCP server (JSON-RPC 2.0 over HTTP) that lets
external agents discover and call an explicit, operator-curated allowlist of
Prax's read-only tools. Bearer-authed, fail-closed, default-off, and run under a
single configured identity with Prax's governance kept in front. See
``docs/infrastructure/mcp-server.md``.
"""
from __future__ import annotations

from prax.mcp.server import DEFAULT_ALLOWLIST, MCPServer

__all__ = ["MCPServer", "DEFAULT_ALLOWLIST"]
