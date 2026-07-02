# Learning to Theorize the World (NEO) — transfer-not-memorize, formalized

[← Research](README.md) · *LLM behavior & interpretability lane*

Reference note on **[Learning to Theorize the World from Observation](https://doojinbaek.github.io/publications/learning-to-theorize-the-world/)**
(Baek et al.) — a cognitive-science-flavored ML paper. Read for its **harness
principles**, per this lane's premise.

**Verdict: document + adopt ONE principle (an MDL/Occam bias for the self-regen
loop); track the eval methodology. Not a build** — NEO is a custom neural
architecture for symbolic program induction over toy domains, not an LLM-agent
tool. Its *value to Prax is conceptual reinforcement + one concrete tweak.*

## What it says

Understanding ≠ accurate prediction. The paper argues understanding requires
inferring an **explicit explanatory theory**: an *executable generative mechanism*
that transforms a source observation into a target via a **composition of learned
primitive operations** — reusable compositional rules, not next-observation
prediction. Method: **NEO (Neural Theorizer)** — encoder → *programmer* (pick the
next primitive) → *executor* (shared transition model) → **adaptive length via
minimum description length** (don't overexplain simple phenomena). Benchmark:
**OTIB** (GridWorld / arithmetic factorization / image editing), whose protocol is
the point: infer a theory from **support** pairs, then **transfer** it to new
**query** inputs — testing generalizable rules vs instance memorization.

Two findings land hardest:
- **"High self-explainability does not guarantee transfer."** A rationale that
  *sounds* like a good explanation can still fail to generalize.
- NEO holds up under **compositional and length OOD** where prediction baselines
  collapse; it recovers primitive codes **even when primitives never appear in
  isolation** in training.

## Why it matters for Prax — it formalizes the never-spike discipline

This is the theoretical backbone of the rules Prax already runs on:

- **Transfer, not memorize = the [`CLAUDE.md`](../../CLAUDE.md) "never spike
  benchmarks" rule.** A harness fix must be an *abstraction of a problem class*
  that generalizes — exactly NEO's "infer a rule from support, verify it transfers
  to query." The **self-regeneration loop** ([`self-regen`](../../prax/eval/self_regen.py))
  and its anti-spike overseer are doing precisely this; NEO is the formal argument
  that it's the right target.
- **"Self-explainability ≠ transfer" is a sharp warning for maker≠checker.** An
  LLM auditor/reviewer that judges a change by how *well-reasoned its rationale
  reads* can be fooled — a plausible explanation is not evidence of generalization.
  The only trustworthy signal is **held-out transfer** (deterministic verification
  on cases not used to derive the change). This reinforces keeping the verifier
  deterministic and the overseer backed by a deterministic pre-filter — both
  shipped.

## The one concrete adopt — MDL / Occam bias in the self-regen loop

NEO's **adaptive length (minimum description length)** — "don't overexplain simple
phenomena" — is a directly adoptable principle: among self-regen candidate patches
that improve the metric, **prefer the shortest/simplest** one (Occam's razor for
scaffold changes). The loop already caps patch length and requires a margin; an MDL
tiebreaker (on comparable gains, keep the shorter patch) would bias it toward
compact, generalizable improvements over accreting prompt bloat. Composes with the
`loop_cost_per_accepted_change` golden. *~small change; offered, not yet wired.*

## Track — theory-transfer as an eval protocol

OTIB's **support → query transfer** split is a clean methodology for *any* Prax
self-improvement: derive/tune a change on one case set, then verify it on a
**held-out** set before crediting it — the anti-overfit discipline for #29 and the
capability suites. (Same held-out-fitness principle as DGM/AlphaEvolve in the
[landscape sweep](agentic-landscape-2026-sweep.md).)

## Document-don't-adopt

- **NEO the architecture** — a bespoke neural program-inducer for toy symbolic
  domains; not an LLM-agent component Prax would run.
- **The OTIB benchmark** — a research probe, not an agentic eval for Prax's regime.

## Sources

- [Learning to Theorize the World from Observation](https://doojinbaek.github.io/publications/learning-to-theorize-the-world/)
- Related: [inherently-interpretable-models](inherently-interpretable-models.md) · [llm-emotion-manifolds](llm-emotion-manifolds.md) · [agentic-landscape-2026-sweep](agentic-landscape-2026-sweep.md) · [diffuse-ai-control-judge-robustness](diffuse-ai-control-judge-robustness.md)
