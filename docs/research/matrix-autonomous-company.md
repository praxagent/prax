# matrix.build — autonomous multi-agent "company" swarm

[← Research](README.md)

Reference note on **[matrix.build](https://matrix.build/)** — *"Launch a 0-Person
Company that actually earns."*

**Verdict: document-don't-adopt.** It's a different product category from Prax (an
autonomous revenue-generating *company* run by a swarm of agents), the
transferable mechanics Prax already has, and the one genuinely novel idea — a
multi-agent **org/department** layer — is a *deliberate non-goal* for Prax's
bounded-orchestrator design. The adjacent project worth actually watching is
**agent-matrix** (an open agent/tool/MCP registry), not the company-builder.

## Epistemic caveat (read first)

The available signal is a **marketing landing page** (the homepage 403s automated
fetch) plus thin, hype-leaning third-party coverage ("the first *alive* autonomous
AI system"). The headline claim — that it autonomously runs a company that
**"actually earns"** — is **unverified**. Treat this as a *vision statement*, not
evidence, exactly as the [provider-independence note](provider-independence-export-control.md)
pulled the Fugu benchmark cite once its comparisons proved unfair. Documented for
the *pattern*, not as a validated result.

## What it claims to be

- A goal → a **fleet of Neo / Claude Code / Codex agents**, each running **its own
  browser, tools, files, and memory**, deployed in **tiered, continuous loops**.
- **"Departments" coordinate the swarm** "from direction to proof."
- Operates the full loop: **build, ship, distribute, earn** — i.e. autonomous
  product + code + marketing + revenue.

## What Prax already has that maps

| matrix.build | Prax equivalent |
|---|---|
| Each agent has its own browser/tools/files/memory | **prax-sandbox** (per-agent containers + browser + desktop) + memory spoke + workspace |
| Orchestrates Claude Code / Codex agents | The sandbox already drives claude-code / codex / opencode coding agents |
| Tiered, continuous autonomous loops | **task-runner** (autonomous Kanban pickups) + proactivity initiative + self-regeneration (#29) |
| "From direction to proof" | Execution-graph observability + scrubbed receipts + LGTM/Grafana |

So the *substrate* — per-agent isolation, external coding-agent orchestration,
continuous loops, proof-of-work — is already Prax's, via the sandbox + task-runner
+ observability.

## The one real architectural contrast — and why Prax says no (for now)

matrix.build's distinctive bet is a **swarm of peer agents with a department/org
layer**. Prax is the opposite *by design*: a **single bounded orchestrator** (~42
tools, deliberately under the ~50-tool accuracy threshold Anthropic documents)
that **delegates to spokes**, not a fleet of co-equal autonomous agents. That
bound is a load-bearing reliability choice (see [`orchestration.md`](orchestration.md),
[`harness-engineering.md`](harness-engineering.md)), not an oversight: more
autonomous peers = more coordination surface, more ways to drift, harder to keep
grounded and auditable. A multi-agent-org layer is a **fork to record, not adopt**
— if Prax ever needs it, this is the reference point, and it would have to come
*after* the graded-autonomy + un-gameable-fitness work, not before.

The "autonomous company that earns" framing is also a **product/business
direction**, not a harness capability — out of scope for an assistant that serves
a user (it echoes the *agentic-commerce* theme already logged in
[`sierra-agent-platform.md`](sierra-agent-platform.md) as document-don't-adopt).

## The actually-relevant adjacent — agent-matrix

Search surfaced a **different** project, [agent-matrix](https://github.com/agent-matrix)
— pitched as *"a PyPI or Docker Hub for AI Agents"*: an open registry of
production-ready agents, tools, and **MCP servers**. That is far more relevant to
Prax's real direction (the curated **MCP server** + plugin system + "usable by
other agents" surface) than the company-builder. **Adopt-candidate to track**:
if Prax ever publishes/discovers tools across orgs, an open agent/MCP registry is
the interoperability layer — evaluate it alongside the
[ARD](agentic-resource-discovery.md) and [OKF](open-knowledge-format.md)
interchange notes (same "cross-org discovery" problem Prax doesn't have yet).

## Sources

- [matrix.build](https://matrix.build/) · [agent-matrix (open registry)](https://github.com/agent-matrix) · [Matrix OS (background-agent cloud computer)](https://matrix-os.com/)
- Related Prax notes: [orchestration](orchestration.md) · [harness-engineering](harness-engineering.md) · [sierra-agent-platform](sierra-agent-platform.md) · [agentic-resource-discovery](agentic-resource-discovery.md)
