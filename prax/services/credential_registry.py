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
    Credential("GOOGLE_API_KEY", "Google", "Programmable Search / Gemini vision / other Google APIs",
               PROXY_FORWARD, host="www.googleapis.com", inject="query:key",
               caveat="Deliberately left UNSET (2026-07-22): Google Cloud billing is NOT a "
                      "hard-capped pay-as-you-go product — a runaway loop or prompt-injected "
                      "agent could run up unbounded charges with no ceiling to stop it. "
                      "Forward-proxyable in principle, but the safer default is to hold no "
                      "Google key at all; Prax degrades to the keyless search providers and "
                      "non-Google vision. Only set it behind a Google Cloud budget/quota cap "
                      "you have configured yourself."),
    Credential("GOOGLE_CSE_ID", "Google Custom Search Engine", "CSE engine id (paired with GOOGLE_API_KEY)",
               PROXY_FORWARD, host="www.googleapis.com", inject="query:cx",
               caveat="Not strictly secret (an engine id), but travels with GOOGLE_API_KEY — "
                      "which is deliberately unset (unbounded-billing risk). See GOOGLE_API_KEY."),
    Credential("VISION_API_KEY", "Vision provider", "Image analysis (analyze_image)",
               PROXY_FORWARD, host=None, inject="bearer"),
    Credential("ELEVENLABS_API_KEY", "ElevenLabs", "Text-to-speech / voice",
               PROXY_FORWARD, host="api.elevenlabs.io", inject="header:xi-api-key"),
    Credential("AMADEUS_API_KEY", "Amadeus", "Travel/flight search (OAuth client id)",
               PROXY_FORWARD, host="api.amadeus.com", inject="oauth2",
               caveat="OAuth2 token exchange (POST id+secret → bearer), not header "
                      "injection — a transparent proxy can't do the exchange; skipped by the map."),
    Credential("AMADEUS_API_SECRET", "Amadeus", "Travel/flight search (OAuth client secret)",
               PROXY_FORWARD, host="api.amadeus.com", inject="oauth2",
               caveat="See AMADEUS_API_KEY — OAuth2 exchange, not simple injection."),
    Credential("TWITTER_API", "X / Twitter API v2", "Fetch tweets/threads",
               PROXY_FORWARD, host="api.twitter.com", inject="bearer"),
    Credential("THREADS_API", "Meta Threads Graph API", "Fetch Threads posts",
               PROXY_FORWARD, host="graph.threads.net", inject="bearer"),
    Credential("NYT_PASSWORD", "New York Times", "News/login-gated article access",
               PROXY_FORWARD, host="www.nytimes.com", inject="login",
               caveat="A site login (cookie session), not header auth — a transparent "
                      "proxy can't inject it; skipped by the map."),
    Credential("HF_TOKEN_RO", "Hugging Face (read-only)", "Download gated eval datasets",
               PROXY_FORWARD, host="huggingface.co", inject="bearer",
               caveat="Used at dataset-fetch time (scripts), NOT agent runtime — low priority."),
    Credential("TWILIO_ACCOUNT_SID", "Twilio", "SMS/voice (account id, basic-auth user)",
               PROXY_FORWARD, host="api.twilio.com", inject="basic:user"),
    Credential("TWILIO_AUTH_TOKEN", "Twilio", "SMS/voice (basic-auth password)",
               PROXY_FORWARD, host="api.twilio.com", inject="basic:pass"),

    # ── Not proxyable — in-process signing, INBOUND auth, own infra, or non-HTTP ──
    Credential("DISCORD_BOT_TOKEN", "Discord", "Discord bot channel",
               PROXY_LOCAL,
               caveat="Stays in Prax by necessity (2026-07-22): the bot GATEWAY is a persistent "
                      "websocket (wss://gateway.discord.gg) that carries the token INSIDE the "
                      "IDENTIFY payload, not an HTTP header — nothing for a header-injecting "
                      "proxy to touch; and even Discord's REST wants 'Authorization: Bot <token>' "
                      "(a prefix generic injection doesn't add → 401). The bot needs the token "
                      "locally for the gateway regardless. RISK: this is a LOWER blast radius "
                      "than a cloud or model key — a stolen bot token can't spend money or reach "
                      "any other provider; it's scoped to the guilds the bot is already in — but "
                      "it is NOT harmless: it grants full impersonation of the bot (read/send in "
                      "every server it's in, DM users) until the token is regenerated. So the "
                      "keyless-Prax invariant genuinely does not cover this one; mitigate with "
                      "minimal bot permissions/intents and rotate the token if Prax is ever "
                      "suspected of compromise."),
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


