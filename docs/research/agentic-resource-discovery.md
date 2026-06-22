# Agentic Resource Discovery (ARD) — assessment for Prax

[← Research](README.md)

> **Reference note.** Source: Junjie Bu & Srinivas Krishnan (Google), *"Announcing the Agentic
> Resource Discovery specification,"* Google for Developers blog, 2026-06-17 —
> <https://developers.googleblog.com/announcing-the-agentic-resource-discovery-specification/>
> · builds on the Linux Foundation AI Catalog Working Group data model.
>
> Question posed: *would adopting ARD make Prax stronger — and if not, document it.* This note
> records both, grounded in a 3-angle code audit (consume / publish / trust).

## What ARD is (one screen)

ARD is *"an open specification for publishing, discovering, and verifying AI capabilities across
the web."* It answers three questions: *"Where does the right capability live? Which capability
should I actually use? And how do I verify it's safe to connect to?"*

- A provider hosts an **`ai-catalog.json`** at a **well-known path on its domain**, listing
  capabilities — *"MCP servers, A2A agents, OpenAPI tools, or even other nested catalogs."*
- **Registries** *("search engines for the agentic web")* crawl and index catalogs. An agent
  either queries a registry by plain-language **intent**, or **bypasses search and fetches a known
  partner's catalog directly**.
- **Trust = domain ownership.** *"Because these catalogs are hosted directly under the
  organization's own domain, ownership of that domain serves as the cryptographic foundation for
  identity and trust"* (a "trust manifest" in `ai-catalog.schema.json`).
- It standardises **discovery + trust only**, then *"steps out of the way"* — the agent connects
  via the capability's **native protocol** (MCP/A2A/OpenAPI). Analogous to `robots.txt` + sitemap
  + `.well-known`, for agents.

The driving use case is enterprise/"agentic web": an ops agent in an incident discovering and
trusting observability tools, doc search, ticketing, and specialist agents **across
organizational boundaries**.

## Would it make Prax stronger? Honest verdict: not meaningfully, not now

ARD targets a problem Prax doesn't have. Prax is a **single-user personal-assistant harness** with
no multi-tenant, partner-onboarding, or marketplace use case; it deliberately caps the orchestrator
near ~50 tools (`tool_registry.py`) and grows capability through **self-authored plugins + spoke
delegation**, not open-web acquisition. The audit, by angle:

### Consume side — a real gap, for a problem Prax doesn't have

Prax has **none** of ARD's primitives, confirmed by grep: no MCP client, no A2A client, no
OpenAPI tool client, no `.well-known`/`ai-catalog` fetch, no registry-by-intent, no
publisher-identity verification. External capability is acquired **only** by a user/agent naming a
**known git URL** (`workspace_service.import_plugin_repo` → `git submodule add`,
`plugin_routes.py`), trusted by **origin tier** (`PluginTrust` BUILTIN/WORKSPACE/IMPORTED) + a
local source scan — not by discovery. So ARD is **not redundant** here, but adopting the consume
side is a **large greenfield build** (catalog fetch + schema validation + trust-manifest
verification + MCP/A2A/OpenAPI client runtimes — none exist) for a benefit the product isn't
reaching for.

### Publish side — trivial but low-payoff / mostly redundant

The artifacts ARD wants mostly **already exist in better-fitted forms**: the prax-sandbox daemon
auto-serves an **OpenAPI 3 spec** (`/openapi.json`, FastAPI default) plus an explicit
**`/v1/capabilities`** endpoint (`prax_sandbox/daemon/app.py`), and the harness has a plugin
`CATALOG.md` + a `plugin.json` manifest schema (`manifest.py`). Writing an `ai-catalog.json` is a
one-route change, but it would describe a **single, named, bearer-gated partner reached over a
private tailnet/VPC** — exactly the *"fetch a known partner directly"* case where ARD itself says
discovery is unnecessary. The two distinctive ARD pieces (registry-indexed discovery; a
domain-ownership trust root) don't serve a harness whose remote story is a pre-shared
**bearer token + optional mTLS** behind ngrok/tailscale rather than a publicly verifiable apex
domain (`prax_sandbox/daemon/config.py:validate_or_die`, fail-closed). For a 1:1 link, bearer+mTLS
is **simpler and stronger** than domain-DNS trust.

### Trust side — Prax's model is arguably stronger already

ARD's trust half is **largely redundant, and in places weaker** than what Prax ships: IMPORTED
tools auto-elevate to HIGH risk (`action_policy.py:200-211`); plugins run in a **stripped-env
subprocess** behind a capability gateway (`plugins/bridge.py`, `capabilities.py`) with an
audit-hook/import-blocker/rlimit "glass sandbox" (`sandbox_guard.py`); the sandbox daemon is
fail-closed **bearer-over-TLS with auth-before-dial** on the CDP socket (`daemon/cdp_proxy.py`).
ARD verifies a **publisher's domain**, not a capability's **behavior** — so it wouldn't replace
any of this; at most it would add a provenance signal Prax currently lacks (today provenance is
"the user pasted a git URL" + a source scan + a content hash for rollback, no signature/sigstore).

