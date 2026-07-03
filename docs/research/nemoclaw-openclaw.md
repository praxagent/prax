# NeMoClaw / OpenClaw (NVIDIA) — lessons for Prax

[← Research](README.md)

Reference note on **[NVIDIA NeMoClaw](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/home)**
and the **OpenClaw** agent framework it wraps. This is the closest external peer to
Prax that exists, so it's read for **lessons**, not adoption.

**Verdict: document + adopt the *security checklist*; treat the architecture as
strong external validation; borrow two patterns (MCP-serves-its-own-docs,
lifecycle management); do NOT replace Prax's stack with it.**

## What it is

- **OpenClaw** — an open-source agent framework that "**originally a personal AI
  assistant, grew into a general-purpose agent platform**" (late 2025). Provides
  agent roles, **state/memory**, **task routing between agents**, and **external
  tool** connection. *(That origin story is Prax's own — OpenClaw is the nearest
  conceptual sibling/competitor.)*
- **NeMoClaw** — **NVIDIA's officially-supported, secure, enterprise distribution**
  of OpenClaw. Single-command install; layers on **OpenShell sandboxes** (NVIDIA
  Agent Toolkit), **security/privacy controls**, **inference routing**, **lifecycle
  management**, an **MCP server** (serving its own docs at `/_mcp/server`), and
  **local open models (Nemotron)** on dedicated GPU systems.

> Source discipline: facts here come from **docs.nvidia.com** + the NVIDIA
> newsroom (authoritative) and the arXiv taxonomy below. The crop of third-party
> "definitive guide" sites are SEO and were not relied on.

## Lesson 1 (strongest) — adopt the security vulnerability taxonomy as an audit

[*A Systematic Taxonomy of Security Vulnerabilities in the OpenClaw AI Agent
Framework*](https://arxiv.org/pdf/2603.27517) enumerates 10 vulnerability classes
for **any** framework with tools + memory + sandboxes + MCP. Prax should self-audit
against each. Current posture (verify before trusting — these are claims to test):

| Vuln class (OpenClaw taxonomy) | Prax posture | Gap? |
|---|---|---|
| **Prompt injection via tool outputs** (most prevalent) | url_reader/Jina fetch; hallucination guard | **GAP** — tool outputs aren't marked untrusted before re-entering reasoning |
| Unsafe deserialization | YAML `safe_load`; workspace syntax-validation | audit other tool-response parsers |
| Path traversal in tool execution | workspace scoped to `workspaces/{user}/`; eval-isolation guards | mostly covered — audit plugin tools |
| Command injection | shell only in the Docker sandbox; eval-mode denylist | covered via sandbox |
| **Memory poisoning** | consolidation validation gate (LTM/STM) | **PARTIAL** — injected tool data → memory path |
| Sandbox escape via native code | prax-sandbox (Docker) + glass-sandbox isolation | covered; track container hardening |
| **MCP authentication bypass** | bearer-gated, per-caller identity, fail-closed | **STRONG** — already done |
| **Tool privilege escalation** (combine low-priv tools) | governed_tool risk tiers + capability gateway | **PARTIAL** — combination escalation is subtle |
| Resource exhaustion via tools | `max_tool_calls`, run timeouts, recursion limits | covered |
| **Confused deputy** (spoofed tool identity) | per-caller MCP identity; governance | **PARTIAL** |

**Action:** track an "agent-framework security self-audit" backlog item against
these 10 classes, prioritising **indirect prompt injection via tool outputs** (the
#1 class) — it composes with the SSRF/egress + activation-gate gaps the
[ARD assessment](agentic-resource-discovery.md) already surfaced and fixed. The
paper's load-bearing line: *autonomous decisions + untrusted tool outputs +
persistent memory = compounding risk; no single mitigation suffices — layer
validation at the tool boundary, model input, sandbox, and memory.*

## Lesson 2 — NVIDIA independently converged on Prax's architecture (validation)

NeMoClaw's enterprise checklist *is* Prax's design, arrived at separately:

| NeMoClaw | Prax equivalent (already shipped) |
|---|---|
| OpenShell **sandboxes**, always-on | **prax-sandbox** (containers + browser + desktop), plug-and-play |
| **Local open models** (Nemotron) on dedicated systems | local/sovereign backend — `VLLM_BASE_URL`, Apertus, the CPU/ds4 direction |
| **MCP server** integration | Prax's curated, bearer-gated MCP server |
| **Inference routing** | model-tiers / `llm_factory` |
| **Lifecycle management** + security controls | governed_tool risk tiers + capability gateway + glass sandbox |
| Single-command install + **AI-readable markdown docs** | fresh-install smoke test + `AGENTS.md`-style docs an agent can follow |

That a major vendor shipped the same stack is the strongest signal yet that Prax's
**sleek-core + plug-and-play + local-first + MCP + governed-tools** bets are right.

## Lesson 3 — two patterns worth borrowing

1. **MCP-serves-its-own-docs (`/_mcp/server`).** NeMoClaw exposes its *operational
   documentation* over MCP so an AI assistant can read it and operate the system.
   Prax's MCP server could expose Prax's own ops/docs surface — a concrete step in
   the "usable by (and self-operable by) other agents" direction.
2. **Lifecycle management as a first-class layer** (onboarding → operation →
   oversight). Prax has the pieces (smoke test, governance, observability) but not a
   named lifecycle layer; worth consolidating as deployments scale.

## Document-don't-adopt

- **NeMoClaw the distribution** — GPU/enterprise-heavy (OpenShell, DGX, Nemotron-
  local); Prax is its own assistant with a CPU-first, plug-and-play stack. Adopting
  it would *replace* Prax, not improve it.
- **OpenClaw the framework** — the nearest competitor; useful to study, not to build
  on (Prax already owns the equivalent surface).

## Sources

- [NeMoClaw docs (NVIDIA)](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/home) · [NVIDIA newsroom](https://nvidianews.nvidia.com/news/nvidia-announces-nemoclaw)
- [OpenClaw security taxonomy (arXiv 2603.27517)](https://arxiv.org/pdf/2603.27517)
- Related Prax notes: [agentic-resource-discovery](agentic-resource-discovery.md) · [plugin-sandboxing](plugin-sandboxing.md) · [harness-engineering](harness-engineering.md) · [provider-independence-export-control](provider-independence-export-control.md)
