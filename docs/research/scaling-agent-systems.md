# Towards a Science of Scaling Agent Systems (Google/MIT, 2025) — assessment

**Source:** [arXiv 2512.08296](https://arxiv.org/abs/2512.08296), Kim, Gu, Park, … McDuff,
Liu (20 authors, **Google Research / DeepMind + MIT**), 9 Dec 2025. Empirical
measurement + predictive-modeling study; **no code release** in v1.
**Verdict:** **Document + adopt the *lens*, not the fitted model.** One of the most
directly relevant papers in this collection: its subject *is* the harness-design question
(when does multi-agent beat single-agent?), its method is **inference-only** (matched-
budget API calls, zero weight training — Prax's exact regime), and its findings **validate
hub-and-spoke on quantitative evidence** while handing Prax's eval engine a concrete
instrumentation target. Adopt two things — a **capability-ceiling routing heuristic** and
the **five coordination metrics** as trace-level eval vocabulary — and explicitly *don't*
adopt the fitted regression (its constants are theirs, not ours).

## What it shows (grounded)

The field builds multi-agent systems (MAS) on "more agents is all you need" with no
principle for *when* it helps. This runs a controlled apples-to-apples study — same
prompts, tools, token budgets — over five architectures (single-agent; independent /
centralized / decentralized / hybrid MAS) across **180 configs, 14,742 runs**, four
benchmarks (Finance-Agent, BrowseComp-Plus, PlanCraft, Workbench), three model families
(GPT-5 / Gemini 2.5 / Claude 4.5). Finding: coordination value is set by **task
properties, not agent count**, swinging from **+80.9%** (Finance, Centralized) to
**−70.0%** (PlanCraft, Independent).

The load-bearing results for us:
- **Error amplification** `Ae = E_MAS/E_SAS`: **4.4× for Centralized** (verifying hub) vs
  **17.2× for Independent** (uncoordinated peers). A hub that verifies contains error
  propagation; peers compound it.
- **Capability ceiling** (β = −0.408, p<0.001): coordination yields **negative** returns
  once the single-agent baseline `P_SA` exceeds ~0.45. Fanning out to a task the solo
  agent already handles *loses* accuracy and burns tokens.
- **Tool-coordination trade-off** (β = −0.330, p<0.001): tool-heavy tasks suffer
  *disproportionately* from multi-agent overhead.
- **Overhead** ranges 58% (Independent) → 515% (Hybrid) extra reasoning turns; message
  density saturates at c*≈0.39.
- A mixed-effects regression (~20 terms) predicts per-architecture performance at
  cross-validated **R²=0.513** (they also report 87% optimal-strategy and leave-one-domain
  R²=0.89 — hold those loosely). Derived decision rule: SAS when P_SA>~0.45; Centralized
  when P_SA≈0.35–0.45 and tools≤8; Decentralized when tools>10.

## Why it matters to Prax

**It is external, quantitative support for choices Prax already made — and a caution
against ones it deliberately avoided:**
- **Hub-and-spoke = their "Centralized MAS with a verifying hub"**, the architecture with
  the lowest error amplification (4.4×) that wins the highest-value task. Independent
  peers (17.2×) are exactly the swarm shape Prax rejects (cf.
  [matrix.build](matrix-autonomous-company.md) / [capy](capy-swe-agent-platform.md) — swarm
  as a deliberate non-goal). Now there's a coefficient behind that call.
- **Lean-orchestrator / spoke-internal tools** (Prax's ~50-tool ceiling) is reinforced by
  the −0.330 tool-coordination coefficient: tool-heavy work *especially* shouldn't fan out.
- Its own limitation (#4: "memory architecture + specialization treated as orthogonal, not
  explored") is the axis where Prax is **richer** — persistent two-layer memory + a
  governance layer the paper has no concept of. So don't over-read it as a full model of
  Prax; it under-describes the actual system.

## What to adopt (lens, not constants)

1. **Capability-ceiling routing heuristic (📋).** "Don't fan out to a spoke when the
   orchestrator alone already handles the task" — coordination has real cost (58–515%
   overhead) and negative returns above a competence threshold. A candidate *delegation-
   decision* signal: gate `delegate_*` on an estimate of whether solo handling suffices,
   and let the eval engine measure whether a fan-out actually beat single-orchestrator on
   the same case. Sits next to [difficulty-driven routing](reliable-agentic-systems-bayer.md)
   and the delegate-vs-do-it-all variance already observed.
2. **The five coordination metrics as a trace-level eval vocabulary (📋).** Overhead %,
   error amplification `Ae`, redundancy `R` (cosine similarity of agent outputs), message
   density, coordination efficiency `Ec` are exactly the **trace-grade** signals Prax's
   eval engine + `trace_search` could compute over real orchestrator→spoke runs — the
   "grade the trace, not just the answer" stance ([praxbench](prax-benchmarks.md))
   applied to *coordination*. Concrete: instrument `delegate_*` to log overhead +
   redundancy, then A/B "did fanning out pay off, or just burn tokens?" as an eval-gate
   metric. This is the highest-value, most concrete adopt.

**Do NOT adopt:** the fitted regression (Eq. 1). R²=0.513 explains ~half the variance on
*their* four benchmarks with *their* metric definitions + an external "Intelligence
Index." Lifting their coefficients to predict Prax would be false precision. Adopt the
**methodology** — measure these metrics on Prax's own traces, fit Prax's own model over
the eval matrix if ever needed — not the constants. And no code shipped, so any adoption
is a clean reimplementation of the metrics.

## Bottom line

A rigorous, harness-level, weights-free result squarely in Prax's design space — the rare
paper whose *subject* is the thing Prax is. **Document + adopt the coordination-metric lens
and the capability-ceiling routing heuristic; don't adopt the fitted regression.** It
*reinforces* hub-and-spoke, the verifying orchestrator, and the lean-tool rule with
external coefficients, and it gives the eval engine a concrete, inference-only target:
measure whether delegation actually paid off. Complements
[reliable-agentic-systems](reliable-agentic-systems-bayer.md) (routing), the swarm-is-a-
non-goal thread ([matrix.build](matrix-autonomous-company.md) / [capy](capy-swe-agent-platform.md)),
and the trace-grading stance ([harness-generalization](harness-generalization.md) on
measurement rigor). Caveat: no specialized domains, natural-language message-passing only,
no code — treat the specific numbers as *their* setup, the framework as the takeaway.