## The actually-useful takeaway: two latent gaps this surfaced — both now FIXED

Evaluating ARD's "auto-connect to a discovered endpoint" model exposed two real weaknesses that
existed **independent of ARD**. Both were fixed (2026-06); they were also the hard prerequisites
for any future consume-side ARD work.

1. **No SSRF / egress guardrails on outbound fetches → ✅ FIXED.** Added `prax/utils/ssrf.py`:
   `validate_url()` enforces an http/https scheme allowlist and blocks any host that *is* or
   *resolves to* a private/loopback/link-local/reserved address (so `localhost`, RFC1918, and the
   `169.254.169.254` cloud-metadata endpoint are refused), with an `SSRF_ALLOWED_HOSTS` escape
   hatch for dev; `safe_request()` follows redirects manually so each hop is re-validated. Applied
   to the plugin gateway `http_get`/`http_post` (`capabilities.py`) and `url_reader.fetch_markdown`.
   Gated by `SSRF_PROTECTION_ENABLED` (default **on**). (The sandbox `/v1/shell` is intentionally
   arbitrary code execution behind the root-equivalent bearer, so it is out of scope.)
2. **The plugin activation gate is behavioral, not hard → ✅ FIXED.** `plugin_import` and
   `plugin_import_activate` are now HIGH-risk (added to `action_policy._HIGH`), so importing or
   activating external plugin code is confirmation-gated. And the gate is now **load-time
   enforced**: when the import scan finds warnings, the importer flags the plugin's loader rel-keys
   via `registry.flag_requires_acknowledgement()`, and `loader.load_all()` **refuses to activate**
   any IMPORTED plugin that `requires_acknowledgement` and is not `is_warnings_acknowledged` —
   `plugin_import_activate` records that acknowledgement (and is itself HIGH-gated). The model can
   no longer load a warned plugin by cooperation alone.

(Also relevant: the HIGH-risk confirm still defaults to *unlock-all-for-the-turn*; the scoped mode
exists — `HIGH_RISK_SCOPED_CONFIRM` — but defaults off. See
[reliable-agentic-systems-bayer.md](reliable-agentic-systems-bayer.md).)

## Recommendation

- **Document, don't adopt (now).** ARD is sound and well-aligned with where the *industry* is going
  (decentralised agentic web), but it's peripheral to a single-user harness and its trust half is
  redundant with Prax's stronger, behavior-level model.
- **Revisit if** Prax ever pivots toward open-web capability consumption (e.g. wanting to use
  third-party MCP/A2A tools by intent). At that point ARD is the right standard to consume — the
  two prerequisites above (SSRF egress filtering + a hard activation gate) are now **in place**, so
  the remaining work is the catalog-fetch/trust-verify/client-runtime layer. Keep Prax's governance
  (imported→HIGH, capability gateway, glass sandbox) in front of any ARD-discovered capability:
  ARD verifies *who published it*, never *what it does*.
- **If a cheap interop gesture is ever wanted**, publishing a static `ai-catalog.json` that simply
  points at the daemon's existing `/openapi.json` + `/v1/capabilities` is a one-route change — but
  it adds re-encoded metadata, not capability.

**Update (2026-06): the prerequisite — "expose Prax's tools over a standard protocol, with a
real identity model" — is now shipped.** A bearer-gated [MCP server](../infrastructure/mcp-server.md)
(`prax/mcp/`) exposes a curated subset of Prax tools to other agents over the Model Context
Protocol, with **per-caller identity + authorization**: each client token maps to its own Prax
`user_id` and tool allowlist (write/MEDIUM tools grantable per-caller; HIGH never). That is the
layer that actually makes capabilities *usable* by other agents (ARD only makes already-usable ones
*discoverable*). ARD itself remains documented-not-adopted: it would sit on top, advertising this
MCP endpoint, once/if there's a reason to be found by registries.

## See also

- [Open Knowledge Format](open-knowledge-format.md) — the prior Google interchange-format note;
  same "assimilate as a boundary format, keep the rich internal model" conclusion.
- [Plugin Trust](../security/plugin-trust.md) · [Tool Risk](../security/tool-risk.md) — Prax's
  existing capability trust/governance model (what ARD's trust half would be redundant with).
- [Plugin Sandboxing research](plugin-sandboxing.md) — the "glass sandbox" process-isolation basis.
- prax-sandbox `docs/remote.md` (sibling repo) — the bearer-over-TLS remote daemon (the 1:1 trust
  model that beats domain-ownership for point-to-point).
