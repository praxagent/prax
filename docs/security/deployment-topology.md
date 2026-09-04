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
- The sandbox receives the selected workspace and a read-write `/source` mount
  of the Prax checkout, including files in that checkout such as `.env`.
- The sandbox inherits `OPENAI_KEY` and `ANTHROPIC_KEY` as coding-agent environment
  variables. Configuring Prax's model endpoint does not automatically configure
  the sandbox's independent coding clients.
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
| Forward | Supported credential rules for destination hosts | `HTTPS_PROXY` / `HTTP_PROXY`, trusted interception CA, nonempty provider placeholders | The checked-in forward service has no caller-authentication gate. Use a trusted loopback endpoint or an independently authenticated tunnel/gateway. |

The forward proxy is implemented, but a registry entry is not evidence that every
provider path works. Some credentials require OAuth exchange, login sessions, or
an unconfigured host and are skipped. Consult the [support matrix](credentials.md#support-and-verification-status).
Unmatched destinations pass through the forward service: it is not an egress
allowlist. Clients that ignore proxy variables can also bypass it.

Forward mode decrypts routed HTTPS requests. Trusting the interception CA
establishes trust in the proxy's certificates; it does not authenticate clients
to the proxy. The reverse service's `PROXY_AUTH_TOKEN` does not protect the forward
port. Apply externally enforced network controls if all egress must be restricted.

### Wiring forward mode

This is an integration procedure for an operator who has already established the
boundary above. Component run instructions live in the proxy repository.

1. Generate `forward-map.json` from Prax's registry. The map contains environment
   variable names and injection rules, not provider key values:

   ```bash
   uv run python -m prax.services.credential_registry --export-forward-map /tmp/prax-forward-map.json
   ```

2. Transfer the map to the isolated proxy's configuration, set the supported real
   keys there, and start its `forward` profile. Keep the forward port loopback-only
   unless access is independently authenticated and restricted.
3. Obtain the **public CA certificate** from the proxy's persisted `mitm-ca`
   volume. Preserve its private key on the proxy. Add the certificate to a bundle
   containing the client's normal system roots, and mount that bundle read-only
   where Prax can read it. Do not assume a CA exists in the host's `~/.mitmproxy`:
   the checked-in container uses a Docker volume.
4. In Prax, use the actual endpoint reachable from its network namespace:

   ```env
   HTTPS_PROXY=http://127.0.0.1:8786
   HTTP_PROXY=http://127.0.0.1:8786
   NO_PROXY=localhost,127.0.0.1
   SSL_CERT_FILE=/path/inside/prax/proxy-ca-bundle.pem
   REQUESTS_CA_BUNDLE=/path/inside/prax/proxy-ca-bundle.pem
   ```

   This example assumes native Prax reaches an authenticated tunnel on loopback.
   A container needs its own reachable endpoint; its `127.0.0.1` is not the host.
   Include local service names in `NO_PROXY` as appropriate. Do not point the model
   base URLs at the reverse service when forwarding directly to provider hosts.
   Set each proxied provider key to a nonempty placeholder so client presence
   checks pass. That placeholder is not an access-control credential.
5. Verify one provider at a time with a small request, its proxy log entry, and
   its expected response. A successful response proves that request path worked;
   it does not prove filesystem isolation or coverage of every tool.

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

Use a dedicated secret store and administrative identity. Avoid host mounts and
Docker sockets that the agent can use to reach the proxy. Restrict callers,
require the reverse token, and encrypt cross-host transport. Run as a non-root
user where supported, minimize privileges, and keep dependencies patched. Logs
should omit credentials and request bodies. Rotate affected keys and proxy
access credentials after suspected compromise.

## Direct provider access

Direct keys in Prax remain supported. This simpler configuration exposes them to
code running with Prax's privileges. Choose the deployment based on the actual
trust model, not on the presence of a proxy container alone.

## Verification status

Documentation and configuration were cross-checked on September 4, 2026. The
[verification ledger](../VERIFICATION_LEDGER.md#secrets-proxy-prax-secrets-proxy)
records earlier live observations, including partial forward-provider coverage.
This documentation revision did not deploy a fresh production stack or execute
paid provider calls. Operators must verify their chosen network path, credentials,
and isolation independently.
