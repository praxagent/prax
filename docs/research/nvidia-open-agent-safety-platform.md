# NVIDIA Open Agent Safety Platform and OpenShell

**Verdict: document + adopt two ideas; the hardware is out of reach, and
OpenShell is a peer to watch, not a replacement.** The platform's principles
are the ones Prax adopted this month from Meta's Muse — enforcement out of the
agent's reach, credentials the agent never sees, and control on the path to
the model — so it is strong external confirmation. Two things are new:

1. **Check the agent's account against the wire.** Keep an out-of-band record
   of what the agent actually asked the model and which tools the model told it
   to call, where the agent can't edit it, and compare it with the agent's own
   trace. A mismatch means the agent is hiding activity.
2. **Show what a policy change newly grants before approving it.** Diff an
   egress-policy change into "these hosts, these methods, these credentials are
   now reachable" instead of reviewing the policy text.

Source: [NVIDIA Open Agent Safety Platform](https://developer.nvidia.com/blog/nvidia-open-agent-safety-platform-a-reference-for-continuous-in-silicon-agent-monitoring)
(NVIDIA developer blog) and the [OpenShell repository](https://github.com/NVIDIA/openshell).
The post gives no latency, throughput or evaluation numbers.

## What it is

Two things under one name.

**The platform** is a hardware reference design in three layers — application
(models, tools, data), runtime (OpenShell), infrastructure — with its monitoring
done "in silicon": **NVIDIA Sentry** runs on **BlueField-4 DPUs**, which "sit on
the node's only path to the model", and OpenShell runs on **Vera CPUs**. Using
DOCA, it "correlates agent interactions, policy decisions, and tool and data
access to create a contextual record" to detect drift from the agent's designed
intent. Five principles: verifiable policy before execution, out-of-band
enforcement, control via the model pathway, authority scaled to reasoning
visibility, and shared responsibility. Its premise: "an agent in these
circumstances cannot be expected to fully govern its own behavior."

**OpenShell** is the open part: an agent runtime, Apache-2.0, version 0.1.x,
about 12k stars. Agents run in a sandbox where "kernel controls confine which
files it can access and which system calls it can make", with policy over file
access, system calls and network connections. "Agents never see real
credentials; OpenShell adds them only to requests bound for approved
endpoints." Policy changes go through **formal verification** that "flags risky
new access it would grant, such as reaching a new host with credentials". It
runs on Linux, Apple-silicon macOS or WSL 2 with Docker, Podman or host
virtualization — no NVIDIA hardware required. The repository doesn't say which
kernel mechanisms it uses (namespaces, seccomp, Landlock or microVMs).

**From the [OpenShell product page](https://www.nvidia.com/en-us/ai/openshell/)**
(four components):

- **Agent sandboxes** with "no direct network access"; the kernel "monitors
  and filters the agent's system calls … blocking unsafe calls".
- **A supervisor** outside the sandbox that "evaluates every network request
  against policy at the **binary**, destination, method, and path levels" —
  per-*program* network policy.
- **A policy prover** that checks "whether policies stay within an allowed
  access boundary and whether proposed network rules add risky access".
- **A gateway** that authenticates users and delivers policies and credentials
  to each sandbox, plus "a policy-aware inference router which forwards
  permitted requests to cloud model endpoints".
- "Security lives in the environment, not the model or the application.
  Nothing is permitted by default." It supports Claude Code, Codex, Copilot
  CLI, OpenClaw, OpenCode and LangChain Deep Agents, on Docker, Podman or
  Kubernetes (Helm). Still no policy-language examples and no numbers.

## Against Prax

| Principle | Prax |
|---|---|
| Credentials never in the agent; injected only toward approved endpoints | **Have it.** prax-secrets-proxy: keyless Prax, the proxy injects the real key; the forward proxy's egress policy decides every request ([secrets-proxy.md](../security/secrets-proxy.md)) |
| Out-of-band enforcement, beyond the agent's reach | **Have it (opt-in).** The egress gates and proxy run outside Prax; systemd limits Prax to loopback, so the proxy is its only way out; the Docker socket is taken away ([out-of-band-approvals.md](../security/out-of-band-approvals.md), [meta-muse-secure-vm](meta-muse-secure-vm.md)) |
| Control via the model pathway | **Half.** All model traffic goes through the secrets proxy, which *controls* it (key, allowlist, token) but only records metadata — method, provider, path, status, size |
| Verifiable policy before execution | **Partly.** Egress requests and HIGH-risk actions go to a person in TeamWork with the exact destination; a *policy file change* has no review of what it newly grants |
| Detect drift from designed intent | **Not built.** Traces exist and are searchable; nothing compares behaviour against an expected profile |
| Hardware enforcement (DPUs, Vera) | **Out of reach.** A single VM; no DPU. The same hardware wall as the GPU-bound research |
| Per-program network policy (the supervisor decides by *binary* as well as destination) | **Declined earlier as too costly** (per-process taint needs kernel hooks, [meta-muse-secure-vm](meta-muse-secure-vm.md)). Our gates decide per destination, method and path, not per program. OpenShell ships it — the main reason to evaluate it |
| A policy prover with an outer access boundary | **Not built.** Nothing stops an edited egress policy from granting more than an operator-set ceiling |

## Adopt

**1. A wire record Prax can't edit, checked against Prax's own trace.**
Prax's audit log and traces live in the process they audit — the
"governance covers the hub, not the spokes" and in-process-governance root
causes from the September review. A compromised Prax can drop its own trace
entries. The proxy already sees every model call and is out of Prax's reach.
Extend its audit line, opt-in, with the **tool calls the model returned** (tool
names, a hash of the arguments, sizes) and a hash of each request, append-only
and hash-chained, in a place Prax can't write. A periodic check then compares
it with Prax's traces: a tool call on the wire that the trace doesn't show is
the signal. Privacy: names, hashes and sizes only — never prompt or response
text.

**2. A diff of newly granted access — and a ceiling — for every policy change.** When the egress
policy file changes, or a person grants "allow for 1 hour", show the reachable
set before and after: new hosts, new methods, new paths, and which requests
would now carry a credential. Review the effect, not the text. OpenShell calls
its version formal verification; ours can start as a plain set difference over
the policy's rules.
Add OpenShell's second check too: an operator-set **ceiling** (the "allowed
access boundary") that no policy edit or timed grant may exceed — refused
outright, not merely shown.

**3. A time-boxed evaluation of OpenShell as prax-sandbox's runtime** (dev VM
only). It ships the two things we declined or lack — per-program network
policy and a policy prover — and supports LangChain-based agents. Run the
sandbox's workloads (shell, Python, browser, Lean) inside it and measure what
breaks. Adopt only if it's a clear gain over the egress gate we already run;
it is 0.1.x and doesn't document its kernel mechanisms.

## Don't adopt, and nuance

- **The hardware platform.** BlueField-4 and Vera are data-centre parts; Prax
  runs in one VM. Out-of-band on a DPU is stronger than out-of-band in a
  separate process, but a separate process under a separate account, with the
  kernel enforcing the route, is the version available to us and is built.
- **Replacing prax-sandbox with OpenShell unevaluated.** Same shape —
  confined agent, credential injection, network policy — plus per-program
  policy we don't have. But it's 0.1.x, it doesn't document its kernel
  mechanisms, and prax-sandbox already has the egress gate, exec deadlines and
  container limits. Evaluate first (adopt item 3), decide on the measurement.
- **"Formal verification" is a strong phrase.** The post describes flagging new
  access a policy would grant; it doesn't say what is formally proven. Our
  version would be an honest set difference, and we'd call it that.
- **No numbers.** No latency, throughput or evaluation results in the post, so
  none of its claims about line-speed enforcement are checkable.

## Adopt tracker

| Item | Status |
|---|---|
| Out-of-band wire record in the secrets proxy (tool calls the model returned, hashed, append-only, hash-chained) + a check against Prax's own traces | **queued** |
| Diff of newly granted access for egress-policy changes and timed grants | **queued** |
| Policy ceiling: no policy edit or grant may exceed an operator-set boundary | **queued** (with the grant diff) |
| Per-program network rules (decide by the requesting program as well as destination) | **queued** — not ruled out; build our own or take OpenShell's after the evaluation |
| OpenShell as prax-sandbox's runtime — per-program network policy + policy prover | **time-boxed evaluation** in the dev VM; adopt only on a clear gain |
| DPU / in-silicon monitoring | **declined** — hardware wall |
