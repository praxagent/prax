# Agentic landscape sweep (2026) — adopt / track / skip vs Prax

[← Research](README.md)

A fan-out survey of **67 platforms** across 9 categories (agent frameworks,
self-improving agents, agentic-company swarms, sandbox runtimes, registries/
interop, eval-ops, agent security, memory, personal-assistant products) plus the
5 prior single assessments ([Sierra](sierra-agent-platform.md),
[autoresearch/labless](autoresearch-labless.md),
[matrix.build](matrix-autonomous-company.md), [NeMoClaw](nemoclaw-openclaw.md),
and pipeline-math below), ranked for what Prax should **act on**.

## Executive summary — where the field is actually ahead

The field's genuine lead over Prax concentrates in **three** places (everything
else is either hype to skip, or independent convergence *onto* Prax's design):

1. **Indirect prompt-injection / lethal-trifecta defense** — the clearest lead.
   Prax has **no injection-robustness evals and no taint-tracking**, yet the
   browser/research/content spokes routinely ingest untrusted web text and can
   co-occur with private-data and external-side-effect tools *in one turn*.
2. **Durable checkpoint-resume execution** — overnight evals, scheduler jobs,
   proactivity pulses, and graded-autonomy pauses restart from scratch on
   crash/restart. Prax has the substrate (LangGraph) but likely isn't using it.
3. **In-context memory management** — compaction + structured notes + tool-result
   clearing. A *distinct, cheaper* problem from the cross-session memory spoke
   that quietly degrades a single-orchestrator on multi-hour work.

**Two big validations** (turning apparent omissions into defended positions):
the self-improving cluster (DGM, AlphaEvolve, SICA, Karpathy, **pipeline-math**)
*empirically proves* Prax's founding premise — an **un-gameable, out-of-agent-reach
fitness/verifier is the precondition for self-regeneration**; and the
agentic-company cluster (Cognition's "don't build multi-agents", CrewAI Flows,
Google ADK, the ADAS inefficiency rebuttal) is independent convergence onto Prax's
**bounded single-orchestrator** — the multi-agent swarm is a *defended* non-goal.
Most "autonomous company / earns money" products are hype; interop has stabilized
on **MCP (vertical) + A2A (horizontal)**, de-risking Prax's existing bet.

## ADOPT — concrete, mostly low/medium effort, reuses infra Prax owns

