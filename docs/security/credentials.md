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

The [registry test](../../tests/test_credential_registry.py) enforces step 2: it
**fails CI** if any `*_KEY` / `*_TOKEN` / `*_SECRET` / `*_API` field exists in
`settings.py` without a matching registry row (it caught `NEO4J_PASSWORD` and two
SSH keys on its first run). It does not prove every authentication flow works.

**Known gap (2026-09): the guard sees `settings.py` fields only.** A credential
read straight from the process environment never enters the registry:
`SENDGRID_API_KEY` (`prax/readers/reader_functions.py`, `os.environ.get`),
`HF_TOKEN` / `HUGGINGFACE_TOKEN` (`prax/eval/benchmarks/datasets.py`; only
`HF_TOKEN_RO` is registered), `ARC_API_KEY` (`prax/eval/arc3/sdk_agent.py`), and
the deployment-level `TS_AUTHKEY` (compose) and `NGROK_AUTHTOKEN` (`.env-example`)
are all outside it. "Exactly one place" is true for what Pydantic loads, not for
everything the codebase reads. Also outside it by construction: the
per-repository ed25519 **deploy keys** that attaching a git repo to a Library
space generates (`prax/services/space_repos.py`, written under
`~/.prax/git-keys/{user}/{space}/{repo}` with mode 0600, never inside the
workspace). They are not env-var credentials, so they have no registry row and
the drift-guard cannot see them; they belong to the same `PROXY_LOCAL` class as
`PRAX_SSH_KEY_B64` (git-over-SSH, not proxyable), and the mitigation is scope —
one key per repository, so a leak exposes that repo and nothing else. Design:
[`space-git-repos.md`](space-git-repos.md).

## The three classes

- `PROXY_MODEL`: a model provider that can use a base-URL override
  (`OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL`); the reverse proxy swaps the
  presented token for the real key. Proxied today.
- `PROXY_FORWARD`: an outbound REST credential whose SDK exposes no base-URL
  knob, so it is proxyable only by the transparent forward proxy (`HTTPS_PROXY`
  plus a CA Prax trusts — the opt-in `forward` profile of `prax-secrets-proxy`,
  see [deployment-topology.md](deployment-topology.md)) injecting by destination
  host. The forward implementation exists, but unsupported flows and missing
  host mappings are skipped, and a deployment that does not run it keeps these
  keys in Prax.
- `PROXY_LOCAL`: session signing, inbound authentication, infrastructure access,
  or a protocol that the current HTTP injectors do not support (git-over-SSH,
  the Discord gateway). These stay local by design.

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
it does not authenticate a client to the forward proxy — that is
`PROXY_FORWARD_AUTH_TOKEN`, carried in the proxy URL, which the checked-in forward
service enforces **only when set** (empty = open to any caller that can reach the
port; added in proxy commit `cf86731`, after the `3a2550c` revision this page was
first cross-checked against). It passes unmatched hosts untouched, so it is not
an egress allowlist. See [the access boundary](secrets-proxy.md#tier-2--general-egress).

`TWILIO_AUTH_TOKEN` is **also the inbound webhook-signature secret**:
`prax/blueprints/twilio_auth.py` validates `X-Twilio-Signature` with this same
value, and an inbound signature check cannot be proxied. If only the proxy holds
the real token, Prax's copy is empty (validation skipped) or a placeholder
(validation can never pass). **Known gap (2026-09):** there is no keyless
configuration in which Twilio signature validation works; see
[configuration.md → Twilio](configuration.md#option-c-twilio-voice--sms) for the
related `https`-behind-ngrok failure.

⚠️ **Google keys: the recommended stance is to hold none (2026-07-22).** They
remain classified `PROXY_FORWARD` (forward-proxyable in principle). Google Cloud
billing is **not** a hard-capped pay-as-you-go product — a runaway loop or a
prompt-injected agent could run up **unbounded** charges with no ceiling to stop
it, which is a categorically worse failure mode than a metered per-call API. So
the safe default is to hold no Google key at all: Prax degrades to the keyless
search providers and non-Google vision. Only set `GOOGLE_API_KEY` behind a Google
Cloud budget/quota cap you have configured yourself. Whether a given proxy
deployment actually holds one is the operator's choice and lives in the proxy's
own `.env`; this page cannot assert its absence.

### Not proxyable by the current integration

| Setting | Why it stays local |
|---|---|
| `DISCORD_BOT_TOKEN` | The Discord gateway uses the token in its websocket IDENTIFY payload (no header to inject), and REST wants `Authorization: Bot <token>`, which generic prefix injection omits — verified `401` through the proxy 2026-07-22. |
| `FLASK_SECRET_KEY` | Prax session signing. |
| `MCP_BEARER_TOKEN` | Authenticates inbound MCP callers. |
| `SANDBOX_DAEMON_TOKEN`, `SANDBOX_CLIENT_KEY` | Remote sandbox access. |
| `TEAMWORK_API_KEY` | Prax/TeamWork integration. |
| `PRAX_API_KEY` | Inbound to Prax itself — checked on `/teamwork/*`, `/plugins/*`, `/api/users/*` when set (`prax/blueprints/inbound_auth.py`, opt-in, default empty); never sent anywhere. |
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
| Forward caller authentication | Present since proxy commit `cf86731` (`PROXY_FORWARD_AUTH_TOKEN`; `407` without it) — open when the token is empty | Not part of the July 22 ledger run; the `3a2550c` cross-check above predates it. Verify in your deployment. |
| Enforced egress allowlist | Not present — unmatched hosts pass through the forward service | Must be supplied by a separate network boundary if required. |

The [verification ledger](../VERIFICATION_LEDGER.md#secrets-proxy-prax-secrets-proxy)
contains the dated observations and limits. Historical provider responses are not
claims about a current operator's credentials or account access.
