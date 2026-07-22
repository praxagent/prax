# Self-Improvements in Modern Agentic Systems (survey) — a self-audit lens for #29

**Source:** [selfimproving-agent.github.io](https://selfimproving-agent.github.io/) —
*"Self-Improvements in Modern Agentic Systems: A Survey"* (arXiv **2607.13104**;
Ren, Chen, Guo et al., KAUST / Jilin / Alberta) + a living paper map
([Awesome-Self-Improving-Agents](https://github.com/selfimproving-agent/Awesome-Self-Improving-Agents),
239 papers).

**Verdict: document + adopt the *taxonomy* as a standing self-audit lens for the
self-regeneration cluster ([#29](../IDEAS_BACKLOG.md)); document-don't-adopt the
foundation-model (weights) branch.** This is a *map, not a method* — its value to
Prax is (1) a vocabulary that classifies what Prax already does and exposes the
empty cells, and (2) it independently validates the core bet: **improve the
scaffolding, not the weights.** It's a reference note, not a build.

## The one distinction worth internalizing

The survey splits *all* agent self-improvement into two branches:

- **Foundation-model improvement** (73 papers) — change the *model* (self-generated
  demos, evaluative feedback/Constitutional-AI, grounded RL environments, generative
  world models). *"Slower, more persistent, more training-centric."*
- **Scaffolding improvement** (166 papers) — change the *shell around* the model
  (prompt optimization, memory, tools, full-scaffolding/RSI). *"Faster, cheaper, and
  more reversible."*

That 73-vs-166 split, and the "faster/cheaper/reversible" framing of the scaffolding
branch, is the field's own confirmation of Prax's thesis and of every prior
weights-level assessment here ([RLM](rlm-harness-lid.md), [lm-sleep](lm-sleep-consolidation.md),
[ARTS](arts-agentic-tree-search.md), [MORPHEUS](skyfall-morpheus-continual-learning.md)):
the persistent-weights lane is a GPU wall; **the harness is where a hosted-LLM system
actually gets to self-improve.**

## Prax mapped onto the scaffolding branch (what we already have)

| Survey cell | Prax mechanism | State |
|---|---|---|
| **Prompt optimization** (scalar feedback / qualitative refine / population evolution / **textual gradients**) | Self-regeneration: propose a system-prompt overlay → verify on the capability suite → keep iff it improves AND the anti-spike overseer approves (`prax/eval/self_regen.py`, `make eval-self-regen`) | ✅ propose→verify→keep; **no textual-gradient or population search yet** |
| **Memory** (what's stored / structure / processing) | Two-layer memory (Qdrant vectors + Neo4j graph) + the metacognitive learned-failure store + per-space progress | ✅ strong; audit "processing" (consolidation) against the taxonomy |
| **Tools** (routing / refinement / **autonomous creation**) | Plugin system — Prax authors, tests, and activates its own `@tool` plugins (`plugin_fix_agent`); this **is** Voyager-style skill accumulation, framework-gated by trust tiers | ✅ the autonomous-creation cell, with a safety gate the survey's examples lack |
| **Full scaffolding / RSI** (recursively edit operating logic — Darwin-Gödel-Machine) | The self-improve agent edits Prax's own code (`self_improve_*` codegen tools) + the #29 accept-gate cluster (public/private golden split, cost-budgeted selection, anti-spike overseer) | 🔨 the pieces exist; the closed loop is #29 P1 |
| **Evaluative feedback** (intrinsic critic) | claim-audit, trace introspection, the supervising eval auditor, hallucination-guard metrics | ✅ several independent critics |

The exercise is reassuring: **Prax occupies every scaffolding cell**, and in two of
them (autonomous tool creation, RSI) it adds the *safety gate* — trust tiers,
un-gameable fitness, anti-spike overseer — that the survey's representative methods
(Voyager, the Darwin-Gödel-Machine) largely don't have. That gate is the differentiator,
not the mechanism.

## What we might want to change (adopt candidates)

Small, and all reinforce the existing [#29 / AIDE²](aide2-recursive-self-improvement.md)
direction — none is a new pillar:

1. **Textual gradients (TextGrad) for the self-regen proposer.** Today self-regen
   proposes overlays free-form and selects by score. The survey's "textual gradient"
   category (TextGrad, 2025) turns the eval *failure signal* into a *directed*
   natural-language "gradient" that tells the proposer **what** to change and why —
   a more sample-efficient proposer than blind sampling. Worth an experiment inside
   the existing propose→verify loop (the verify/accept gate is unchanged, so it's
   safe). Complements the AIDE² evidence.
2. **Population / evolutionary search over overlays.** The "population evolution"
   category (and AIDE²) argues a *population* of candidates + selection beats
   single-shot iterate. Prax's `accept_change` gate already scores candidates; wiring
   a small population loop on top is a natural, low-risk extension once #29 P1 lands.
3. **The taxonomy as a recurring self-audit.** Re-run the table above whenever the
   #29 cluster moves — it's the cheapest way to spot an empty cell (e.g. memory
   *processing*/consolidation is the least-developed Prax cell) before it becomes a
   gap. Fold into the adopt-tracker.

## Honest limits of the source

- It's a **survey**, so it *organizes* rather than *measures* — no unified benchmark,
  each cited method carries its own paper's numbers. Adopt the taxonomy and the
  pointers (TextGrad, population evolution, MemoryBank, DGM), not any headline result.
- The **foundation-model branch is document-don't-adopt** for the same reason it
  always is here: it needs training infrastructure Prax doesn't have. The survey is
  useful precisely for making that lane's cost explicit and pointing us at the
  scaffolding lane instead.
- Convergent, not novel: it names and organizes what the AIDE², RLM, and eval-rigor
  assessments already argued. Its contribution is the *complete map* — which is
  exactly what a self-audit lens needs.
