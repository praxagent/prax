# The secrets proxy — running a KEYLESS Prax

[← Security](README.md)

The optional [prax-secrets-proxy](https://github.com/praxagent/prax-secrets-proxy)
keeps supported provider API keys outside Prax's process. Prax sends requests to
the proxy, which substitutes the credential and streams the response. This
protects provider key material only when Prax cannot read or administer the
proxy's environment, files, or host. Prax still holds a proxy token and other
[local credentials](credentials.md), and can misuse its authorized access.

The design goal is the infra-level *"make the secret unreachable"* boundary that
the [OpenAI long-horizon-safety assessment](../research/openai-long-horizon-safety.md)
names as the real wall — an in-code guard the agent can edit is only a speed
bump (that piece's "model split a token to evade the scanner" incident, and the
[`source_grep` secret leak](sandbox-execution-boundary.md) fixed here). The same
boundary caps sandbox env-key exfiltration at the root *provided the sandbox is
not handed the token*. **Closed 2026-09:** prax's own `docker-compose.yml` /
`.lite.yml` used to pass `OPENAI_API_KEY=${OPENAI_KEY}` /
`ANTHROPIC_API_KEY=${ANTHROPIC_KEY}` into the `sandbox` service (in keyless mode,
the proxy token — spendable, not stealable); they now pass nothing, matching
prax-sandbox's own compose, and `tests/test_compose_sandbox_service.py` pins it.
See [sandbox-execution-boundary.md](sandbox-execution-boundary.md).

This page is about **Tier 1**: the model providers (OpenAI-compatible +
Anthropic), reached through a base-URL **reverse** proxy — where the high-value
keys live. The **Tier 2 forward (MITM) proxy** for all other REST egress is
shipped as an opt-in profile of the same repo; its wiring and lock-down checklist
live in [deployment-topology.md](deployment-topology.md) (summary
[below](#tier-2--general-egress)).

## Two paths — and which Praxagent endorses

| Configuration | Where provider keys live | Requirement |
|---|---|---|
| Direct provider access | Prax's environment | Code able to read that environment can obtain the keys. |
| Reverse proxy | A separate service | Keep secrets and administrative access outside Prax's reach; require a proxy token. |

Both configurations are supported. Praxagent endorses the reverse-proxy path for
its security properties; direct provider access stays the zero-friction default
so casual adoption isn't taxed, but it is **planned for deprecation in a future
release** — prefer the proxy for anything new or exposed. Use credential
isolation to reduce exposure to provider-key extraction; a second repository,
process, or virtual environment under the same OS user does not create that
isolation. Turning the proxy on needs no code change for the agent's model calls
(`OPENAI_BASE_URL` already existed; `ANTHROPIC_BASE_URL` was added so
`ChatAnthropic` can point at the proxy too).

**Known gap (2026-09) — closed 2026-09 for the harness-owned clients.** Only
`build_llm()` used to read `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL`; the direct
`OpenAI(api_key=settings.openai_key)` clients elsewhere did not, so under a
base-URL-only reverse-proxy path they sent the proxy token to `api.openai.com`
and failed (live symptom, 2026-09-07: an hourly schedule dying with
`invalid model ID` from the legacy history summariser in
`prax/conversation_memory.py`). The OpenAI SDK would pick up `OPENAI_BASE_URL`
from the process environment, but Pydantic loads `.env` without exporting it,
and the startup allow-list that does export proxy variables
(`_PROXY_ENV_ALLOWLIST` in `prax/settings.py`) covers only
`HTTP(S)_PROXY`/`NO_PROXY`/CA-bundle names — so the fix is in code, not
config. `prax/agent/llm_factory.py` now has `openai_client()`, the **only**
sanctioned constructor of a raw `openai.OpenAI` client, with exactly
`build_llm`'s OpenAI key/base-URL semantics (`OPENAI_KEY` = the proxy token
when keyless; `OPENAI_BASE_URL` → the proxy; callers cannot override
`api_key`/`base_url`). `tests/test_keyless_clients.py` AST-scans `prax/` and
fails CI on any other construction. Migrated: `prax/conversation_memory.py`
(summaries via `build_llm(tier="low")`, so tier/provider routing applies as
well), `prax/services/youtube_service.py` (Whisper via `openai_client()`),
`prax/readers/web/web2mp3.py` (summary via `build_llm`, TTS via
`openai_client()`), `prax/readers/latex/latex_functions.py` (via `build_llm`),
`prax/services/sms_service.py` (its client was never read; removed). **Still
direct** — grandfathered in that test (new sites fail, stale entries warn, so the
list only shrinks) and still failing under
the base-URL-only path until routed through `openai_client()`:
`prax/plugins/capabilities.py` (TTS, Whisper), `prax/services/library_service.py`
(covers), `prax/readers/latex/latext_gpt_tools.py`, and
`prax/plugins/tools/image/plugin.py` (plugins may not import `prax.agent`, so it
needs a capability). The embedder and the vision client keep their own
`EMBEDDING_BASE_URL` / `VISION_BASE_URL` deliberately. The forward (MITM) mode
does not depend on a base URL, so it should cover the remaining clients (the
SDK's httpx transport honours `HTTPS_PROXY`), but this has not been checked per
client — see the [ledger](../VERIFICATION_LEDGER.md) row; the 2026-07-22
forward-proxy verification recorded OpenAI returning 200 through it without
exercising these clients individually.

## Why a separate service and repo

The boundary depends on filesystem permissions, host administration, and network
policy. Stock Prax Compose mounts the host Docker socket into Prax. A proxy
container on that same daemon is not isolated from a process with administrative
access to the daemon. Run the proxy on a separate host or independently
administered daemon Prax cannot control, or redesign execution privileges before
relying on same-host isolation. See [Deployment topology](deployment-topology.md).

## How it works

```text
Prax                                 Reverse proxy                Provider
OPENAI_KEY = proxy access token  -->  validate token
OPENAI_BASE_URL = .../openai           inject provider key  ---->  model API
                                <--  stream response      <----
```

`/openai/…` and `/anthropic/…` select configured upstreams. Unknown prefixes return
`404`. When `PROXY_AUTH_TOKEN` is set, missing or incorrect client tokens return
`401` before forwarding. **The implementation allows unauthenticated access when
that setting is empty; production operators must set it.** `/healthz` is a
separate readiness endpoint and is not an authentication test.

Prax's `OPENAI_KEY` and `ANTHROPIC_KEY` must contain the configured proxy token,
not an arbitrary placeholder, for the authenticated reverse proxy.

## Run it

### Production: isolated reverse proxy

1. On a proxy host Prax cannot administer, clone the
   [proxy repository](https://github.com/praxagent/prax-secrets-proxy) and follow
   its container setup. Store provider keys only in its private configuration.
   Generate `PROXY_AUTH_TOKEN` with `scripts/gen-token.sh` and set it there. If
   you use `scripts/gen-cert.sh` for a self-signed certificate, make
   `certs/proxy.key` readable by the container's `proxyapp` user — a `0600` key
   raises `PermissionError` at startup.
2. Start its production Gunicorn container. Keep the default loopback binding
   and reach it through an authenticated encrypted tunnel, or deliberately
   publish on a private interface with TLS and firewall rules allowing only the
   Prax host. The stock loopback binding is not remotely reachable.
3. Set URLs reachable from **Prax's own network namespace**:

   ```env
   OPENAI_BASE_URL=https://proxy.example.internal:8785/openai
   ANTHROPIC_BASE_URL=https://proxy.example.internal:8785/anthropic
   OPENAI_KEY=<PROXY_AUTH_TOKEN>
   ANTHROPIC_KEY=<PROXY_AUTH_TOKEN>
   ```

   Replace the example hostname. Use a trusted certificate, or add the proxy CA
   to a bundle preserving system roots and point `SSL_CERT_FILE` to that bundle
   inside Prax's process/container. Do not disable certificate verification.
   An encrypted tunnel terminating on loopback may use HTTP on its local leg.

   If the OpenAI-compatible route forwards to **OpenAI itself**, also set
   `OPENAI_BASE_URL_IS_OPENAI=true`. Prax otherwise treats a custom base URL as
   a third-party endpoint and forces Chat Completions, which can break models
   requiring Responses. Leave this flag false for OpenRouter or other
   compatible upstreams. This applies to the local wiring below as well.
4. The sandbox needs nothing from this: stock Prax Compose passes it no provider
   keys and the image ships no coding-agent CLI (closed 2026-09 — previously the
   two key values were passed without matching provider base URLs). Any coding
   client you install in the sandbox yourself must be configured separately;
   a token without client endpoint configuration does not make it use the proxy.
5. Check readiness, then request a provider route without a token and confirm
   `401`. Repeat with an incorrect token. Finally make one small authorized model
   request and check its response and proxy log. This last step is a billed
   provider call; readiness alone does not prove it works.

### Development wiring on one host

`make secrets-proxy` runs the sibling in a virtual environment at `127.0.0.1:8785`;
the full-Compose `secrets-proxy` profile runs a container. These are wiring
examples, not proof of isolation from the same user or Docker administrator.

Native Prax uses `http://127.0.0.1:8785/openai` and `/anthropic`. Prax in the same
Compose network uses `http://secrets-proxy:8785/openai` and `/anthropic`. Set a
nonempty `PROXY_AUTH_TOKEN` and use it as the client's key in either case.
Plain HTTP belongs only on a trusted local leg or inside an encrypted tunnel.

## Security properties — and limits

- Provider-key isolation does not protect workspace data or every credential in
  Prax. Session-signing, inbound, sandbox, and other local tokens remain sensitive.
- A leaked proxy token cannot authenticate directly to the provider, but can
  authorize calls through a reachable proxy. Revoke it after suspected compromise.
- The reverse proxy restricts upstream selection. It does not prevent other
  network paths or sending sensitive data to an allowed provider. Enforce network
  policy outside the agent where needed.
- Logs omit keys and bodies. Review access, limit provider spending, patch
  dependencies, and protect the proxy's configuration and administrative access.
- Rate limits, payload limits, and trajectory monitoring are controls to configure
  or add; do not assume this integration implements them.

## The channel between Prax and the proxy

Audited 2026-10-01 on the production VM by probing the listeners from another
local account, with requests that could not reach a provider:

| Listener | Caller auth | Encryption | State |
|---|---|---|---|
| `:8785` reverse proxy (model calls) | `PROXY_AUTH_TOKEN` required — `401` without it | TLS; self-signed cert with SANs `secrets-proxy`, `localhost`, `127.0.0.1`, trusted through the CA bundle; Prax never disables verification | sound |
| `:8786` forward proxy (all other HTTPS) | **none** — an unauthenticated request was forwarded | plain-HTTP proxy protocol on loopback; TLS inside each CONNECT tunnel to the proxy's CA | **open to every local process** |
| `:8791` egress admin | admin / taint tokens | loopback | not running (policy off) |

Loopback is not a user boundary: every account on the machine, the dev tree,
and every process Prax starts can use `:8786` and have production's keys
injected. That is the hole to close. In order:

1. **Authenticate every caller of the forward proxy** with its own identity
   (prax-secrets-proxy: `PROXY_FORWARD_AUTH_TOKEN` works over HTTPS since its #5;
   per-program identities since #6) — `prax-prod`, `prax-dev`, and for children
   `prax-tools`. Then `HTTPS_PROXY=http://prax-prod:<token>@127.0.0.1:8786`.
2. **Keep that credential out of child processes** before step 1:
   `CHILD_ENV_STRIP_PROXY_CREDENTIALS` (on by default) hands every subprocess the
   URL without it, or `CHILD_PROXY_URL`'s own identity
   (`prax/services/child_env.py`). Verified with real `git` through an
   authenticating mitmproxy: unprotected, the child used Prax's credential;
   stripped, it got `407`; with its own identity, it worked as `prax-tools`.
3. **Then give identities different rules**: the egress policy and forward-map
   rules can name callers (`callers`), so `prax-tools` can be limited to, say,
   GitHub, and one-instance credentials (the Discord bot token) to `prax-prod`.

What is acceptable as it is: the proxy credential crosses loopback in clear text
(in `Proxy-Authorization`), readable only by root, who can read the keys anyway;
request contents are TLS inside the tunnel.

## Production notes

Use a production WSGI server, a nonempty reverse-proxy token, encrypted cross-host
transport, and restricted reachability. `secrets_proxy/config.py` treats an empty
`PROXY_AUTH_TOKEN` as **open** — any caller that can reach the port spends the
keys — so set it, and bind loopback or a private network anyway so that
reachability is a *second* control rather than the only one. Keep the proxy
outside Prax's administrative and filesystem access, as its own container/user
with the keys in *its* secret store only. The [proxy README](https://github.com/praxagent/prax-secrets-proxy)
owns component setup; this page describes Prax's integration and limits.

## Discord through the forward proxy

The bot token used to stay in Prax: Discord's REST API wants
`Authorization: Bot <token>`, and the gateway carries the token *inside* its
IDENTIFY and RESUME messages, not in a header. The forward proxy now handles
both (prax-secrets-proxy: header injection with a `prefix`, and a `ws-json:d.token`
rule that rewrites client→server WebSocket messages). discord.py sends those
messages as plain JSON text — the gateway connects with `compress=0`, and
zlib-stream compresses only the server's messages.

**One instance only.** A token injected for every caller would turn any Prax
holding the placeholder — a dev instance — into the bot as well, and both would
answer every message. So the registry marks the credential `exclusive`: the
map is exported with it only when its callers are named, and the proxy refuses
to load an exclusive rule without them. Callers are per-program identities
(prax-secrets-proxy `PROXY_FORWARD_CALLERS`), so production needs its own proxy
token.

Rollout (production `.env` changes — the operator's to make):

1. Proxy: put the real `DISCORD_BOT_TOKEN` in the proxy's `.env`; give production
   its own caller (`python -m secrets_proxy.callers new prax-prod`).
2. Map: `python -m prax.services.credential_registry --export-forward-map
   ../prax-secrets-proxy/forward-map.json --exclusive-callers DISCORD_BOT_TOKEN=prax-prod`,
   then recreate the proxy.
3. Prax: `HTTPS_PROXY=http://prax-prod:<token>@127.0.0.1:8786`,
   `DISCORD_USE_PROXY=true`, and replace `DISCORD_BOT_TOKEN` with a placeholder.
4. Rotate the bot token in the Discord developer portal once it lives only in
   the proxy — the old one has been in Prax's `.env`.

Verified 2026-10-01 with real discord.py 2.7.1 through the real mitmproxy
against a fake Discord: the production caller logged in (REST got `Bot` + the
token) and its IDENTIFY reached the gateway carrying the token although the
client sent a placeholder; a second caller got `401` and `LoginFailure`, never
reaching the gateway. Not yet run against Discord itself.

## Tier 2 — general egress

The optional forward proxy is **shipped** as the companion repository's opt-in
`forward` profile: a mitmproxy-based transparent forward proxy (`HTTPS_PROXY` →
`:8786`, a CA Prax trusts) that terminates TLS and injects credentials for
configured hosts from the registry-generated forward map. Coverage is partial;
see the dated [support matrix](credentials.md#support-and-verification-status)
and [verification ledger](../VERIFICATION_LEDGER.md). Wiring and the lock-down
checklist are in [deployment-topology.md](deployment-topology.md).

The forward listener has its own caller credential, `PROXY_FORWARD_AUTH_TOKEN`
(`secrets_proxy/mitm_addon.py` answers `407` without it once it is set); the
reverse-proxy `PROXY_AUTH_TOKEN` has no effect on forward-mode access, and an
empty forward token leaves the listener open to any caller that can reach it.
Restrict it to loopback on a trusted host or an authenticated tunnel regardless.
Two honest limits remain: the addon **injects** for allow-listed hosts and passes
every other host through untouched — its map controls credential injection, not
allowed egress, so it is not yet the **egress allowlist** that would kill the
data-exfiltration leg from [sandbox-execution-boundary.md](sandbox-execution-boundary.md);
and the sandbox container does not route through it. Both are tracked in the
[adopt-tracker](../research/adopt-tracker.md). Trusting its CA permits
interception of routed HTTPS traffic and does not authenticate the caller to the
proxy.

## Wire record — checking Prax's account against the model path

Idea credit: NVIDIA's Open Agent Safety Platform. With the proxy's
`PROXY_WIRE_RECORD` on, the forward proxy writes one hash-chained line per
model response: the tool calls the model asked for, as names and argument
hashes, never text, written outside Prax's process
([proxy README](https://github.com/praxagent/prax-secrets-proxy#wire-record--opt-in-what-the-agent-actually-asked-the-model)).

`scripts/check_wire_record.py WIRE.jsonl --caller <name>` verifies the chain,
then lists every tool call on the wire that Prax's own traces don't show
within a few minutes — activity Prax didn't account for. It is read-only.

- Give each Prax instance its own proxy username (`http://<name>:<token>@…`)
  so `--caller` can separate dev from prod.
- **Arguments too.** Each tool span records two hashes, never the arguments:
  `requested_args_sha256` (what the model asked for, from its response, matched
  by `tool_call_id`) and `args_sha256` (what the tool actually ran with, taken
  from the innermost tool start). Both use the wire record's canonical form.
  So beyond a hidden or dropped call, the check reports **ARGS DIFFER** (the
  trace misreports what the model asked for) and **CHANGED BEFORE RUNNING**
  (the tool ran with arguments other than the model's). Traces written before
  these fields existed are matched on name and time only.
- The chain is tamper-evident, not tamper-proof: keep the record writable only
  by the proxy and copy its head hash off the machine.

