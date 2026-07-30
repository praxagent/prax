# Kernel Forge — MCTS agent harness for CUDA kernel optimization (arXiv 2607.24762)

**Brodsky, Kumar, Kashmira, Danatanarayana, Mars, Flautner, Tang (Michigan /
Clinc lineage).** Open-source harness: an LLM agent generates and optimizes
CUDA kernels for unmodified PyTorch models, exploring with **Monte Carlo Tree
Search** instead of a single refine-loop. Reported (DGX Spark, 50 iterations
per kernel, not independently verified): 1.52× adaptive_avgpool2d (ResNet-50),
1.70× group_norm (SD 3.5), 2.83× softmax (Gemma 4 E2B), 1.54× softmax (Qwen
3.5 35B) over PyTorch eager.

**Verdict: document-don't-adopt the system; bank ONE structural idea for #29 —
search-shaped proposal instead of a single refinement chain.** The domain is
someone else's lane, but the harness shape maps cleanly onto a decision the
self-regen design has not made yet.

---

## Why the domain itself is a pass

CUDA kernel work needs GPUs to *evaluate* candidates — every MCTS rollout is a
compile-and-benchmark on hardware we don't rent (the GPU wall's seventh
sighting, though this one needs inference-grade hardware only at eval time, not
training). And Prax serves hosted models through the keyless proxy; kernel
speedups are our providers' concern, per the inference-listicle verdict.

## The idea worth banking

Their real claim is about **search topology**: a single iterative-refinement
chain gets stuck in the first basin it finds; MCTS keeps a tree of partial
optimizations alive and allocates budget to promising branches. What makes it
work in their setting transfers to #29's proposer:

1. **A fast, objective evaluator per node** — kernel correctness + wall-clock.
   Prax's analogue exists: `make ci` + golden pass-rate + token cost per
   candidate change. Grounded-enough, cheap-enough.
2. **Proposals as tree nodes, not a chain.** The current P1 design ("propose a
   plugin edit, verify, adopt-or-drop") is one rollout deep. AIDE² already
   pushed toward population search; MCTS is the budgeted version of the same
   move — explore several candidate self-edits, expand the ones whose partial
   signals look good, abandon basins early.
3. **Budget as a first-class knob** — 50 iterations/kernel is an explicit spend
   ceiling, which is exactly the `accept_change` cost-axis shape.

This folds into the existing "population/evolutionary search over overlays"
adopt-row rather than becoming a new one: MCTS is a *specific, budget-aware*
instance of that row, with a paper's worth of evidence it beats a single chain
in a domain with objective evaluators. No new tracker row; the survey row gains
this as a cite.

## Honest limits

Abstract-level read; numbers transcribed, not reproduced; no acknowledged
limitations were visible in the fetched content, which usually means the
limitations section wasn't fetched — not that there are none. Four kernels are
showcases, not a distribution. "DGX Spark" is desk-side hardware; transfer of
speedups to server GPUs is unverified.

## Related

- [aide2-recursive-self-improvement.md](aide2-recursive-self-improvement.md) —
  the #29 proposer this idea sharpens.
- [self-improving-agents-survey.md](self-improving-agents-survey.md) — the
  population-search candidate this makes concrete.
- [arts-agentic-tree-search.md](arts-agentic-tree-search.md) — prior tree-search
  sighting; that one needed test-time training, this one only needs an
  objective evaluator, which is why it fares better.
