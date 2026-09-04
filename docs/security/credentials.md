# Credential registry

[← Security](README.md) · Related: [Secrets proxy](secrets-proxy.md)

Prax's credential settings are classified in
[`prax/services/credential_registry.py`](../../prax/services/credential_registry.py).
This page explains the available proxy paths and their limitations. A registry
classification describes an integration mechanism, not proof that a provider
has been tested or that a particular deployment isolates its secrets.

## Adding a credential

1. Add its setting in `prax/settings.py`.
2. Add a registry row with the appropriate class.
3. Implement and test any required client or proxy wiring. Record external
   verification separately in the [verification ledger](../VERIFICATION_LEDGER.md).

The [registry test](../../tests/test_credential_registry.py) checks that credential
settings have entries. It does not prove every authentication flow works.

## The three classes

- `PROXY_MODEL`: a model provider that can use a base-URL override.
- `PROXY_FORWARD`: an outbound API credential considered for host-based forward
  injection. The forward implementation exists, but unsupported flows and missing
  host mappings are skipped.
- `PROXY_LOCAL`: session signing, inbound authentication, infrastructure access,
  or a protocol that the current HTTP injectors do not support. These stay local.

## The registry

### Tier 1 — model providers

| Setting | Reverse-proxy wiring |
|---|---|
| `OPENAI_KEY` | `OPENAI_BASE_URL` points to the proxy's `/openai` route; use the proxy token as the client key. |
| `ANTHROPIC_KEY` | `ANTHROPIC_BASE_URL` points to `/anthropic`; use the proxy token as the client key. |
| `OPENROUTER_API_KEY` | Set Prax's `OPENROUTER_BASE_URL` to the proxy's OpenAI-compatible route. Configure that proxy upstream for OpenRouter and provide its real key there. |

The reverse proxy's OpenAI-compatible leg has one configured upstream. An
OpenRouter upstream is not simultaneously a direct OpenAI endpoint. Verify model
paths and routing in the intended configuration.

### Tier 2 — REST APIs

| Setting | Host / mechanism | Current limit |
|---|---|---|
| `BRAVE_API_KEY` | `api.search.brave.com`, subscription header | No successful live verification recorded. |
| `TAVILY_API_KEY` | `api.tavily.com`, bearer | No successful live verification recorded. |
| `SERPER_DEV_API_KEY` | `google.serper.dev`, API-key header | Successful historical forward request recorded. |
| `JINA_API_KEY` | `r.jina.ai`, bearer | The map names the reader host; do not assume it also covers `s.jina.ai` search. |
| `GOOGLE_API_KEY`, `GOOGLE_CSE_ID` | `www.googleapis.com`, query parameters | Does not cover every Google/Gemini host. Historical Custom Search request returned `403`. |
| `VISION_API_KEY` | Provider-dependent | No fixed host; skipped by the generated map. |
| `ELEVENLABS_API_KEY` | `api.elevenlabs.io`, `xi-api-key` | Historical request returned `401`; successful authenticated use not established. |
| `AMADEUS_API_KEY`, `AMADEUS_API_SECRET` | OAuth token exchange | Skipped; generic header injection does not perform the exchange. |
| `TWITTER_API` | `api.twitter.com`, bearer | Successful historical forward request recorded. |
| `THREADS_API` | `graph.threads.net`, bearer | No successful live verification recorded. |
| `NYT_PASSWORD` | Login/cookie session | Skipped; not an HTTP credential-injection flow. |
| `HF_TOKEN_RO` | `huggingface.co`, bearer | Historical dataset-fetch request recorded; this is not an agent-runtime guarantee. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | `api.twilio.com`, paired HTTP basic auth | Successful historical forward request recorded. |

Only configured hosts and implemented authentication schemes are covered. A
nonempty placeholder is needed for clients with credential-presence checks, but
it does not authenticate a client to the forward proxy. The checked-in forward
service has no caller token gate and passes unmatched hosts. See
[the access boundary](secrets-proxy.md#tier-2--general-egress).

### Not proxyable by the current integration

| Setting | Why it stays local |
|---|---|
| `DISCORD_BOT_TOKEN` | The Discord gateway uses the token in its connection payload. |
| `FLASK_SECRET_KEY` | Prax session signing. |
| `MCP_BEARER_TOKEN` | Authenticates inbound MCP callers. |
| `SANDBOX_DAEMON_TOKEN`, `SANDBOX_CLIENT_KEY` | Remote sandbox access. |
| `TEAMWORK_API_KEY` | Prax/TeamWork integration. |
| `NEO4J_PASSWORD` | Graph database access over Bolt. |
| `GPU_POWER_BROKER_TOKEN` | Infrastructure control. |
| `PRAX_SSH_KEY_B64`, `PLUGIN_REPO_SSH_KEY_B64` | Git-over-SSH access. |

Local credentials remain sensitive. For example, a stolen bot token can
impersonate the bot within its granted permissions, while SSH or sandbox
credentials can grant code or infrastructure access. Scope them narrowly and
rotate affected credentials after suspected compromise. Provider-key isolation
must not be described as holding no secrets or nothing of value.

## Support and verification status

Cross-checked September 4, 2026 against Prax's registry, the companion proxy source
at `3a2550cb6e5747c22cc37bbf37349196065a2775`, and the existing verification ledger.
This documentation pass did not make paid provider calls.

| Surface | Implementation | Recorded external evidence |
|---|---|---|
| Reverse model proxy | Available; authentication enforced only with nonempty `PROXY_AUTH_TOKEN` | July 22 ledger records TLS/readiness and token rejection checks; it does not establish a new full Prax completion test in this revision. |
| Forward injector | Available as an opt-in mitmproxy service; bearer, named-header, basic, and query injection | July 22 ledger records successful requests for OpenAI, Serper, Twilio, Twitter, and Hugging Face. Other paths remain partial or unverified. |
| OAuth/login/dynamic-host flows | Not supported by the generated forward map | Amadeus, NYT login, and the generic vision credential are skipped. |
| Forward caller authentication and enforced egress allowlist | Not present in the checked-in service | Must be supplied by a separate network/authentication boundary if required. |

The [verification ledger](../VERIFICATION_LEDGER.md#secrets-proxy-prax-secrets-proxy)
contains the dated observations and limits. Historical provider responses are not
claims about a current operator's credentials or account access.
