# Cognitive Architectures for Language Agents (CoALA, 2023) — assessment

**Source:** [arXiv 2309.02427](https://arxiv.org/abs/2309.02427), Theodore Sumers, Shunyu
Yao, Karthik Narasimhan, Thomas Griffiths (Princeton), Sep 2023 (v3 Mar 2024). A
**framework / position paper** — zero code, zero weights. (Yao is the ReAct / Tree-of-
Thoughts author, so this is the ReAct lineage writing its own retrospective taxonomy.)
**Verdict:** **Document + adopt as a self-audit *lens* — do not adopt as architecture.**
CoALA is the field's canonical "periodic table" for agent design, and Prax already
instantiates most of it without having read it (the tell that it won't hand us new
features). But it's a *sharp mirror*: mapping Prax onto its axes surfaces two genuinely
actionable, generalizable gaps — **memory-writes as first-class governed actions** and
the **unlearning / memory-obsolescence** hole Prax has no answer for — plus a clean
shared vocabulary for the memory stack. Its blind spot (no safety/trust/governance axis
at all) is exactly Prax's thesis, so it validates our *shape* while saying nothing about
our *point*.

## The framework (grounded)

CoALA reframes an LLM as a **probabilistic production system** and *prompting as control
flow* over it, then describes any language agent on three axes:

- **Memory (modular):** *working* (active symbolic state for the current decision cycle —
  the hub), *episodic* (past trajectories/experience), *semantic* (facts about world +
  self), *procedural* (both implicit = LLM weights, and explicit = agent code).
- **Action space:** *external* actions = **grounding** (physical / dialogue / digital
  environments) vs. *internal* actions = **retrieval** (long-term → working memory),
  **reasoning** (LLM transforms working memory), **learning** (write to long-term memory).
- **Decision cycle:** a **planning stage** (*propose → evaluate → select* candidate
  actions) then an **execution stage** (apply, observe). Loop.

It scores five landmark agents on these axes to make the point that real systems are
*partial*: SayCan (procedural only, grounding only), ReAct (no long-term memory; reason +
ground; propose-only, no evaluate), Voyager (procedural memory + skill-library learning,
all four actions), Generative Agents (episodic/semantic, full loop), Tree-of-Thoughts
(no memory, reasoning-only, full propose-evaluate-select).

Named open problems (2023, and telling): autonomous memory read/write beyond RAG;
**learning beyond fine-tuning** — meta-learning via *code modification*, optimizing
*retrieval procedures*, and **"unlearning"** (deleting obsolete memories); interleaving
retrieval with planning; agents that modify their own decision procedures ("not yet
implemented").

## Mapping Prax onto CoALA — what the lens surfaces

| CoALA axis | Prax's realization | What the lens shows |
|---|---|---|
| **Working memory** | `agent_plan.yaml` + per-turn system-prompt injection | Matches CoALA (ephemeral hub). CoALA's "working memory is the central hub every module reads/writes" makes the *fable_feedback* "per-turn state in process globals" critique legible as a **working-memory-scoping** bug, not a vague smell. |
| **Episodic** | traces + `trace_search`, conversation history | Well covered. |
| **Semantic** | Qdrant vectors + Neo4j graph (two-layer) | **Ahead** of the paper (it assumes flat semantic memory; Prax has vector+graph). |
| **Procedural (explicit code)** | plugin system, self-regen loop | CoALA is *prescient* here: "learning = modify your own procedural memory (code)" and "agents that modify their own decision procedures are not yet implemented" **name Prax's [self-regen loop](aide2-recursive-self-improvement.md) (#29) as the frontier.** Good external framing to cite. |
| **Internal action: learning (memory write)** | memory-write tools, consolidation | CoALA's insistence that **learning is an action in the same space as tool calls** is the actionable lens (below). |
| **External = grounding** | ~97 governed tools, sandbox, browser | Covered — and Prax's **governance/trust-tier** layer is something CoALA has *no concept of*. |
| **Decision: propose/evaluate/select** | orchestrator + spokes + eval engine | Prax's spokes ≈ action modularity; CoALA's explicit **evaluate** step (value a candidate *before* committing) is thin in a ReAct-style loop — a named hook for an in-loop critic/eval step. |

## What to actually take (lens, not code)

1. **Memory-writes are first-class actions → they belong under governance.** CoALA's
   cleanest transferable stance: *learning (writing to long-term memory) sits in the same
   action space as external tool calls.* Prax governs external tools (risk tiers, audit,
   lethal-trifecta guard) but treats memory-writes as a side effect. The lens says a write
   to Qdrant/Neo4j — especially one derived from untrusted tool output — deserves the
   **same governed-tool audit trail** as an external action (it's a persistence surface an
   injection can poison). Generalizable, and it connects to the provenance-tainting
   middleware. 📋 backlog.
2. **Unlearning / memory-obsolescence — a named gap Prax has no answer for.** CoALA flags
   deleting obsolete/wrong memories as unexplored, and Prax's two-layer store has no
   deletion/decay/obsolescence story — stale facts accrete. This pairs with the
   [self-improving survey](self-improving-agents-survey.md)'s "memory consolidation is the
   weakest cell" and the [learnable-novelty](learnable-novelty.md) consolidation-gate: the
   write side needs a *forget* side. 📋 backlog.
3. **The four-memory vocabulary as the memory-stack's documentation spine.** working /
   episodic / semantic / procedural maps almost 1:1 onto Prax's stack and gives a clean
   shared language (and a periodic self-audit: re-map Prax onto the table when the memory
   or self-regen work moves — same discipline as the self-improving-survey taxonomy audit).

## Why not adopt it as architecture

Modular memory + tool/grounding actions + plan/execute loops are now **baked into
LangGraph** (which Prax builds on) and standard practice — CoALA won't tell a 2026 harness
anything new *structurally*, and Prax already occupies most of its cells. Crucially, CoALA
is a **capability taxonomy, not a safety one**: it has no trust/governance/eval-gate/
multi-tenant-identity axis — precisely Prax's differentiators. So it validates that Prax's
*shape* is sound and gives a vocabulary + two real backlog gaps, but it is silent on the
security thesis that is the actual point of Prax.

## Bottom line

The foundational agent-architecture framework, and it aged well *as vocabulary* while
being *superseded as structure*. **Adopt as a recurring self-audit lens** (memory-axis
vocabulary + "learning-is-a-governed-action" + the unlearning gap); **don't adopt as
architecture** — Prax already is a fairly complete CoALA agent, and its safety layer lives
entirely outside CoALA's scope. Complements [self-improving-survey](self-improving-agents-survey.md)
(the self-audit-taxonomy discipline), [active-inference](active-inference.md) (the other
"agent as principled loop" lens), and [learnable-novelty](learnable-novelty.md) (the
consolidation/forgetting side of memory).
