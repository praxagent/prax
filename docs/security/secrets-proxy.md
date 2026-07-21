# The secrets proxy — running a KEYLESS Prax

[← Security](README.md)

**Goal: Prax runs with *no* real API keys in its process.** A separate, small
proxy — its own isolated service in its own repo,
[`praxagent/prax-secrets-proxy`](https://github.com/praxagent/prax-secrets-proxy) —
holds the keys; Prax points its model clients at the proxy, which injects the real
credential, forwards, and streams the response back. **Prax can never read or
exfiltrate a key it never holds** — the infra-level *"make the secret unreachable"*
boundary that the [OpenAI long-horizon-safety
assessment](../research/openai-long-horizon-safety.md) names as the *real wall*
(an in-code guard the agent can edit is only a speed bump — proven by that piece's
"model split a token to evade the scanner" incident, and by the [`source_grep`
secret-leak](sandbox-execution-boundary.md) we fixed). It also caps the sandbox
env-key exfil at the root: there's no key in the env to steal.

This is **Tier 1**: the two model providers (OpenAI-compatible + Anthropic), which
is where the high-value keys live. [Tier 2](#tier-2--general-egress-future) (all
other egress + an allowlist) is future work.

## Two paths — and which Praxagent endorses

There are two ways to give Prax model keys:

| | **(A) Keys-in-Prax** | **(B) KEYLESS via the proxy** ⭐ *endorsed* |
|---|---|---|
| Where the real keys live | Prax's own `.env` | a separate, isolated service Prax can't read |
| Setup cost | none — works out of the box | run one extra service |
| If Prax is compromised/injected | keys can be exfiltrated | **nothing to steal** |
| Status | fine for trusted/solo use; **planned for deprecation** | **recommended for all new/hardened deployments** |

**Praxagent endorses path (B)** for its security properties. Path (A) stays the
zero-friction default so casual adoption isn't taxed, but it is **planned for
deprecation in a future release** — prefer (B) for anything new or exposed. Turning
(B) on needs **no code change in Prax** (`OPENAI_BASE_URL` already existed;
`ANTHROPIC_BASE_URL` was added so `ChatAnthropic` can point at the proxy too).

## Why a *separate* service and repo

The security is **process/filesystem isolation, not file naming.** Two `.env` files
in one repo is *not* a boundary — Prax's own process can `open()` any file it has
filesystem access to, so a "second env file" it can read protects nothing. Real
isolation means the keys live where **Prax's process can't reach them**: a separate
OS user, container, or host. The proxy is that separate trust domain, which is why
it ships as its **own repo** with its **own `.env`** — deploy it isolated, and that
isolation is the wall.

## How it works

```
 KEYLESS Prax                    secrets-proxy (holds the keys)         provider
 ─────────────                   ──────────────────────────────         ────────
 OPENAI_BASE_URL ─────POST──────▶  strip client auth                 ┌─ api.openai.com
   = …/openai        (placeholder   inject real OPENAI_KEY ──────────┼─ (or a 3rd-party
 OPENAI_KEY=placeholder  key)       forward + STREAM back ◀──────────┘   base URL)
 ANTHROPIC_BASE_URL ──POST──────▶  inject x-api-key + version ───────── api.anthropic.com
   = …/anthropic
```

- The proxy is a **separate process** (its own repo/env/container) whose environment
  holds the real keys. Prax's environment holds only **placeholders** (any non-empty
  string — the proxy overwrites them).
- **Allowlist by construction:** the proxy only forwards to the providers in its
  config (`/openai/…`, `/anthropic/…`). An unknown prefix is a `404`, so it can't be
  turned into an open relay.
- **Streaming** passes through unbuffered, so token streaming works.
- **Audit log:** one line per call (method / provider / path / status / request
  size) — **never** the key or the body.

