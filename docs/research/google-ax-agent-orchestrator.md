# google/ax — Kubernetes-native agent orchestrator — assessment

**Source:** [github.com/google/ax](https://github.com/google/ax) — Google's
declarative orchestrator for running autonomous agent workloads on Kubernetes,
built on something the README calls "Agent Substrate". Go, Apache-2.0, ~3.6k
stars when read. Explicitly **pre-stable**: the README says the project is
"actively refining core concepts, protocols, and specifications" with major
breaking changes expected before v1.0. Assessed 2026-09-20.

**⚠️ Source limits.** Read from the repository landing page only. The Go source
was not read, nothing was installed or run, and no cluster was stood up. "Agent
Substrate" is referenced there but not explained, so it is not described here.
No benchmarks or performance claims are published, so none are quoted.

**Verdict: document — don't adopt the platform, and don't file it as a rival.
It governs the *container*; Prax governs the *tool call*. Those are different
layers, and ax could in principle run Prax as one of its Tasks.** Two things are
worth taking as design pressure, one of which is a gap Prax has had open since
the 2026-07-08 outage.

---

## What it actually is

Four declarative primitives, `kubectl`-shaped, applied as `ax.io/v1alpha1`
manifests:

| Primitive | What it declares |
|---|---|
| `Task` | the agent workload, sandboxed, with CPU/memory limits |
| `Workspace` | a warm start — git repos, MCP servers and tools pre-wired |
| `Gateway` | network egress, allowlisted explicitly |
| `Model` | LLM provider config and credentials, centrally held |

Plus `ax suspend` / `ax resume` checkpointing, `ax ssh` for interactive
debugging, and `ax watch` for live monitoring.

The framing in its README is the honest part and the reason it is worth reading:
agents are neither stateless microservices nor batch jobs. They accumulate
state, need isolation, call external APIs, and burn money unattended. That is a
correct problem statement and it is the same one Prax's infrastructure answers
in a different shape.

## Why not adopt it

**It is a Kubernetes dependency, and Prax just spent a week going the other
way.** Both boxes were retired on 2026-09-14 onto one machine specifically to
cut cost. Prax's deployment story is a Makefile, systemd units and a handful of
containers on a single host, and the suite's stated posture is plug-and-play
add-ons that a solo operator can run. A control plane you `make deploy` into a
cluster is the opposite trade. Nothing here is wrong; it is built for fleets.

**It is pre-stable by its own statement.** Breaking changes to the manifest
schema are promised. Building against `v1alpha1` now buys rework.

**It solves the outer loop, which is not where Prax's differentiator lives.**
Every governance decision Prax makes — risk tiers, the lethal-trifecta guard,
capability ceilings, the audit log — happens *inside* the agent loop, per tool
call, with the argument values in hand. `Gateway` cannot see that a tool call is
the third leg of a trifecta; it can only see a hostname. The two are
complementary, and conflating them would be a category error in either
direction.

## What is worth taking

**1. Resource limits as a declared, enforced field — this is a real open gap.**
`Task` carries CPU and memory limits as part of the manifest. Prax's sandbox has
none. The 2026-07-08 outage was exactly this failure: an unbounded `ffmpeg`
lavfi source wrote a 21 GB file into the container's `/tmp`, and because the
container overlay *is* the host disk, it took the whole box down and Prax's tool
calls with it. The punch-list item to bound sandbox `/tmp` with a sized tmpfs has
been open since. Seeing Google treat limits as a first-class declared field
rather than an afterthought is the nudge to close it — and on zeta the stakes are
higher than on a disposable cloud box, because a runaway container now shares a
disk with TJ's other projects.

**2. Task-per-workload is what per-tenant sandbox isolation looks like when you
actually build it.** Prax's largest known architectural gap is that there is ONE
sandbox container with ONE bind-mounted workspace, which is why eval runs
polluted the real workspace and why dev and prod had to be separated by machine,
then by disk and account. `Task` makes the isolated unit the default rather than
something you carve out. Prax does not need Kubernetes to take the lesson: the
target shape is one sandbox per tenant per run, created and destroyed around the
work, and the sandbox daemon mode (`SANDBOX_DAEMON_URL`) is the existing seam.

## Corroboration worth recording

Two of ax's four primitives are Prax's own security theses, arrived at
independently by Google:

- **`Gateway` is allowlisted egress.** `prax-secrets-proxy` is allowlist-by-
  construction — an unknown provider gets a 404 — and the Tier-2 forward proxy
  is the transparent version of the same control.
- **`Model` centralises credentials away from the workload.** That *is* keyless
  Prax: the agent holds only an access token, the proxy holds the real keys, and
  a compromised agent has nothing to steal.

That is not evidence that Prax's design is correct, and it should not be cited as
such. It is a data point that the two controls a large vendor chose to make
first-class, out of everything it could have declared, are the two Prax already
built. Worth knowing when explaining why the proxy exists.

## Declined

- Adopting ax, or building an ax manifest for Prax. If someone later wants Prax
  on a cluster, ax is a reasonable substrate and Prax would be the process inside
  a `Task` — but nothing needs preparing now, and `v1alpha1` will move.
- Treating it as a competitor to Prax or TeamWork. It has no agent, no tool
  governance, no memory and no UI; it runs other people's agents.
