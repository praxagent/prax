# Endorsed deployment topology — isolate, sandbox, guardrail

[← Security](README.md) · Related: [Secrets proxy](secrets-proxy.md) · [Credential registry](credentials.md)

Prax is meant to be the **mature-adult** version of an open agent harness: powerful,
but built so that when — not if — the agent is prompt-injected or runs hostile code,
it **cannot walk away with your secrets or your box.** The endorsed production shape
makes that structural, not hopeful.

## The shape: four isolated containers on one shared network

```
              ┌──────────────────────── shared docker network ────────────────────────┐
              │                                                                         │
   ┌──────────┴──────────┐   ┌───────────────────┐   ┌────────────────┐   ┌────────────┴────────────┐
   │  PRAX (the agent)   │   │  SECRETS-PROXY     │   │  SANDBOX       │   │  TEAMWORK (optional)    │
   │  • no real API keys │──▶│  • holds ALL keys  │   │  • runs code   │   │  • web UI               │
   │  • HTTPS_PROXY ─────────▶│  • injects + fwd   │──▶ (egress)        │   │  • no AI keys           │
   │  • trusts proxy CA  │   │  • LOCKED DOWN 🔒   │   │  • own overlay │   │                         │
   └─────────────────────┘   └───────────────────┘   └────────────────┘   └─────────────────────────┘
        can reach the proxy       can reach the             the ONLY           can reach Prax's API
        over the network only     internet                 code-exec box
        — NOT its filesystem                                (container = the
          or its env                                         boundary)
```

Each box is a **separate container** (ideally a separate OS user/UID too). They
**share a network so they can talk**, but one container **cannot read another's
filesystem, env, or memory**. That's the whole game:

- **Prax** runs *keyless*. It holds the proxy's access token (or trusts its CA) and
  nothing else of value. Compromise it and there's no provider key to steal.
- **The secrets-proxy** holds every real key and is the single, small, auditable
  component that injects them. It is the **deterministic** trust anchor — you can
  read all of its code, versus an LLM whose behaviour you can't bound.
- **The sandbox** is the only place agent-written code runs; the container *is* the
  boundary (see [sandbox-execution-boundary](sandbox-execution-boundary.md)).
- **TeamWork** (optional) is a pure UI with no AI keys.

### Why this is the right trade (the deterministic-risk argument)

Keeping keys in the agent is a **high-probability, unbounded** risk: prompt
injection is routine for an agent that reads the web and runs code, and you can't
cap what a manipulated LLM does with a key it holds. Moving the keys into the proxy
converts that into a **low-probability, bounded** risk: a ~few-hundred-line service
that does exactly what its code says, every time — auditable, rate-limitable,
lockable. You can engineer *its* compromise probability down; you cannot do that to
an LLM. Concentrating the secret in the smallest, most predictable component is the
same principle behind HSMs and secret managers.

## Two proxy modes — pick your coverage

Both run the proxy as its own container; they differ in how much egress they cover.
The map of exactly which credential is handled how is the
[credential registry](credentials.md) — the single source of truth.

| Mode | Covers | Mechanism | Cost |
|---|---|---|---|
| **Reverse** (default, shipped) | the 3 **model** keys | Prax points `OPENAI_BASE_URL`/`ANTHROPIC_BASE_URL` at the proxy; it swaps the token for the real key | simplest; no TLS interception |
| **Forward** (opt-in) | **all 15 injectable keys** (model **and** REST) | transparent MITM: Prax sets `HTTPS_PROXY` at the proxy, which terminates TLS and injects auth by destination host from the registry-generated forward-map | one box for *all* egress — no base-URL needed — but **decrypts all egress** |

Forward mode is a superset: the forward-map includes the model providers too, so
you do **not** also set `OPENAI_BASE_URL` — every outbound call, model or REST, is
intercepted and injected by host. Reverse mode is just the lighter, no-MITM option
when you only want the model keys off the box.

