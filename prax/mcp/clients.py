"""Per-caller MCP client registry — identity + authorization.

Each MCP client is an entry ``{name, token|token_sha256, user_id, allow}``:
- **token / token_sha256** — the bearer secret presented by that caller
  (``token_sha256`` lets you store only a hash, so a leaked registry file
  doesn't reveal usable tokens).
- **user_id** — the Prax identity the caller acts as. Every tool call resolves
  to THIS user's context (workspace, memory, approved secrets), so different
  callers get different, bounded identities.
- **allow** — the explicit list of tool names this caller may use (or
  ``"default"`` for the read-only default set). The allowlist IS the
  authorization boundary: to grant a caller *write* (MEDIUM) tools, list them
  here. HIGH-risk tools are never exposed over MCP regardless (no human to
  confirm), enforced in the server.

Registry sources, merged: a JSON file at ``settings.mcp_clients_path``
(``{"clients": [...]}``) plus a legacy single-client fallback synthesised from
``MCP_BEARER_TOKEN`` / ``MCP_USER_ID`` / ``MCP_TOOL_ALLOWLIST`` (so v1 configs
keep working unchanged).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MCPClient:
    name: str
    user_id: str | None = None
    allow: frozenset[str] | None = None  # None → server's DEFAULT_ALLOWLIST
    token: str = field(default="", repr=False)
    token_sha256: str = field(default="", repr=False)

    def matches(self, presented: str) -> bool:
        """Constant-time check that *presented* is this client's token."""
        if self.token_sha256:
            digest = hashlib.sha256(presented.encode("utf-8")).hexdigest()
            return hmac.compare_digest(digest, self.token_sha256)
        if self.token:
            return hmac.compare_digest(presented.encode("utf-8"), self.token.encode("utf-8"))
        return False


def _parse_allow(value) -> frozenset[str] | None:
    """Normalise an allow spec → frozenset of names, or None for the default set."""
    if value in (None, "", "default", "DEFAULT"):
        return None
    if isinstance(value, str):
        names = [n.strip() for n in value.split(",") if n.strip()]
    elif isinstance(value, (list, tuple, set, frozenset)):
        names = [str(n).strip() for n in value if str(n).strip()]
    else:
        return None
    return frozenset(names) or None


def _clients_from_file() -> list[MCPClient]:
    from prax.settings import settings
    path = (settings.mcp_clients_path or "").strip()
    if not path:
        return []
    p = Path(path).expanduser()
    if not p.exists():
        logger.warning("MCP_CLIENTS_PATH set but %s does not exist", p)
        return []
    try:
        data = json.loads(p.read_text())
    except Exception:
        logger.exception("Could not parse MCP clients file %s", p)
        return []
    out: list[MCPClient] = []
    for i, entry in enumerate(data.get("clients", [])):
        try:
            name = entry.get("name") or f"client-{i}"
            token = entry.get("token", "") or ""
            token_sha256 = (entry.get("token_sha256", "") or "").lower()
            if not token and not token_sha256:
                logger.warning("MCP client %r has no token/token_sha256 — skipped", name)
                continue
            out.append(MCPClient(
                name=name,
                user_id=entry.get("user_id") or None,
                allow=_parse_allow(entry.get("allow")),
                token=token,
                token_sha256=token_sha256,
            ))
        except Exception:
            logger.warning("Skipping malformed MCP client entry #%d", i, exc_info=True)
    return out


def legacy_client() -> MCPClient:
    """The single client synthesised from the v1 settings (always returned for
    direct/in-process use even if no token is set — token only matters for auth)."""
    from prax.settings import settings
    return MCPClient(
        name="default",
        user_id=settings.mcp_user_id or None,
        allow=_parse_allow(settings.mcp_tool_allowlist),
        token=settings.mcp_bearer_token or "",
    )


def load_clients() -> list[MCPClient]:
    """All configured MCP clients: file entries + the legacy single-token client
    (only included when ``MCP_BEARER_TOKEN`` is set)."""
    from prax.settings import settings
    clients = _clients_from_file()
    if settings.mcp_bearer_token:
        clients.append(legacy_client())
    return clients


def resolve_client(presented: str | None) -> MCPClient | None:
    """Resolve a presented bearer token to a client, or None. Constant-time-ish:
    every client is checked (no early break on match)."""
    if not presented:
        return None
    matched: MCPClient | None = None
    for client in load_clients():
        if client.matches(presented) and matched is None:
            matched = client
    return matched
