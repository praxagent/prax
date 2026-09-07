# Deployment topology: credentials, execution, and access

[← Security](README.md) · Related: [Secrets proxy](secrets-proxy.md) · [Credential registry](credentials.md)

The recommended boundary separates provider credentials from the agent and runs
generated code in a dedicated execution environment. Its protections depend on
deployment isolation. Authorized API access, workspace data, and local credentials
remain exposed to agent misuse; this is not a guarantee against compromise.

## What the stock Compose files provide

The full and lite configurations start a bundled `prax` service and a separate
`sandbox`. TeamWork and memory services run inside `prax`; they are not four
independently isolated containers. The full file can add a reverse proxy through
the `secrets-proxy` profile.

The files favor local development and self-improvement:

- Prax receives `/var/run/docker.sock`, which gives it administrative access to
  the host Docker daemon.
- The sandbox receives only the selected workspace, at `/workspace`. **Closed
  2026-09:** until then it also got a read-write `/source` mount of the Prax
  checkout (files such as `.env` included) and inherited `OPENAI_KEY` and
  `ANTHROPIC_KEY` as coding-agent environment variables. Both went with the
  coding-agent CLIs, and `tests/test_compose_sandbox_service.py` pins the keyless,
  workspace-only shape. The `docker-compose.dev.yml` overlay still adds
  `/source/*` mounts to the sandbox, and any coding client you install there
  yourself brings its own credential.
- UI, API, and dashboard ports are published to host interfaces. The optional
  Tailscale service adds a private route; it does not remove those bindings.

A separate proxy container **on the same Docker daemon** does not isolate its
secrets from a process that can administer that daemon. Do not describe stock
Compose as hardened against a compromised Prax process.

## A boundary for credential isolation

```text
Prax host / execution domain       Separate proxy host / administration domain
Prax + trusted workspace    ---->  authenticated reverse proxy  ----> provider
       |                          provider credentials
       +--> sandbox
```

Prax may reach the proxy's restricted network endpoint but must not have its host
credentials, Docker socket, secret files, or administrative API. A separate VM or
host is one way to establish that boundary. A same-host deployment requires
independently restricted users, administration, mounts, and execution privileges;
a virtual environment or sibling directory alone does not suffice.

For production, restrict host-published ports, put user-facing services behind
authentication, and expose only the interfaces needed. Run one instance per
untrusted tenant with independent execution, data, and credentials. The current
shared sandbox is suitable for one owner or a trusted team, not mutually
untrusted tenants. See [Authentication](../guides/authentication.md).

## Two proxy modes — pick your coverage

| Mode | Coverage | Client setup | Access boundary |
|---|---|---|---|
| Reverse | Configured OpenAI-compatible and Anthropic upstreams | Provider base URLs plus the proxy token in the normal API-key slot | `PROXY_AUTH_TOKEN` must be nonempty; restrict network reachability and encrypt cross-host traffic. |
| Forward | Supported credential rules for destination hosts | `HTTPS_PROXY` / `HTTP_PROXY` carrying the forward token, trusted interception CA, nonempty provider placeholders | `PROXY_FORWARD_AUTH_TOKEN` must be nonempty (the addon answers `407` without it; empty = open — added in proxy commit `cf86731`); keep the listener loopback-only or behind an authenticated tunnel as defence in depth. |

