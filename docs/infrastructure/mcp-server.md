# MCP Server — exposing Prax tools to other agents

[← Infrastructure](README.md)

Prax can expose a **curated, bearer-authed subset of its tools to other agents** over the
[Model Context Protocol](https://modelcontextprotocol.io) (MCP). This is the foundation for
"make Prax usable by other agents": an external MCP client (another harness, Claude, etc.)
discovers Prax's tools and calls them over a standard wire protocol — without importing Prax's
code or touching its database.

It is **off by default** and **fail-closed**: the endpoint is only mounted when both
`MCP_SERVER_ENABLED=true` *and* a bearer token are set.

> Scope note: this is the discovery/execution layer. Each caller maps to its own Prax identity
> and tool allowlist (see "Per-caller clients" below). ARD (catalog-based *discovery + trust*) is
> a separate, optional layer on top — see
> [Agentic Resource Discovery](../research/agentic-resource-discovery.md).

## What it is

- A dependency-free server (no MCP SDK) implementing the request/response slice of MCP's
  **Streamable HTTP** transport: a client `POST`s a JSON-RPC 2.0 message to `/mcp` and gets a
  single JSON response. Methods: `initialize`, `ping`, `tools/list`, `tools/call`, and the
  `notifications/initialized` notification.
- A Flask blueprint (`prax/mcp/blueprint.py`) mounted by `app.create_app`; protocol logic lives
  in `prax/mcp/server.py` (`MCPServer`).

## Security model

| Control | How |
|---|---|
| **Bearer auth** | Constant-time (`hmac.compare_digest`) check of `Authorization: Bearer <MCP_BEARER_TOKEN>` on every request; 401 otherwise. |
| **Optional token expiry** | With `MCP_TOKEN_EXPIRY_ENABLED=true`, a client's `expires_at` (ISO-8601) is enforced — an expired token is rejected exactly like an invalid one (401), and an unreadable expiry is treated as expired (fail-closed). Default off → tokens never expire. Lets you hand a short-lived token to another agent and let it lapse instead of rotating. |
| **Fail-closed** | The blueprint is registered only when enabled AND a token is set. `MCP_SERVER_ENABLED=true` with no token logs an error and mounts nothing. |
| **Per-caller allowlist** | Each client exposes only the tools in *its own* allowlist (the authorization boundary). To grant a trusted caller *write* (MEDIUM) tools, list them in that client's `allow`. |
| **Never HIGH** | A tool classified HIGH-risk is refused even if allowlisted — at build time *and* call time. HIGH tools are destructive/irreversible and expect a human confirmation an external caller can't give. (To expose a constrained capability, wrap a narrow MEDIUM tool instead.) |
| **Per-caller identity** | Each client maps to a Prax `user_id`; every call runs under *that* user's context (workspace, memory, approved secrets). Different callers get different, bounded identities — calls are attributed `mcp:<client-name>` in the audit trail. |
| **Governance kept in front** | Exposed tools come from the governed registry (`get_registered_tools`), so risk classification + audit logging still apply. |
| **SSRF guard** | Any outbound HTTP a called tool makes still goes through the [SSRF egress guard](../security/tool-risk.md#ssrf-egress-guard). |

## Configuration

The endpoint mounts when `MCP_SERVER_ENABLED=true` **and** at least one client is configured —
via the single-token shortcut, a multi-client registry, or both (they merge).

**Single caller (shortcut).** One token → one identity → the default (or `MCP_TOOL_ALLOWLIST`)
tool set:

```bash
MCP_SERVER_ENABLED=true
MCP_BEARER_TOKEN=<a strong random token>
MCP_USER_ID=<the prax user id this caller acts as>
# Optional — defaults to a small safe read-only set:
# MCP_TOOL_ALLOWLIST=get_current_datetime,memory_recall,knowledge_search
```

**Per-caller clients (registry).** Point `MCP_CLIENTS_PATH` at a JSON file; each entry is a
distinct caller with its own token, identity, and allowlist:

```jsonc
// MCP_CLIENTS_PATH=/secrets/mcp_clients.json
{
  "clients": [
    {                                  // read-only research agent
      "name": "research-agent",
      "token": "…strong secret…",
      "user_id": "u_research",
      "allow": ["memory_recall", "knowledge_search", "knowledge_namespaces"]
    },
    {                                  // trusted writer — token stored hashed
      "name": "ops-agent",
      "token_sha256": "<sha256(token) hex>",
      "user_id": "u_ops",
      "allow": ["memory_recall", "knowledge_search", "memory_remember", "note_create"]
    }
  ]
}
```

- `token` (plaintext) or `token_sha256` (store only the hash; a leaked file can't be replayed).
- `user_id` — the Prax identity that caller acts as.
- `allow` — explicit tool names, or `"default"` for the read-only default set. **Listing a
  write (MEDIUM) tool here is how you grant write access to a trusted caller.** HIGH-risk tools
  are refused regardless.

**Default allowlist** (a client with `allow: "default"`, or the single-token client with no
`MCP_TOOL_ALLOWLIST`): `get_current_datetime`, `memory_recall`, `knowledge_search`,
`knowledge_namespaces`, `conversation_search`, `trace_search`, `trace_detail` — all read-only
(`prax/mcp/server.py:DEFAULT_ALLOWLIST`).

**Optional: expiring tokens (default off).** Hand a caller a token that lapses on its own
instead of living forever. Turn enforcement on and stamp each token with an expiry:

```bash
MCP_TOKEN_EXPIRY_ENABLED=true        # enforce expires_at everywhere (default false)
MCP_TOKEN_EXPIRES_AT=2026-12-31T00:00:00Z   # expiry for the single-token client (optional)
```

```jsonc
// in a registry entry:
{ "name": "research-agent", "token": "…", "user_id": "u_research",
  "allow": ["knowledge_search"], "expires_at": "2026-12-31T00:00:00Z" }
```

Semantics: enforcement is **flag-gated** (off → `expires_at` is ignored, so existing static
tokens are unaffected). A client with no `expires_at` never expires. Past expiry, the token is
rejected with `401` as if it had never been issued; a malformed timestamp is treated as expired
(fail-closed). The check is on every request, so you can shorten/extend a lease by editing the
registry file with no restart.

Expose the endpoint to other machines the same way as the rest of Prax (tailscale serve / a
reverse proxy with TLS) — the bearer token is the auth boundary; put TLS in front of it.

## Example

```bash
# Discover the exposed tools:
curl -s https://<host>/mcp \
  -H "Authorization: Bearer $MCP_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# Call one:
curl -s https://<host>/mcp \
  -H "Authorization: Bearer $MCP_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"knowledge_search","arguments":{"query":"transformers"}}}'
```

## Deliberate limits

- **Never HIGH.** Destructive/irreversible (HIGH-risk) tools are never exposed — they need a
  human-in-the-loop confirmation an autonomous caller can't give. Write access is granted at the
  MEDIUM tier per-caller via the allowlist.
- **Request/response transport.** No SSE streaming or server-initiated messages (a tools server
  doesn't need them); sessions are stateless.
- **Not auto-discoverable.** Other agents must be pointed at the URL + token. Catalog-based
  discovery (ARD) is a separate optional layer — see the ARD reference note.
- **Coarse identity unit.** Authorization is per-*client* (per-token), not per end-user within a
  client. A client maps to exactly one Prax `user_id`; rotate/scope tokens per integration.

## See also

- [Agentic Resource Discovery (ARD)](../research/agentic-resource-discovery.md) — the discovery/trust
  layer that would sit on top of this, and why exposing tools (this) is the prerequisite, not ARD.
- [Tool Risk & Supply Chain](../security/tool-risk.md) — risk tiers, the SSRF guard, and the
  governance choke point this server reuses.
- [Providing Prax a sandbox](sandbox.md) — the other already-externally-callable surface
  (the bearer-over-TLS sandbox daemon).
