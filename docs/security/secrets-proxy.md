# The secrets proxy — running a KEYLESS Prax

[← Security](README.md)

The optional [prax-secrets-proxy](https://github.com/praxagent/prax-secrets-proxy)
keeps supported provider API keys outside Prax's process. Prax sends requests to
the proxy, which substitutes the credential and streams the response. This
protects provider key material only when Prax cannot read or administer the
proxy's environment, files, or host. Prax still holds a proxy token and other
[local credentials](credentials.md), and can misuse its authorized access.

## Two paths — and which Praxagent endorses

| Configuration | Where provider keys live | Requirement |
|---|---|---|
| Direct provider access | Prax's environment | Code able to read that environment can obtain the keys. |
| Reverse proxy | A separate service | Keep secrets and administrative access outside Prax's reach; require a proxy token. |

Both configurations are supported. Use credential isolation to reduce exposure
to provider-key extraction. A second repository, process, or virtual environment
under the same OS user does not create that isolation.

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
   Generate `PROXY_AUTH_TOKEN` with `scripts/gen-token.sh` and set it there.
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
4. Configure sandbox coding agents separately. Stock Prax Compose passes the two
   key values to the sandbox but does not pass matching provider base URLs.
   Tokens without client endpoint configuration do not make those clients use
   the proxy.
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

## Production notes

Use a production WSGI server, a nonempty reverse-proxy token, encrypted cross-host
transport, and restricted reachability. Keep the proxy outside Prax's
administrative and filesystem access. The [proxy README](https://github.com/praxagent/prax-secrets-proxy)
owns component setup; this page describes Prax's integration and limits.

## Tier 2 — general egress

The optional forward proxy is implemented in the companion repository. It uses
mitmproxy to terminate TLS and inject credentials for configured hosts. Coverage
is partial; see the dated [support matrix](credentials.md#support-and-verification-status)
and [verification ledger](../VERIFICATION_LEDGER.md).

The checked-in forward configuration does **not** apply `PROXY_AUTH_TOKEN` and
passes unmatched hosts. Its map controls credential injection, not allowed egress.
Restrict it to loopback on a trusted host or an endpoint behind independently
enforced caller authentication. For remote use, configure an authenticated tunnel
or gateway; the reverse-proxy token has no effect on forward-mode access. Trusting
its CA permits interception of routed HTTPS traffic and does not authenticate the
caller to the proxy.
