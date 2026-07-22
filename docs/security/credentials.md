# Credential registry — the single source of truth (Prax ⇄ proxy, no drift)

[← Security](README.md) · Related: [The secrets proxy](secrets-proxy.md)

**Every credential Prax supports is registered in exactly one place —
[`prax/services/credential_registry.py`](../../prax/services/credential_registry.py) —
and classified by whether/how the [secrets-proxy](secrets-proxy.md) can hold it
instead of Prax.** This page mirrors that registry for humans; the code is
authoritative.

## Why this exists (the never-drift contract)

Keyless Prax only works if there's a *complete, authoritative* answer to "what
credentials does Prax use, and can the proxy handle each?" If a new key gets added
to `settings.py` and nobody updates the proxy story, a secret silently stays in
Prax's env that the operator believed was gone — **drift**, and a security
regression.

So the invariant is enforced by a test, not by discipline:
[`tests/test_credential_registry.py`](../../tests/test_credential_registry.py)
**fails CI** if any `*_KEY` / `*_TOKEN` / `*_SECRET` / `*_API` field exists in
`settings.py` without a matching row in the registry. You cannot add a credential
and forget to classify it. (It has already earned its keep — it caught
`NEO4J_PASSWORD` and two SSH keys on first run.)

### Adding a credential (the whole procedure)
1. Add the `Field(..., alias="NEW_KEY")` in `prax/settings.py`.
2. Add a `Credential(...)` row in `credential_registry.py`, choosing a class:
   - **`PROXY_MODEL`** — a model provider reached via a base-URL override
     (proxied *today*).
   - **`PROXY_FORWARD`** — an ordinary HTTPS REST API; proxyable *only* via the
     transparent forward proxy (Tier-2, planned) — until then the key stays in Prax.
   - **`PROXY_LOCAL`** — in-process signing, an *inbound* token, or Prax's own
     co-located infra; the proxy cannot and should not hold it.
3. If it's `MODEL`/`FORWARD`, wire the proxy side to inject it.

The test enforces step 2. Keep the classification honest.

## The three classes

- **Tier 1 — model providers (proxied today).** Base-URL override
  (`OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL`); the proxy swaps the presented token
  for the real key. This is what ships in `prax-secrets-proxy` now.
- **Tier 2 — REST APIs (planned).** These SDKs don't expose a base-URL knob, so
  they can only be proxied by a **transparent forward proxy** (`HTTPS_PROXY` + a CA
  Prax trusts) that injects auth by destination host. Until that ships, these keys
  stay in Prax. See [secrets-proxy.md → Tier 2](secrets-proxy.md#tier-2--general-egress-future).
- **Not proxyable.** In-process (`FLASK_SECRET_KEY`), inbound (`MCP_BEARER_TOKEN`),
  Prax's own infra (`NEO4J_PASSWORD`, sandbox/TeamWork), or non-HTTP
  (git-over-SSH keys). By design these live only in Prax.

## The registry (mirrors the code — 29 credentials)

### Tier 1 — model providers · proxied TODAY via base-URL override

| Env | Service | Host | Inject |
|---|---|---|---|
| `OPENAI_KEY` | OpenAI (or OpenAI-compatible base URL) | api.openai.com | bearer |
| `ANTHROPIC_KEY` | Anthropic | api.anthropic.com | x-api-key |
| `OPENROUTER_API_KEY` | OpenRouter (cheap-eval) | openrouter.ai | bearer¹ |

¹ Proxyable via the openai leg (`PROXY_OPENAI_BASE_URL=…openrouter…`) or its own
upstream; needs Prax's openrouter base URL pointed at the proxy.

### Tier 2 — REST APIs · proxyable only via the transparent forward proxy (PLANNED)

| Env | Service | Purpose | Host | Inject |
|---|---|---|---|---|
| `BRAVE_API_KEY` | Brave Search | web search | api.search.brave.com | `X-Subscription-Token` |
| `TAVILY_API_KEY` | Tavily | web search | api.tavily.com | bearer |
| `SERPER_DEV_API_KEY` | Serper.dev | web search | google.serper.dev | `X-API-KEY` |
| `JINA_API_KEY` | Jina AI | URL reader + search | r.jina.ai | bearer (reader works keyless) |
| `GOOGLE_API_KEY` | Google | programmable search / APIs | www.googleapis.com | `?key=` |
| `GOOGLE_CSE_ID` | Google CSE | search engine id | www.googleapis.com | `?cx=` |
| `VISION_API_KEY` | Vision provider | image analysis | — | bearer |
| `ELEVENLABS_API_KEY` | ElevenLabs | text-to-speech | api.elevenlabs.io | `xi-api-key` |
| `AMADEUS_API_KEY` | Amadeus | travel search (OAuth id) | api.amadeus.com | basic |
| `AMADEUS_API_SECRET` | Amadeus | travel search (OAuth secret) | api.amadeus.com | basic |
| `TWITTER_API` | X / Twitter v2 | fetch tweets/threads | api.twitter.com | bearer |
| `THREADS_API` | Meta Threads | fetch Threads posts | graph.threads.net | bearer |
| `NYT_PASSWORD` | New York Times | news access | www.nytimes.com | basic |
| `HF_TOKEN_RO` | Hugging Face | gated dataset fetch² | huggingface.co | bearer |
| `TWILIO_ACCOUNT_SID` | Twilio | SMS/voice (basic user) | api.twilio.com | basic |
| `TWILIO_AUTH_TOKEN` | Twilio | SMS/voice (basic pass) | api.twilio.com | basic |

² Used at dataset-fetch time (scripts), not agent runtime — low priority.

_Verified live 2026-07-22: Serper, OpenAI, and **Twilio** (basic auth) all inject +
work through the forward proxy; **ElevenLabs** injects correctly but the held key is
stale (401 → rotate)._

### Not proxyable — stays in Prax by design

| Env | Why it stays local |
|---|---|
| `DISCORD_BOT_TOKEN` | **non-HTTP** — the bot gateway is a websocket carrying the token in its IDENTIFY payload (no header to inject), and REST wants `Authorization: Bot <token>` (prefix generic injection omits → 401). Verified 401 through the proxy 2026-07-22. |
| `FLASK_SECRET_KEY` | in-process session signing; never egresses |
| `MCP_BEARER_TOKEN` | **inbound** — authenticates other agents *to* Prax |
| `SANDBOX_DAEMON_TOKEN` / `SANDBOX_CLIENT_KEY` | Prax's own remote-sandbox infra (bearer / mTLS key) |
| `TEAMWORK_API_KEY` | Prax's own co-located UI (loopback/tailnet) |
| `NEO4J_PASSWORD` | Prax's own graph DB over `bolt://` |
| `GPU_POWER_BROKER_TOKEN` | infra control token (reclassify FORWARD if it ever calls a third party) |
| `PRAX_SSH_KEY_B64` / `PLUGIN_REPO_SSH_KEY_B64` | git-over-SSH — not an HTTP API the egress proxy can inject |

## Honest status

**Today, keyless covers the 3 model keys** (the highest-value theft target) — that's
real and worth doing. The 17 Tier-2 REST keys can only *all* move to the proxy once
the transparent forward proxy is built; `DISCORD_BOT_TOKEN`'s gateway and the 9
local credentials fundamentally can't. So "Prax holds zero secrets" is the goal, and
this registry is the honest, enforced map of how far along the path each credential
is — with a test making sure the map never lies.