Even with forward mode, **9 credentials structurally cannot move** (in-process
signing, the inbound MCP token, Prax's own DB/sandbox/UI, git-over-SSH keys, and
Discord's websocket gateway) — see the registry. "Zero secrets in Prax" is the
direction; this is the honest map of how far it reaches.

### Wiring forward mode
```bash
# 1. Generate the forward-map from Prax's registry (never-drift link):
python -m prax.services.credential_registry --export-forward-map ../prax-secrets-proxy/forward-map.json
# 2. Run the forward proxy (its own container, holds the real keys):
cd ../prax-secrets-proxy && docker compose --profile forward up   # mitmproxy on :8786
# 3. Trust its CA in Prax's bundle (system CAs + the mitmproxy CA):
cat "$(uv run python -m certifi)" ~/.mitmproxy/mitmproxy-ca-cert.pem > ~/PRAX/prax-proxy-ca-bundle.pem
```
Then in Prax's env (note: **no** `OPENAI_BASE_URL` — forward mode catches it too):
```bash
HTTPS_PROXY=http://secrets-proxy:8786
HTTP_PROXY=http://secrets-proxy:8786
NO_PROXY=localhost,127.0.0.1        # don't route Prax's own loopback/UI through it
SSL_CERT_FILE=/abs/path/prax-proxy-ca-bundle.pem
REQUESTS_CA_BUNDLE=/abs/path/prax-proxy-ca-bundle.pem
# Set EVERY proxied key (OPENAI_KEY, SERPER_DEV_API_KEY, ELEVENLABS_API_KEY, …) to a
# NON-EMPTY placeholder here — the real keys live only in the proxy's .env, and the
# proxy strips the placeholder + injects the real key by host. The placeholder must
# be non-empty: several Prax REST clients presence-guard on the key (e.g. serper
# returns "SERPER_DEV_API_KEY isn't configured" if it's blank) and short-circuit
# BEFORE the request reaches the proxy. Any non-empty string works (e.g. "proxied").
```

**Verify one provider round-trips** before trusting it: with the proxy up, make a
real call (e.g. a web search) and confirm the answer comes back — a `401` means the
key isn't reaching that host (check the forward-map rule + the real key in the
proxy's `.env`); a TLS error means Prax isn't trusting the mitmproxy CA (rebuild the
bundle). The proxy logs one `injected <scheme> @ <host>` line per hit — never the key.

## 🔒 LOCK DOWN THE PROXY CONTAINER — HARD

**The proxy is now the crown jewels.** It holds every key and (in forward mode) sees
every request body. Its compromise is total. Treat it like an HSM, not an app:

- **Isolate it.** Own container, own non-root UID, own secret store. Prax must be
  able to reach its *port* and nothing else — never its filesystem, env, or the
  `.env` that holds the keys. (Docker: separate service; no shared volumes with
  Prax; don't mount the proxy's `.env` anywhere Prax can read.)
- **Minimise reachability.** Bind loopback or the private container network only.
  Nothing outside the stack should be able to reach `:8785`/`:8786`. Reachability
  *is* the control — whoever can reach it can spend the keys.
- **Require the token** (`PROXY_AUTH_TOKEN`) so only Prax, not any other process on
  the network, can use it. Use **TLS** (or the MITM CA) so the token/traffic never
  cross a wire in plaintext.
- **Least privilege.** No extra tools in the image, read-only root filesystem where
  possible, drop Linux capabilities, no Docker socket, no host mounts.
- **Never log secrets.** The proxy logs `method/host/status` only — never a key or
  body. Keep it that way.
- **Rotate on any doubt.** If the proxy box is ever suspected, rotate *every* key it
  held — that's the blast radius, and it's why keeping it small and boring matters.
- **Patch it.** It's the one component whose compromise is game-over; keep its base
  image and `mitmproxy`/deps current.

If you can't lock the proxy down this hard, you have **not** improved your security
by adding it — you've just moved all the keys into one box. The isolation is the
whole point.

## The opt-out — Prax can still hold its own keys (at your risk)

None of this is forced. If you'd rather run the simple way, **put the keys in Prax's
own `.env` and don't point any base URL / `HTTPS_PROXY` at a proxy.** Prax works
exactly as before. This is fine for a trusted, solo, non-exposed setup — you're
choosing to accept that a prompt-injected or compromised Prax could read those keys.
Praxagent **endorses the proxy topology** for anything exposed or handling data you
care about, and the keys-in-Prax path is planned for eventual de-emphasis — but it
stays supported. Your box, your call.

## Verification status

The reverse proxy is live-verified end-to-end (keyless model calls over TLS with the
token gate). The forward (MITM) injector is **unit-tested** (the generic
bearer/header/basic/query injection, per host, from the registry map); a full
end-to-end MITM run against each real third-party provider is the operator's to
verify with their own keys. See [`VERIFICATION_LEDGER.md`](../VERIFICATION_LEDGER.md).