Code, tests, run instructions, and the full honest-limits writeup live in the proxy
repo: [`praxagent/prax-secrets-proxy`](https://github.com/praxagent/prax-secrets-proxy).

## Run it

**1. Start the proxy** (in its own isolated environment — the ONLY place the real
keys live), per its README. From Prax you can use the convenience target once the
sibling repo is cloned next to `prax/` and its `.env` is filled in:

```bash
git clone https://github.com/praxagent/prax-secrets-proxy ../prax-secrets-proxy
cp ../prax-secrets-proxy/.env-example ../prax-secrets-proxy/.env   # add REAL keys
../prax-secrets-proxy/scripts/gen-token.sh                        # make PROXY_AUTH_TOKEN
make secrets-proxy            # runs the sibling in its own venv (127.0.0.1:8785)
# — or, stronger isolation (separate container): docker compose --profile secrets-proxy up
```

The **proxy owns an access token** (`PROXY_AUTH_TOKEN`, generated proxy-side): only
a caller presenting it can spend the keys. Copy that token — it becomes Prax's
"key" below.

**2. Point a keyless Prax at it** — in Prax's `.env`, the token + the base URLs:

```bash
OPENAI_BASE_URL=http://127.0.0.1:8785/openai
ANTHROPIC_BASE_URL=http://127.0.0.1:8785/anthropic
OPENAI_KEY=<PROXY_AUTH_TOKEN>       # the proxy validates this, then swaps in the real key
ANTHROPIC_KEY=<PROXY_AUTH_TOKEN>
# With TLS on the proxy (any cross-host link): use https:// URLs above and add
# SSL_CERT_FILE=/abs/path/to/prax-secrets-proxy/certs/proxy.crt  (httpx trusts it)
```

Now a compromised or prompt-injected Prax has no *provider* key to steal —
`printenv`, `source_grep .env`, a poisoned tool call: all it finds is the proxy
token, which only works against the token-gated, allowlisted, audited proxy.

`GET /healthz` on the proxy reports which providers have a key (booleans only, never
values).

## Security properties — and honest limits

**Guarantees:**
- Prax never holds a real model key → it cannot be *exfiltrated* from Prax, by any
  path (env read, `.env` read, sandbox tool, injection).
- The proxy strips client-supplied auth and injects the real key server-side, so a
  leaked token is worthless *as a provider key*.
- **Token-gated:** with `PROXY_AUTH_TOKEN` set, only a caller presenting the token
  reaches any provider — any other process/person on the network gets `401` (before
  the proxy even reveals a provider exists). **TLS** (optional; recommended for any
  cross-host link) keeps the token and traffic off the wire in plaintext.
- Allowlist + audit log: only the configured providers are reachable, and every
  call is recorded (key/body excluded).

**Limits (go in clear-eyed):**
- **It stops key *theft*, not key *abuse*.** A compromised Prax that still holds the
  token can make
  legitimate-looking calls it shouldn't (spam the model; smuggle data *inside* a
  request to an allowed provider). Mitigate at the proxy with rate limits, payload
  caps, and the audit log + trajectory monitoring (a tracked add-on); an optional
  policy/LLM inspector can gate flagged requests (deterministic policy first — a
  per-request LLM judge is foolable and adds latency to every call).
- **The proxy becomes the trusted component.** It holds the keys, so it must be
  isolated (its own process/user/container) and Prax must not be able to reach its
  config or env. The trust boundary moves to a small, auditable service — a good
  trade vs. trusting the whole agent.
- **Non-API secrets** Prax needs *in-process* (`FLASK_SECRET_KEY`, DB creds) can't
  be proxied. They aren't external-exfil targets, and DB access is already local.

## Production notes

See the [proxy repo](https://github.com/praxagent/prax-secrets-proxy)'s README for
the authoritative production guidance. In brief: front it with a real WSGI server
(`gunicorn`), bind it loopback or a private network the keyless Prax can reach (it
is **unauthenticated** by design — reachability is the control, like the sandbox's
loopback ports), and run it as its own container/user with the keys in *its* secret
store only.

## Tier 2 — general egress (future)

Extend the same pattern to the ~5 other key-using sites (Twilio, search APIs,
ElevenLabs): either point each at the proxy with a service tag, or run a
**transparent forward proxy** that injects auth by destination host (needs a CA the
sandbox trusts) — which also gives the **egress allowlist** that kills the
data-exfiltration leg from `sandbox-execution-boundary.md`. Two birds. Tracked in
the [adopt-tracker](../research/adopt-tracker.md).