The forward proxy is implemented, but a registry entry is not evidence that every
provider path works. Some credentials require OAuth exchange, login sessions, or
an unconfigured host and are skipped. Consult the [support matrix](credentials.md#support-and-verification-status).
Unmatched destinations pass through the forward service: it is not an egress
allowlist. Clients that ignore proxy variables can also bypass it.

Forward mode decrypts routed HTTPS requests. Trusting the interception CA
establishes trust in the proxy's certificates; it does not authenticate clients
to the proxy. The reverse service's `PROXY_AUTH_TOKEN` does not protect the forward
port; that listener has its own `PROXY_FORWARD_AUTH_TOKEN`. Apply externally
enforced network controls if all egress must be restricted.

### Wiring forward mode

This is an integration procedure for an operator who has already established the
boundary above. Component run instructions live in the proxy repository.

1. Generate `forward-map.json` from Prax's registry. The map contains environment
   variable names and injection rules, not provider key values:

   ```bash
   uv run python -m prax.services.credential_registry --export-forward-map /tmp/prax-forward-map.json
   ```

2. Transfer the map to the isolated proxy's configuration, set the supported real
   keys there, set `PROXY_FORWARD_AUTH_TOKEN`, and start its `forward` profile.
   The proxy compose bind-mounts `./forward-map.json`; if the file is missing,
   Docker creates a *directory* in its place and the forward proxy crash-loops
   with `IsADirectoryError: /config/forward-map.json`. The addon
   (`secrets_proxy/mitm_addon.py`) answers `407` to a caller without the token
   once it is set and warns loudly at startup when it is empty — **empty means
   open** to any caller that can reach `:8786`. Keep the forward port
   loopback-only regardless; reachability is defence in depth, not the control.
3. Obtain the **public CA certificate** from the proxy's persisted `mitm-ca`
   volume (for example `docker run --rm -v prax-secrets-proxy_mitm-ca:/ca alpine
   cat /ca/mitmproxy-ca-cert.pem`). Preserve its private key on the proxy. Add
   the certificate to a bundle containing the client's normal system roots, and
   mount that bundle read-only where Prax can read it. Do not assume a CA exists
   in the host's `~/.mitmproxy`: the checked-in container uses a Docker volume.
4. In Prax, use the actual endpoint reachable from its network namespace, with
   the forward token in the proxy URL (the username half is free-form and lands
   in the audit line, so injections are attributable):

   ```env
   HTTPS_PROXY=http://prax:<PROXY_FORWARD_AUTH_TOKEN>@127.0.0.1:8786
   HTTP_PROXY=http://prax:<PROXY_FORWARD_AUTH_TOKEN>@127.0.0.1:8786
   NO_PROXY=localhost,127.0.0.1        # keep Prax's own loopback/UI off the proxy
   SSL_CERT_FILE=/path/inside/prax/proxy-ca-bundle.pem
   REQUESTS_CA_BUNDLE=/path/inside/prax/proxy-ca-bundle.pem
   ```

   This example assumes native Prax reaches the proxy on loopback. A container
   needs its own reachable endpoint; its `127.0.0.1` is not the host. Include
   local service names in `NO_PROXY` as appropriate. Do not point the model base
   URLs at the reverse service when forwarding directly to provider hosts — the
   forward map covers the model providers too. Set **every** proxied provider key
   (`OPENAI_KEY`, `SERPER_DEV_API_KEY`, `ELEVENLABS_API_KEY`, …) to a nonempty
   placeholder so client presence checks pass: several Prax REST clients
   short-circuit on an empty key (serper returns "SERPER_DEV_API_KEY isn't
   configured") *before* the request reaches the proxy. Any non-empty string
   works; it is not an access-control credential.

   Mind the names: the reverse proxy reads `OPENAI_KEY` / `ANTHROPIC_KEY` (no
   `_API_`), while the forward map's `key_env` entries are the registry's REST
   names — `OPENROUTER_API_KEY`, `ELEVENLABS_API_KEY`, `SERPER_DEV_API_KEY`,
   `JINA_API_KEY`, … (**with** `_API_`). A misspelled variable in the proxy's
   environment does not fail loudly: the host rule still matches, the addon
   strips the client's placeholder and, finding the secret empty, injects
   nothing (`secrets_proxy/forward_inject.py`, `_apply`), which surfaces as a
   confusing `401` from the provider rather than an error from the proxy. The
   `[forward] injected <scheme> @ <host>` audit line does not tell the two
   apart — it records that a host rule matched, not that a secret was present —
   so check the variable name in the proxy's environment directly.
5. Verify one provider at a time with a small request, its proxy log entry, and
   its expected response. The test that matters is a real completion returned
   while the process holds only a placeholder — for example, with Prax's `.env`
   exported into the shell:

   ```bash
   uv run python -c "
   import json,os,urllib.request
   r=urllib.request.Request('https://openrouter.ai/api/v1/chat/completions',
     data=json.dumps({'model':'openai/gpt-4o-mini','max_tokens':12,
       'messages':[{'role':'user','content':'Reply with exactly: KEYLESS_OK'}]}).encode(),
     headers={'Authorization':f\"Bearer {os.environ['OPENROUTER_API_KEY']}\",
              'Content-Type':'application/json'})
   print(json.load(urllib.request.urlopen(r,timeout=60))['choices'][0]['message']['content'])"
   ```

   A successful response proves that request path worked; it does not prove
   filesystem isolation or coverage of every tool.

## Credentials and data that remain in Prax

Even a working proxy deployment retains session-signing secrets, inbound MCP
credentials, sandbox/UI/DB access, and any configured SSH keys. Discord's bot
credential remains local because the gateway protocol uses it in its connection
payload. Unsupported third-party authentication flows also remain local until
implemented differently. These credentials and workspace contents have value;
protect and rotate them according to their privileges.

Credential isolation reduces direct provider-key exposure. A compromised agent
can still spend through an authorized proxy, misuse the bot or workspace, or
send data to reachable destinations. Keep credentials narrowly scoped, review
provider spending, and enforce the required network and execution policy outside
agent-editable code.

## Protecting the proxy

Use a dedicated secret store and administrative identity: own container, own
non-root UID, no shared volumes with Prax, and never mount the proxy's `.env`
anywhere Prax can read it. Avoid host mounts and Docker sockets that the agent
can use to reach the proxy. Restrict callers — nothing outside the stack should
reach `:8785`/`:8786`, because whoever can reach a listener can spend the keys —
and require **both** tokens: `PROXY_AUTH_TOKEN` for the reverse proxy and
`PROXY_FORWARD_AUTH_TOKEN` for the forward proxy. Both are **open when left
empty**. Encrypt cross-host transport (TLS, or the MITM CA) so the token never
crosses a wire in plaintext. Run as a non-root user where supported, minimize
privileges (no extra tools in the image, read-only root filesystem where
possible, dropped capabilities), and keep its base image and `mitmproxy`/deps
patched — it is the one component whose compromise is game-over. Logs should
omit credentials and request bodies (`method/host/status` only). Rotate *every*
key the proxy held, plus its access credentials, after suspected compromise;
that is the blast radius, and why keeping it small and boring matters.

## Direct provider access

Direct keys in Prax remain supported. This simpler configuration exposes them to
code running with Prax's privileges. Choose the deployment based on the actual
trust model, not on the presence of a proxy container alone.

## Verification status

Documentation and configuration were cross-checked on September 4, 2026. The
[verification ledger](../VERIFICATION_LEDGER.md#secrets-proxy-prax-secrets-proxy)
records earlier live observations, including partial forward-provider coverage.
The forward proxy's caller token (`PROXY_FORWARD_AUTH_TOKEN`, proxy commit
`cf86731`) landed after that cross-check and is not in the ledger's live runs.
This documentation revision did not deploy a fresh production stack or execute
paid provider calls. Operators must verify their chosen network path, credentials,
and isolation independently.
