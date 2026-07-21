# The secrets proxy — running a KEYLESS Prax

[← Security](README.md)

**Goal: Prax runs with *no* real API keys in its process.** A separate, small
proxy holds the keys; Prax points its model clients at the proxy, which injects
the real credential, forwards, and streams the response back. **Prax can never
read or exfiltrate a key it never holds** — the infra-level *"make the secret
unreachable"* boundary that the [OpenAI long-horizon-safety
assessment](../research/openai-long-horizon-safety.md) names as the *real wall*
(an in-code guard the agent can edit is only a speed bump — proven by that piece's
"model split a token to evade the scanner" incident, and by the [`source_grep`
secret-leak](sandbox-execution-boundary.md) we fixed). It also caps the sandbox
env-key exfil at the root: there's no key in the env to steal.

This is **Tier 1**: the two model providers (OpenAI-compatible + Anthropic), which
is where the high-value keys live. [Tier 2](#tier-2--general-egress-future) (all
other egress + an allowlist) is future work.

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

- The proxy is a **separate process** whose environment holds the real keys. Prax's
  environment holds only **placeholders** (any non-empty string — the proxy
  overwrites them).
- Prax needs **no code change** to use it — `OPENAI_BASE_URL` already existed;
  `ANTHROPIC_BASE_URL` was added to point `ChatAnthropic` at the proxy.
- **Allowlist by construction:** the proxy only forwards to the providers in its
  config (`/openai/…`, `/anthropic/…`). An unknown prefix is a `404`, so it can't
  be turned into an open relay.
- **Streaming** passes through unbuffered, so token streaming works.
- **Audit log:** one line per call (method / provider / path / status / request
  size) — **never** the key or the body.

Code: `prax/secrets_proxy/` (`config.py`, `app.py`, `__main__.py`). Tests:
`tests/test_secrets_proxy.py` (keyless; pins injection, auth-stripping, the
allowlist, streaming, and that the key/body never appear in the audit log).

## Run it — TWO env files

The whole point is separation, so the real keys and Prax's config live in
**different files** (and different processes):

| File | Loaded by | Holds |
|---|---|---|
| **`.env-proxy`** (from `.env-proxy-example`, **gitignored**) | the secrets proxy only | the **REAL** `OPENAI_KEY`/`ANTHROPIC_KEY` + proxy config |
| **`.env`** (from `.env-example`) | Prax only | Prax config + **placeholder** keys + the `*_BASE_URL`s |

**1. Start the proxy** — the ONLY place the real keys live:

```bash
cp .env-proxy-example .env-proxy      # then put the REAL keys in .env-proxy
make secrets-proxy                    # loads .env-proxy; listens 127.0.0.1:8785
```

`.env-proxy` keys: `OPENAI_KEY`, `ANTHROPIC_KEY` (the real ones);
`PROXY_HOST`/`PROXY_PORT` (default `127.0.0.1:8785`); `PROXY_OPENAI_BASE_URL` /
`PROXY_ANTHROPIC_BASE_URL` (default the real providers — set the OpenAI one to a
third-party like OpenRouter/DeepSeek if you route there); `PROXY_AUDIT_LOG` (a
file path for the audit trail); `PROXY_TIMEOUT_S`; `PROXY_ENV_FILE` (override which
file to load). Explicit shell env still wins, e.g. `OPENAI_KEY=… make secrets-proxy`.

**2. Point a keyless Prax at it** — in Prax's `.env`, *placeholders* + the base URLs:

```bash
OPENAI_BASE_URL=http://127.0.0.1:8785/openai
ANTHROPIC_BASE_URL=http://127.0.0.1:8785/anthropic
OPENAI_KEY=proxy-placeholder        # any non-empty string; the proxy overwrites it
ANTHROPIC_KEY=proxy-placeholder
```

Now a compromised or prompt-injected Prax has nothing to steal — `printenv`,
`source_grep .env`, a poisoned tool call: all it finds is `proxy-placeholder`.

`GET /healthz` reports which providers have a key (booleans only, never values).

## Security properties — and honest limits

**Guarantees:**
- Prax never holds a real model key → it cannot be *exfiltrated* from Prax, by any
  path (env read, `.env` read, sandbox tool, injection).
- The proxy strips client-supplied auth and injects the real key server-side, so a
  leaked placeholder is worthless.
- Allowlist + audit log: only the configured providers are reachable, and every
  call is recorded (key/body excluded).

**Limits (go in clear-eyed):**
- **It stops key *theft*, not key *abuse*.** A compromised Prax can still make
  legitimate-looking calls it shouldn't (spam the model; smuggle data *inside* a
  request to an allowed provider). Mitigate at the proxy with rate limits, payload
  caps, and the audit log + trajectory monitoring (a tracked add-on); an optional
  policy/LLM inspector can gate flagged requests (deterministic policy first — a
  per-request LLM judge is foolable and adds latency to every call).
- **The proxy becomes the trusted component.** It holds the keys, so it must be
  isolated (its own process/user/container) and Prax must not be able to reach its
  config or env. The trust boundary moves to a small, auditable ~150-line service —
  a good trade vs. trusting the whole agent.
- **Non-API secrets** Prax needs *in-process* (`FLASK_SECRET_KEY`, DB creds) can't
  be proxied. They aren't external-exfil targets, and DB access is already local.

## Production notes

- Front the proxy with a real WSGI server, not the Flask dev server:
  `gunicorn -k gthread -w 4 'prax.secrets_proxy.app:build_proxy_app()'`.
- Bind it loopback (or a private network the keyless Prax can reach) — it is
  **unauthenticated** by design (whoever can reach it can spend the keys), so treat
  reachability as the control, exactly like the sandbox's loopback ports.
- Run it as its own container/user with the keys in *its* secret store only.

## Tier 2 — general egress (future)

Extend the same pattern to the ~5 other key-using sites (Twilio, search APIs,
ElevenLabs): either point each at the proxy with a service tag, or run a
**transparent forward proxy** that injects auth by destination host (needs a CA the
sandbox trusts) — which also gives the **egress allowlist** that kills the
data-exfiltration leg from `sandbox-execution-boundary.md`. Two birds. Tracked in
the [adopt-tracker](../research/adopt-tracker.md).
