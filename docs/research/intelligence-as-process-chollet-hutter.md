# Chollet & Hutter — intelligence as *process* (the foundation under Prax's eval philosophy)

[← Research](README.md) · *LLM behavior & foundations lane*

Investigated whether two individuals — **François Chollet** and **Marcus Hutter**
— are worth documenting for Prax. **Verdict: document their IDEAS (not bios, not the
"-pilled" meme), because they are the theoretical spine of the eval philosophy Prax
already runs on — and each yields one concrete adopt.** They earn a foundations
note, not a personality file.

## The two ideas (verified, current)

**François Chollet — intelligence = skill-acquisition efficiency.**
["On the Measure of Intelligence"](https://arxiv.org/abs/1911.01547) (2019) defines
intelligence not as skill at a task but as **how efficiently a system acquires new
skill on tasks it wasn't prepared for**, controlling for **priors, experience, and
generalization difficulty**. His **[ARC-AGI](https://arcprize.org/arc-agi)**
benchmark operationalizes it with novel abstract-reasoning puzzles; it remains hard
(ARC-AGI-2 ~24% under Kaggle compute limits in 2025 vs high-cost frontier scores),
and he now backs it with Ndea — the explicit bet that *scale on static benchmarks
isn't AGI*.

**Marcus Hutter — intelligence = compression + prediction under a universal prior.**
[AIXI / Universal AI](http://www.hutter1.net/ai/uaibook.htm) frames the optimal
agent as one that predicts its environment via **Solomonoff induction** (weight
hypotheses by simplicity) and acts to maximize reward — intelligence as **optimal
compression and prediction with minimal assumptions**. The **[Hutter
Prize](http://prize.hutter1.net/)** makes the thesis concrete: *better lossless
compression of knowledge = better intelligence.*

**The shared claim:** intelligence is a **dynamic process** — learning efficiency,
frontier expansion over a task graph, compression of experience into reusable priors
— **not a static score.** This is exactly what [EDGE-Bench](edge-bench-learning-curves.md)'s
log-sigmoid learning law operationalizes, and exactly the axis the
[benchmark scan](benchmark-scan-2026-adopt.md) found Prax is missing.

## Why they're the foundation Prax already embodies

| Their principle | Where Prax already lives it |
|---|---|
| Chollet: **generalize to novel tasks, don't memorize** | The **never-spike rule** (CLAUDE.md) + **transfer-not-memorize** ([learning-to-theorize](learning-to-theorize.md)) |
| Chollet: **skill-acquisition *efficiency*, not static score** | The new **learning-curve harness-lift** axis (from EDGE-Bench) — does the harness+memory learn *faster*, not just score higher |
| Chollet: **priors + experience** drive acquisition | The memory stack + **self-regeneration loop** (accumulate & reuse experience) |
| Hutter: **compression / simplicity prior (Occam)** | The **MDL/Occam bias** already wired into the self-regen keep-logic — prefer the simplest patch that transfers |
| Hutter: **prediction against a trusted objective** | The **un-gameable deterministic verifier** — reward = a fixed, compressible ground truth, not a judge that can be talked around |

## The two concrete adopts

1. **ARC-AGI as a benchmark (Chollet)** — add it to the do-first shortlist's
   *generalization* slot. It's deterministic (grid in → grid out, exact match),
   CPU-runnable, and tests the one thing coverage-style benchmarks don't: **skill
   acquisition on novel tasks**. It fits the new `BenchmarkAdapter` seam directly.
   Track ARC-AGI-2 / the ARC Prize as an external yardstick for "does Prax *learn*,
   or just *know*."
2. **Compression as a metric (Hutter)** — extend the Occam bias from a tiebreaker
   into a first-class signal: prefer scaffold/memory changes that **compress** — a
   shorter prompt or a smaller retained-context that holds the same score is
   strictly better (MDL). Composes with the HAL cost-axis and the
   loop-cost-per-accepted-change metric.

## Document-don't-adopt

- **AIXI itself** — uncomputable / theoretical; take the *compression-and-priors
  principle*, not the intractable optimal agent.
- **The "-pilled" framing** — a social-media shorthand, not a claim to cite. The
  substance is the two papers, not the meme.

## Sources

- [Chollet, "On the Measure of Intelligence" (arXiv 1911.01547)](https://arxiv.org/abs/1911.01547) · [ARC Prize / ARC-AGI](https://arcprize.org/arc-agi)
- [Hutter, Universal AI (AIXI)](http://www.hutter1.net/ai/uaibook.htm) · [Hutter Prize](http://prize.hutter1.net/)
- Related: [edge-bench-learning-curves](edge-bench-learning-curves.md) · [learning-to-theorize](learning-to-theorize.md) · [benchmark-scan-2026-adopt](benchmark-scan-2026-adopt.md)
