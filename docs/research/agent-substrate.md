# Agent Substrate (Google, CNCF sandbox application)

**Verdict: document + adopt two ideas.** The platform itself is not for Prax: it
multiplexes thousands of mostly-idle agents onto a few Kubernetes pods, and Prax
is one agent on one VM. But its egress design is the closest published match
to our secrets proxy, and it holds two rules we did not:

1. **Never put a credential on a cleartext wire.** Substrate injects only into
   HTTPS its gateway intercepts. Ours injected by host whatever the scheme, so
   `http://` to a credential host sent the real key unencrypted. **Adopted and
   fixed** (prax-secrets-proxy #7, reproduced and verified live).
2. **An identity the program can't read.** Substrate redirects all of the
   sandbox's traffic (nftables) to a **trusted tunnel client** that holds the
   actor's certificate. The untrusted process has no token to read, forge or
   leak. This is the stronger form of the per-program rules just built
   (prax-secrets-proxy #6), whose identities are tokens a program can read.
   **Queued** as the route for the sandbox side.

Source: [cncf/sandbox#523](https://github.com/cncf/sandbox/issues/523), the
application for Agent Substrate to join the CNCF sandbox (the TOC vote passed),
and the repository [agent-substrate/substrate](https://github.com/agent-substrate/substrate)
(Apache-2.0, Go, public since May 2026, about 4k stars). Its maintainers are
Google engineers (including Kubernetes co-founder Tim Hockin), with
contributors from NVIDIA, Solo.io and Microsoft. I read its docs on egress,
credential injection, request parking and its threat model; I did not run it.

## What it is

A runtime for large-scale agent deployments on Kubernetes. Agents ("actors")
are mostly idle, so Substrate maps many actors onto a few warm "worker" pods
and suspends and resumes them with memory snapshots (gVisor checkpoints or
Cloud Hypervisor microVMs), targeting sub-second resume and eventually p95
under 100 ms. An Envoy-based router wakes a suspended actor when traffic
arrives for it. Declarative CRDs (`WorkerPool`, `ActorTemplate`,
`SandboxConfig`), a gRPC control plane backed by PostgreSQL, and SPIFFE-style
mTLS between components. It names OpenShell, E2B, Daytona and Modal as
neighbours, and lists LangChain, ADK, Claude Code, Codex and MCP servers as
workloads.

Maturity, in its own words: the threat model says it "has little to no security
hardening at this time". Credential injection ships behind an
`--experimental-egress-credential-injection` install flag.

## Its egress, against ours

| Substrate | Prax (secrets proxy + systemd drop-ins) |
|---|---|
| All actor TCP is redirected by nftables to `atunnel`, a trusted client in the worker pod, which opens an mTLS CONNECT tunnel to the policy enforcement point | Prax's own process is limited to loopback by the kernel (`IPAddressDeny=any`), so the forward proxy is its only way out. The sandbox's egress goes through its own gate, not this proxy |
| The tunnel client presents a **short-lived per-actor certificate**; the actor never holds it | One proxy token per program (per-program rules, prax-secrets-proxy #6). A program can read its own token, and programs sharing an environment can read each other's |
| "Everything originating from the actor is untrusted": an actor-supplied hostname or SNI is never proof of the destination | The same rule: requests are judged on the dialled address, a mismatched `Host` is refused, and the connection is pinned to the checked address |
| Injection only on intercepted HTTPS; the actor sends a placeholder header, and the gateway replaces it | Injection by destination host, stripping any client-supplied value first. **Was also applied to cleartext http — now fixed (#7)** |
| Per-actor authorisation of *which credential* an actor may use (atespace → namespace policy) | Any authenticated caller got every key. Per-program rules (#6) can now limit a credential's host to named programs |
| Fail-closed table: missing secret → 403, provider down → 503, bad config → 500 | A missing key passes the request through without it, so the provider refuses it (no leak, but the failure shows up upstream as a 401) |
| **DNS (port 53) bypasses the enforcement point** | **The same gap**, already documented in `40-egress-only-through-the-proxy.conf`: Prax reaches systemd-resolved on loopback, which forwards lookups upstream, so a name Prax resolves can carry data out |

## Adopt

**1. No credential on a cleartext wire — done.** `http://` to an injection host
now goes out without the key, with a warning naming the host (never the key).
Reproduced before the fix with a fake key in a throwaway mitmproxy container,
and verified after: HTTPS still injected, HTTP not. prax-secrets-proxy #7.

**2. Identity held by a trusted tunnel client, not the program — queued.**
For the sandbox container: redirect its outbound TCP (nftables `REDIRECT`) to a
small sidecar that adds the sandbox's proxy credential and forwards to the
secrets proxy. The processes inside never see a token, so they cannot leak one
or claim another program's rules. It needs `NET_ADMIN` for the sidecar only,
and a place outside the sandbox's filesystem for the token. The same route
closes the sandbox's own egress gap. It does not give per-*binary* identity
inside the sandbox; OpenShell's supervisor remains the reference for that.

## Bank

- **Fail-closed injection as a written contract.** Their table of injection
  failures and outcomes is worth copying into our docs. Behaviour change is
  optional: a missing key already cannot leak.
- **Parking with a budget, and never cancelling a committed operation.** Their
  request parking stops *starting* retries at the budget but lets an in-flight
  restore finish rather than discard it. The same shape as our parked approvals
  and turn budgets: bound the waiting, not work already committed.
- **Threat model as review skills.** They plan to turn their threat model into
  AI review skills run continuously on the repository. Our
  `suite_review_2026-09-05.md` could be used the same way by `/code-review`.

## Don't adopt

- **The platform.** Kubernetes, snapshot-based multiplexing and a PostgreSQL
  control plane solve density at 1M+ actors. Prax runs one agent on an 8 GB VM;
  this is the scale non-goal recorded for [capy](capy-swe-agent-platform.md) and matrix.build.
- **DNS bypass.** Theirs is a deliberate exception; ours is a known gap. Neither
  is a pattern. Closing ours means giving Prax a resolver that answers only
  names the proxy has allowed, which is not planned yet.
