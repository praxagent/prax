# Prax & the interoperability frontier

[← Architecture](README.md)

Where Prax sits in the emerging **agent-interoperability** ecosystem — what it
already does, and the frontier it can move onto. (Goal: *"Prax becomes part of the
interoperability frontier."*)

> **Note on scope.** This was prompted by [Pangram Space](https://www.pangram.com/pangram-space),
> which turned out to be an **interpretability** project (visualizing an AI-text-
> detector's activations), **not** an interoperability standard — there's nothing
> there to join. It belongs (if anywhere) to the LLM-behavior/interpretability lane,
> not here. But the *goal* it surfaced is real, so this doc maps Prax against it.

## The five axes of agent interoperability

1. **Expose** — let other agents/products use *your* capabilities.
2. **Consume** — use *other* agents'/services' capabilities.
3. **Discover** — find capabilities across orgs/the web, and verify them.
4. **Interchange** — move knowledge/artifacts between systems in a portable format.
5. **Identity & provenance** — authenticate who/what produced a request or a piece
   of content (credentials, signing, content authentication).

## Where Prax is today [verified]

| Axis | Prax today |
|---|---|
| **Expose** | ✅ **Strong.** The MCP server (`prax/mcp/`, [`infrastructure/mcp-server.md`](../infrastructure/mcp-server.md)) exposes a curated, bearer-gated, **per-caller-identity** subset of Prax tools to *other* agents over JSON-RPC MCP. Governance stays in front; write tools grantable per-caller, HIGH never. This is the "make Prax usable by other agents" surface. |
| **Consume** | ⚠️ **Gap.** Prax is an MCP **server**, not yet an MCP **client** — it can't yet treat *another* agent's MCP server (or an A2A peer) as a tool source. `prax/mcp/clients.py` is the *caller registry*, not an outbound client. |
| **Discover** | ◻️ **Assessed, deferred.** Google's ARD ([`research/agentic-resource-discovery.md`](../research/agentic-resource-discovery.md)) — publish/discover/verify capabilities via `ai-catalog.json` — verdict was *document-don't-adopt-now* (cross-org web discovery isn't a single-user harness's problem; its trust half is redundant with Prax's imported→HIGH + capability gateway). |
| **Interchange** | ✅ **Shipped.** The OKF bridge (`knowledge_export_okf`/`import`, [`research/open-knowledge-format.md`](../research/open-knowledge-format.md)) makes the concept graph round-trippable to portable markdown bundles. |
| **Identity & provenance** | ⚠️ **Partial.** Per-caller MCP identity + ephemeral capability TTLs exist (inbound). **No outbound content provenance** — Prax doesn't sign/attest the content it generates across channels. |

## The frontier (ranked by fit)

1. **MCP-as-client / A2A (the "Consume" axis) — highest-value, most natural.**
   Prax is hub-and-spoke; an outbound MCP/A2A client makes *any* external agent or
   MCP server a **spoke** — Prax consumes others' capabilities the same way it
   exposes its own. This is the cleanest "join the frontier" move and reuses the
   delegation pattern. (Tool-overload still applies — gate behind the on-demand
   tool-search / hub-and-spoke discipline.) **Tracked: [`../IDEAS_BACKLOG.md`](../IDEAS_BACKLOG.md) #27.**
2. **Content provenance / credentials (the "Identity & provenance" axis).** Prax
   *produces* content across Discord/SMS/TeamWork/notes. Participating in content-
   authentication standards (C2PA Content Credentials, signed attestations of
   "produced by Prax/agent X") is a real interop frontier — and the honest flip
   side of the AI-detection world Pangram lives in (interpretability shows AI text
   is reliably detectable → aim to be *verifiably attributable*, not undetectable;
   [`../research/pangram-detector-interpretability.md`](../research/pangram-detector-interpretability.md)).
   Lets downstream systems verify provenance instead of guessing. **Tracked: [`../IDEAS_BACKLOG.md`](../IDEAS_BACKLOG.md) #28.**
3. **Capability discovery (the "Discover" axis) — when cross-org matters.** ARD (or
   a successor) becomes relevant if Prax ever needs to *discover* third-party
   capabilities at web scale rather than being handed endpoints. Deferred until
   there's a multi-org use case; the assessment is already on file.

## Verdict

Prax is **already a first-class interop *provider*** (MCP server + OKF interchange)
— that half of the frontier is shipped. The gap is the **consumer** half: an
outbound **MCP/A2A client** is the single highest-leverage step to "part of the
interoperability frontier," because it closes the loop (expose *and* consume) using
machinery Prax already has (hub-and-spoke, governed tools, per-caller identity).
Content provenance is the natural second frontier. Capability discovery (ARD) stays
deferred until a cross-org need is concrete.

See also: [`infrastructure/mcp-server.md`](../infrastructure/mcp-server.md),
[`research/open-knowledge-format.md`](../research/open-knowledge-format.md),
[`research/agentic-resource-discovery.md`](../research/agentic-resource-discovery.md).