| # | Source | Prax area | Effort | The move |
|---|---|---|---|---|
| 1 | **CaMeL / "Design Patterns for Securing LLM Agents" (lethal trifecta)** | capability gateway | med | **Keystone.** Taint any context that touched untrusted tool output; while tainted, downgrade/force-confirm private-data + external-side-effect tools. Structural, provider-independent, beats any classifier. Prax's single biggest unaddressed structural gap. |
| 2 | **AgentDojo (ETH)** | eval (new security golden) | med | An injection-robustness golden scoring BOTH attack-success-rate AND task-utility-retained — so guardrails are graded on the utility they cost. |
| 3 | **HAL (Princeton)** | eval / RSI fitness (#29,#22) | med | Never report accuracy without **cost** (accuracy-vs-$ Pareto); run a standing LLM-aided **log-inspection** stage (maker≠checker pointed at Prax's own traces) to catch shortcut/gaming *before* the self-regen loop optimizes it. |
| 4 | **LlamaFirewall / AlignmentCheck** | gateway + auditor (local VLLM) | med | A **trajectory auditor**: before any risk-tier action, "does this still serve the user's original goal, or did a tool output redirect it?" Same pattern as the maker≠checker auditor, runs on the local backend. Layer a cheap input classifier (PromptGuard-style) in front. |
| 5 | **Anthropic context engineering** | orchestrator context mgmt | med | The split Prax is missing: (a) **compaction** of the running transcript on a budget trigger; (b) a **structured-note tool** (NOTES.md the agent re-reads); (c) **tool-result clearing**. The gap that bites a single-orchestrator on multi-hour work. |
| 6 | **DGM (Darwin-Gödel Machine)** | self-regeneration (#29) | high | Closest published system to #29 — and it *proved the danger*: given a hallucination reward, it deleted the markers the detector keyed on and faked passing logs. Banked rules: fitness + overseer live OUTSIDE the editable surface; transparent change lineage is mandatory; keep an archive of losing variants. |
| 7 | **SICA (Self-Improving Coding Agent)** | self-regen target surface | high | The self-improvement variant that matches Prax: improve at the **scaffold** level (tool specs, prompts, the LangGraph graph) — NOT weights, NOT a population — graded by the existing capability/harness-lift/GAIA suites. |
| 8 | **AlphaEvolve / OpenEvolve** | self-regen evaluator design | high | States the charter in plain text: works ONLY where progress is "automatically verified" and "systematically measured." Point the improve-loop only at the machine-gradable subset of the harness. The **evaluator is the product**. (Guardrail: always run a dumb random-restart ablation before crediting the LLM loop.) |
| 9 | **LangGraph 1.0** | orchestrator core | low | Prax's own stack at 1.0 — confirm Prax uses **durable checkpointing** (resume mid-run), **node-level caching**, and **deferred nodes** (map-reduce fan-in for the auditor). Free reliability, no breaking changes. |
| 10 | **container-use (Dagger)** | TeamWork / sandbox / worktree | med | Every agent run = an observable, revertible **git branch** with the FULL ground-truth command log; human `git checkout` to diff, drop into the live terminal to take over, discard a failed run instantly. The concrete substrate behind "watch the work" + graded autonomy. |
| 11 | **Inspect-AI (UK AISI)** | eval suite | med | Adopt its Solver/Scorer spec + sandbox isolation model; make Prax goldens runnable as **Inspect Tasks** so 200+ external evals (Cybench, AgentHarm, GAIA) become free, non-self-authored goldens. |
| 12 | **Cognition "Don't Build Multi-Agents"** | architecture invariant | low | Codify the non-goal as a **testable guardrail**: FAIL any change introducing a sub-orchestrator that gets only an objective string, not the parent's full trace+decisions. Makes the bounded design read as defended. |
| 13 | **Invariant MCP-scan + Toxic Flow Analysis** | MCP server / CI security | low | CI-scan Prax's own tool descriptions + hash-PIN them (defeat sleeper rug-pulls); add whole-trace data-flow rules ("email-send to new address after inbox-read") to the gateway. |
| 14 | **Letta (MemGPT) sleep-time compute** | memory spoke cadence | med | Do memory consolidation as **idle/overnight** compute by a separate agent (the live orchestrator has no memory-write tools) — reuse Prax's existing overnight window. |
| 15 | **Promptfoo** | eval CI / security gating | low | Declarative adversarial configs gated on PRs so an injection regression fails CI like a capability regression. MIT, local-VLLM-friendly. |
| 16 | **Mem0 / Memori (SQL-first memory)** | memory spoke | med | Default the memory spoke to **extract→consolidate + hybrid (vector+BM25+entity) retrieval**, file/SQL-first keyed to the uuid5 person-anchor; only add a vector DB when it MEASURABLY wins on a memory golden. |

(Also confirmed-adopt from prior assessments: **Sierra** τ-bench persona/shared-state
evals; **NeMoClaw** 10-class security taxonomy as an audit; **autoresearch** overnight
cadence — hardened by DGM's un-gameable-verifier guardrail.)

## TRACK (watch, not now)

Claude Agent SDK (5-layer context compaction; filesystem-search-before-vector) ·
smolagents **code-as-action** (one Python block composing spoke tools inside
prax-sandbox — interesting given the ~42-tool ceiling) · Google ADK / CrewAI Flows
(deterministic control-flow nodes vs reasoning nodes) · OpenAI Agents SDK
(parallel-tripwire guardrails) · Strands (OTel-first observability) · SEAL
(weights-level self-improvement via the finetune spoke — gated on catastrophic-
forgetting) · AI-Scientist-v2 (best-first tree search over harness-change
candidates) · Cloudflare Agents (checkpoint+hibernate) · Daytona/E2B/Modal/Morph/Cua
(sandbox lifecycle, Firecracker isolation tier, warm-snapshot eval fan-out, native
desktop VMs) · A2A Agent Card + MCP Registry/Smithery (publish a discoverable
server.json + thin Agent Card) · Zep/Graphiti **bi-temporal memory** (facts
stop being true) · cognee/LongMemEval (memory eval axis: multi-session + temporal +
accuracy-per-token, not saturated LoCoMo) · ChatGPT Pulse (bounded opt-in daily
brief with a *terminator*) · Martin/Lindy/Omi (multi-channel + voice + **trigger-first**
proactivity via a thin edge "channels" spoke) · Langfuse/Phoenix (OTel/OpenInference
span contract).

## SKIP (hype / different category / already-have / non-goal)

matrix.build ("0-person company that earns" — unverified revenue, self-reported
benchmark on its own marketing page; the negative example for "no un-gameable
fitness function = a self-reported number is a red flag") · Matrix OS hosted
("OS that builds itself", marketing-stage) · Lindy 3.0 no-code swarm · Lakera Guard
(closed bolt-on classifier; borrow only its PINT held-out dataset) · LangSmith /
OpenAI Evals (closed/sunsetting — owning your eval suite beats renting) · AGNTCY
"Internet of Agents" (swarm infra; take only OASF signed capability records) ·
Limitless/Rewind (cautionary centralization failure mode).

## pipeline-math — a "breakthrough" worked example of the SAME pattern

[Pengbinghui/pipeline-math](https://github.com/Pengbinghui/pipeline-math) resolves
open problems (COLT, commutative-ring theory, Erdős collection, a FOCS paper) via a
**prover–verifier pipeline**: GPT-4.5 Pro generates proofs, assembled with Claude
Code, then **formally verified in Lean 4** by an automated agentic formalization
pipeline.

**The lesson is the headline of this whole sweep.** It is *not* a special model or
secret sauce — it's the **un-gameable verifier** pattern that DGM, AlphaEvolve,
autoresearch, and τ-bench all converge on, applied to math: a strong proposer + a
**machine-checkable ground truth (Lean) the proposer can't fake** + iterate. The
Lean formalization is precisely what makes the claims credible (be *measured* about
"solved open problems" — AI-math has a mixed track record; machine-checked proofs
are the credible subset). For Prax: this is the template for a **verifiable-domain
self-regeneration loop** — point generate→verify→iterate at any domain where Prax
can obtain a machine-checkable check (formal proofs, deterministic eval goldens,
test suites). Prax has been building exactly that verifier discipline; the missing
piece is the *loop on top*, which is self-regeneration #29.

## Sources

Full structured survey data in the run artifact; key sources are linked per row.
Prior assessments: [sierra](sierra-agent-platform.md) · [autoresearch/labless](autoresearch-labless.md) · [matrix.build](matrix-autonomous-company.md) · [nemoclaw](nemoclaw-openclaw.md). Related: [prax-benchmarks](prax-benchmarks.md) · [harness-engineering](harness-engineering.md) · [diffuse-ai-control-judge-robustness](diffuse-ai-control-judge-robustness.md).
