# Claude Science (Anthropic) — is Prax capable of this?

[← Research](README.md)

Reference note on **[Claude Science](https://www.anthropic.com/news/claude-science-ai-workbench)**
(Anthropic, beta, released 2026-06-30) — an AI "workbench" for life-sciences
researchers.

**Verdict: document + adopt two concrete patterns; strongest architecture
validation yet (it's from Prax's own model provider); the domain layer is
plug-and-play, not a capability gap.** Short answer to "is Prax capable of this?":
**yes on the architecture, no on the domain content** — and the domain content is
exactly what Prax's plugin/MCP/spoke design exists to add.

## What it is

A generalist **coordinating agent + 60+ curated skills/connectors** for genomics /
single-cell / proteomics / structural-bio / cheminformatics, doing literature
analysis → multi-step execution → manuscript prep. Standout pieces:

- **Reproducibility artifacts** — every figure carries "the exact code and
  environment that produced it, a plain-language description of how it was
  created, and the full message history."
- **Compute management** — drafts an analysis plan, **requests approval before
  accessing new resources**, and submits jobs to **your lab's own infra** (laptop
  / Linux box / HPC login node).
- **Reviewer agent (actor-critic)** — "inspects the outputs, flagging incorrect
  citations, untraceable numbers, and figures that don't match their underlying
  code, and self-correcting."
- Connectors: UniProt, PDB, Ensembl, Reactome, ClinVar, ChEMBL, GEO; NVIDIA
  BioNeMo (Evo 2, Boltz-2, OpenFold3); Modal compute.

## The architecture is a near-mirror of Prax (validation)

Anthropic independently built the same shape Prax is built on — the strongest
external validation to date, since it's from the provider Prax runs on:

| Claude Science | Prax equivalent (already shipped) |
|---|---|
| Generalist **coordinating agent** + curated skills/connectors | Bounded orchestrator + **spokes** + plugin system + curated **MCP server** |
| **Actor-critic reviewer** (citations / untraceable numbers / figure-code mismatch) | **maker ≠ checker** — claim_audit + golden auditor + the self-regen overseer just shipped |
| **Reproducibility artifacts** (code + environment + message history, auditable) | prax-sandbox (captures code/env) + execution-graph traces + scrubbed eval receipts + library author-provenance |
| **Compute mgmt**: draft plan → approve → run on YOUR infra | agent_plan + governed-tool confirmation + graded autonomy + prax-sandbox (local/remote daemon) + cloud-GPU power broker |
| Runs on the user's own laptop/Linux/HPC | prax-sandbox local or `SANDBOX_DAEMON_URL`; the sovereign / on-prem stance |
| Custom specialist agents / skills | `plugin_write` / self_tools / skill capture |

## So — is Prax capable of this?

**Architecturally, yes.** Prax already has the coordinating-agent + skills +
reviewer + user-infra-compute + reproducible-trace machinery. What it **lacks is
the life-sciences DOMAIN LAYER**: the 60+ science skills, the scientific-database
connectors (UniProt/PDB/Ensembl/ClinVar/ChEMBL/GEO), the BioNeMo model
integrations, and the manuscript/figure workflows. Those are **domain
plugins/MCP connectors + model backends**, not new core capability — precisely the
plug-and-play surface Prax is designed around (connectors via MCP; BioNeMo/Evo2/
Boltz2 via the `VLLM_BASE_URL`/GPU-sandbox rails; skills via the plugin system).
Building "Prax for science" is a *content* project on top of the existing harness,
not a re-architecture. (Case studies — Manifold Bio, Allen Institute ~2yr→faster
lit review, UCSF ~1/10th time — are credible but user-reported accelerations.)

## Two patterns worth adopting NOW (domain-agnostic)

1. **First-class reproducibility artifacts.** Prax has all the pieces (sandbox
   code/env capture + execution-graph message history) but doesn't *bundle* them
   into one auditable artifact attached to an output. Adopt: a "reproducible
   result" wrapper for the research/knowledge/sandbox spokes — output + exact
   code + environment + plain-language method + trace id. This is the general form
   of the eval "receipts" and directly raises trust for any analytical output.
2. **A traceability reviewer** (extends maker≠checker). Claude Science's reviewer
   checks a specific, high-value property: **do the numbers/figures/citations
   trace to their source?** (untraceable number → flag; figure ≠ its code → flag;
   citation not real → flag). Prax's claim_audit covers grounding/hallucination;
   a dedicated *traceability* reviewer (every quantitative claim maps to a
   computation/source) is a concrete extension — and composes with the grounding
   golden and the interpretability "share flowing through interpreted concepts"
   idea from [`inherently-interpretable-models.md`](inherently-interpretable-models.md).

## Document-don't-adopt

- **Claude Science the product** — a closed Anthropic desktop app; Prax is its own
  agent-agnostic harness. Study it, don't build on it.
- **The life-sciences vertical** — a product direction, not a harness gap; pursue
  only if serving scientists is a goal (then: domain plugins + MCP connectors).

## Sources

- [Claude Science (Anthropic)](https://www.anthropic.com/news/claude-science-ai-workbench)
- Related Prax notes: [nemoclaw-openclaw](nemoclaw-openclaw.md) · [inherently-interpretable-models](inherently-interpretable-models.md) · [harness-engineering](harness-engineering.md) · [grounding](grounding.md)