# ── forward-map generation (the never-drift link to the MITM proxy) ─────────
# The transparent forward proxy (prax-secrets-proxy, forward mode) injects
# credentials by destination host from a JSON map. We GENERATE that map from this
# registry so the proxy config can't drift from Prax's own list of credentials.

def build_forward_map() -> tuple[list[dict], list[tuple[str, str]]]:
    """Return (rules, skipped) for the forward proxy.

    Covers BOTH the model providers and the REST APIs, so forward mode alone
    proxies *all* injectable egress in one box (HTTPS_PROXY, no base-URL needed) —
    the model keys are ordinary bearer/x-api-key-by-host injections too. ``rules``
    is the JSON the proxy's ForwardInjector loads. ``skipped`` is ``[(env,
    reason)]`` for creds a transparent proxy *can't* inject (OAuth token-exchange,
    site login, or no fixed host) — kept honest, not hidden.
    """
    rules: list[dict] = []
    skipped: list[tuple[str, str]] = []
    basic_user: dict[str, str] = {}
    basic_pass: dict[str, str] = {}

    for c in (*model_credentials(), *forward_credentials()):
        scheme = c.inject or ""
        if not c.host:
            skipped.append((c.env, "no fixed host"))
            continue
        if scheme in ("oauth2", "login"):
            skipped.append((c.env, scheme))
            continue
        if scheme == "basic:user":
            basic_user[c.host] = c.env
        elif scheme == "basic:pass":
            basic_pass[c.host] = c.env
        elif scheme == "bearer":
            rules.append({"host": c.host, "scheme": "bearer", "key_env": c.env})
        elif scheme == "x-api-key":
            rules.append({"host": c.host, "scheme": "header:x-api-key", "key_env": c.env})
        elif scheme.startswith(("header:", "query:")):
            rules.append({"host": c.host, "scheme": scheme, "key_env": c.env})
        else:
            skipped.append((c.env, f"unknown-scheme:{scheme}"))

    for host in sorted(set(basic_user) | set(basic_pass)):
        rules.append({"host": host, "scheme": "basic",
                      "user_env": basic_user.get(host), "pass_env": basic_pass.get(host)})
    return rules, skipped


def export_forward_map(path: str) -> tuple[int, list[tuple[str, str]]]:
    """Write the forward-map JSON to *path*. Returns (rule_count, skipped)."""
    import json
    rules, skipped = build_forward_map()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rules, fh, indent=2)
        fh.write("\n")
    return len(rules), skipped


def _main() -> None:  # pragma: no cover - thin CLI
    import argparse
    p = argparse.ArgumentParser(description="Prax credential registry tools")
    p.add_argument("--export-forward-map", metavar="PATH",
                   help="Write the secrets-proxy forward-map JSON from the registry")
    args = p.parse_args()
    if args.export_forward_map:
        n, skipped = export_forward_map(args.export_forward_map)
        print(f"Wrote {n} forward-map rules to {args.export_forward_map}")
        if skipped:
            print("Skipped (a transparent proxy can't inject these — they stay in Prax):")
            for env, reason in skipped:
                print(f"  - {env}: {reason}")
    else:
        p.print_help()


if __name__ == "__main__":  # pragma: no cover
    _main()
