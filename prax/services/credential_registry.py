"""The canonical registry of every credential Prax supports — the ONE source of
truth so Prax and the secrets-proxy never drift.

Why this exists
---------------
Prax runs KEYLESS when the [secrets-proxy](../../docs/security/secrets-proxy.md)
holds the real keys. For that to be safe *and* complete, there must be a single,
authoritative answer to "what credentials does Prax use, and can the proxy handle
each?" — otherwise a new key gets added to ``settings.py`` and silently isn't
proxied (drift), leaving a secret in Prax's env that the operator thought was gone.

This module is that answer. Every outbound/inbound secret is listed here with a
``proxy`` classification. A drift-guard test
(``tests/test_credential_registry.py``) fails CI if a credential field is added to
``settings.py`` without a matching entry here — so the two can never silently
diverge. The human-readable mirror is ``docs/security/credentials.md``.

How to add a credential
-----------------------
1. Add the ``Field(..., alias="NEW_KEY")`` in ``prax/settings.py``.
2. Add a ``Credential(...)`` row below, classifying how (if at all) the proxy
   injects it.
3. If it's ``PROXY_FORWARD``/``PROXY_MODEL``, wire the proxy side
   (``prax-secrets-proxy``) to inject it.
The drift test enforces step 2; keep it honest.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── proxy classification ────────────────────────────────────────────────────
# How, if at all, the secrets-proxy can hold this credential instead of Prax.
PROXY_MODEL = "model"      # Tier-1, SHIPPED: model provider reached via a base-URL
                           #   override (OPENAI_BASE_URL / ANTHROPIC_BASE_URL). The
                           #   proxy swaps the presented token for the real key.
PROXY_FORWARD = "forward"  # Tier-2, PLANNED: an ordinary HTTPS REST call to a fixed
                           #   host. Proxyable only via a transparent forward proxy
                           #   (HTTPS_PROXY + a trusted CA) that injects auth by host
                           #   — NOT by a base-URL override (these SDKs don't expose
                           #   one). Until Tier-2 ships, the key stays in Prax.
PROXY_LOCAL = "local"      # NOT an outbound third-party secret — in-process signing,
                           #   an INBOUND auth token, or a credential for Prax's own
                           #   co-located infra. Never leaves as a proxied call, so
                           #   the proxy cannot and should not hold it. Stays in Prax.


@dataclass(frozen=True)
class Credential:
    env: str                 # environment-variable name (matches the settings alias)
    service: str             # human-readable service name
    purpose: str             # what Prax feature needs it
    proxy: str               # PROXY_MODEL | PROXY_FORWARD | PROXY_LOCAL
    host: str | None = None  # host it authenticates to (forward-proxy routing key)
    inject: str | None = None  # how the secret is presented: bearer | x-api-key |
                               #   basic | header:<Name> | query:<param>
    caveat: str = ""         # honest nuance (gateway websocket, fetch-time-only, …)


# ── THE REGISTRY ────────────────────────────────────────────────────────────
# Ordered by tier. Keep this list complete: the drift test fails if a credential
# in settings.py is missing here.
REGISTRY: tuple[Credential, ...] = (
    # ── Tier 1 — model providers (proxied TODAY via base-URL override) ──
    Credential("OPENAI_KEY", "OpenAI (or an OpenAI-compatible base URL)",
               "Primary LLM provider", PROXY_MODEL,
               host="api.openai.com", inject="bearer"),
    Credential("ANTHROPIC_KEY", "Anthropic",
               "Claude LLM provider", PROXY_MODEL,
               host="api.anthropic.com", inject="x-api-key"),
    Credential("OPENROUTER_API_KEY", "OpenRouter",
               "Cheap/prepaid eval provider (OpenAI-compatible)", PROXY_MODEL,
               host="openrouter.ai", inject="bearer",
               caveat="Proxyable via the openai leg with PROXY_OPENAI_BASE_URL=openrouter, "
                      "or its own upstream; needs Prax's openrouter base-URL pointed at the proxy."),

    # ── Tier 2 — REST APIs (proxyable only via the transparent forward proxy) ──
    Credential("BRAVE_API_KEY", "Brave Search", "Web search (SEARCH_PROVIDER=brave)",
               PROXY_FORWARD, host="api.search.brave.com", inject="header:X-Subscription-Token"),
    Credential("TAVILY_API_KEY", "Tavily", "Web search (SEARCH_PROVIDER=tavily)",
               PROXY_FORWARD, host="api.tavily.com", inject="bearer"),
    Credential("SERPER_DEV_API_KEY", "Serper.dev", "Web search (SEARCH_PROVIDER=serper)",
               PROXY_FORWARD, host="google.serper.dev", inject="header:X-API-KEY"),
    Credential("JINA_API_KEY", "Jina AI", "URL reader + Jina search",
               PROXY_FORWARD, host="r.jina.ai", inject="bearer",
               caveat="Reader works keyless; the key only raises quota / enables Jina search."),
    Credential("GOOGLE_API_KEY", "Google", "Programmable Search / other Google APIs",
               PROXY_FORWARD, host="www.googleapis.com", inject="query:key"),
    Credential("GOOGLE_CSE_ID", "Google Custom Search Engine", "CSE engine id (paired with GOOGLE_API_KEY)",
               PROXY_FORWARD, host="www.googleapis.com", inject="query:cx",
               caveat="Not strictly secret (an engine id), but travels with GOOGLE_API_KEY."),
    Credential("VISION_API_KEY", "Vision provider", "Image analysis (analyze_image)",
               PROXY_FORWARD, host=None, inject="bearer"),
    Credential("ELEVENLABS_API_KEY", "ElevenLabs", "Text-to-speech / voice",
               PROXY_FORWARD, host="api.elevenlabs.io", inject="header:xi-api-key"),
    Credential("AMADEUS_API_KEY", "Amadeus", "Travel/flight search (OAuth client id)",
               PROXY_FORWARD, host="api.amadeus.com", inject="basic"),
    Credential("AMADEUS_API_SECRET", "Amadeus", "Travel/flight search (OAuth client secret)",
               PROXY_FORWARD, host="api.amadeus.com", inject="basic"),
    Credential("TWITTER_API", "X / Twitter API v2", "Fetch tweets/threads",
               PROXY_FORWARD, host="api.twitter.com", inject="bearer"),
    Credential("THREADS_API", "Meta Threads Graph API", "Fetch Threads posts",
               PROXY_FORWARD, host="graph.threads.net", inject="bearer"),
    Credential("NYT_PASSWORD", "New York Times", "News/login-gated article access",
               PROXY_FORWARD, host="www.nytimes.com", inject="basic"),
    Credential("HF_TOKEN_RO", "Hugging Face (read-only)", "Download gated eval datasets",
               PROXY_FORWARD, host="huggingface.co", inject="bearer",
               caveat="Used at dataset-fetch time (scripts), NOT agent runtime — low priority."),
    Credential("TWILIO_ACCOUNT_SID", "Twilio", "SMS/voice (account id, basic-auth user)",
               PROXY_FORWARD, host="api.twilio.com", inject="basic"),
    Credential("TWILIO_AUTH_TOKEN", "Twilio", "SMS/voice (basic-auth password)",
               PROXY_FORWARD, host="api.twilio.com", inject="basic"),
    Credential("DISCORD_BOT_TOKEN", "Discord", "Discord bot channel",
               PROXY_FORWARD, host="discord.com", inject="header:Authorization",
               caveat="REST is forward-proxyable, but the bot GATEWAY is a persistent "
                      "websocket (wss://gateway.discord.gg) — hard to MITM cleanly. "
                      "Treat as effectively unproxyable until the gateway path is solved."),

    # ── Not proxyable — in-process signing, INBOUND auth, or Prax's own infra ──
    Credential("FLASK_SECRET_KEY", "Flask", "Session cookie signing (in-process)",
               PROXY_LOCAL, caveat="Never leaves the process; not an external-exfil target."),
    Credential("MCP_BEARER_TOKEN", "Prax MCP server", "INBOUND: authenticates other agents TO Prax",
               PROXY_LOCAL, caveat="Inbound, not outbound — the proxy is for outbound egress."),
    Credential("SANDBOX_DAEMON_TOKEN", "Prax remote sandbox daemon", "Bearer for a remote sandbox",
               PROXY_LOCAL, caveat="Prax's own co-located infra, not a third-party provider."),
    Credential("SANDBOX_CLIENT_KEY", "Prax remote sandbox daemon", "Client mTLS key for the sandbox",
               PROXY_LOCAL, caveat="A TLS client key, not an API token; Prax's own infra."),
    Credential("TEAMWORK_API_KEY", "TeamWork UI", "Prax↔TeamWork API (own UI)",
               PROXY_LOCAL, caveat="Prax's own co-located UI, typically loopback/tailnet."),
    Credential("GPU_POWER_BROKER_TOKEN", "GPU power broker", "Optional GPU-broker control token",
               PROXY_LOCAL, caveat="Infra control token; classify FORWARD if it ever calls a third party."),
    Credential("NEO4J_PASSWORD", "Neo4j", "Knowledge-graph database password",
               PROXY_LOCAL, caveat="Prax's own co-located DB (bolt://), not a third-party HTTP API."),
    Credential("PRAX_SSH_KEY_B64", "Git (self-improve)", "SSH deploy key for pushing Prax's own repo",
               PROXY_LOCAL, caveat="Git-over-SSH, not an HTTP API — the egress proxy can't inject it."),
    Credential("PLUGIN_REPO_SSH_KEY_B64", "Git (plugins)", "SSH key for the plugin repo",
               PROXY_LOCAL, caveat="Git-over-SSH, not an HTTP API — the egress proxy can't inject it."),
)

# Non-secret settings whose env alias *looks* like a credential but ISN'T an
# outbound key — the drift test skips these. Keep it small + justified.
NON_CREDENTIAL_ALIASES: frozenset[str] = frozenset({
    "MCP_TOKEN_EXPIRES_AT",       # a timestamp, not a secret
    "MCP_TOKEN_EXPIRY_ENABLED",   # a bool flag
    "SITES_CREDENTIALS_PATH",     # a filesystem path (points at a creds file)
    "GIT_AUTHOR_NAME",            # not a secret
    "GIT_AUTHOR_EMAIL",           # not a secret
})


# ── helpers ─────────────────────────────────────────────────────────────────

def by_env(env: str) -> Credential | None:
    return next((c for c in REGISTRY if c.env == env), None)


def all_envs() -> frozenset[str]:
    return frozenset(c.env for c in REGISTRY)


def model_credentials() -> tuple[Credential, ...]:
    """Tier-1 — proxied today via a base-URL override."""
    return tuple(c for c in REGISTRY if c.proxy == PROXY_MODEL)


def forward_credentials() -> tuple[Credential, ...]:
    """Tier-2 — proxyable only via the transparent forward proxy."""
    return tuple(c for c in REGISTRY if c.proxy == PROXY_FORWARD)


def local_credentials() -> tuple[Credential, ...]:
    """Not proxyable by design (in-process / inbound / own-infra)."""
    return tuple(c for c in REGISTRY if c.proxy == PROXY_LOCAL)
