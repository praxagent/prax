# EDGE-Bench (ByteDance Seed) — learning curves as an eval axis

[← Research](README.md) · companion to [benchmark-scan-2026-adopt](benchmark-scan-2026-adopt.md)

Reference note on **[EDGE-Bench](https://edge-bench.org)** (ByteDance Seed) — an
**ultra-long-horizon "environment learning" benchmark**: does an agent *improve*
through real interaction/feedback over **≥12h (some >72h)** horizons, rather than
scoring one-shot?

**Verdict: document + adopt the METHOD (a learning-curve metric + an
experience-reuse-vs-resampling ablation), not the benchmark itself (gated + compute-
heavy). It's the strongest external validation yet of Prax's experience-reuse,
long-horizon, and long-context bets.**

## What it is

134 tasks across 6 domains (Scientific/ML, Systems/SE, NP-hard Optimization,
Knowledge Work, Lean formal math, Games). Each runs a cyclic loop —
**Attempt → Observe → Absorb → Improve** — where the agent **accumulates and reuses
experience** across many submissions (a case study: 247 submissions, 7 "turning
points" where it reframes the problem). Headline results:

- **A log-sigmoid scaling law** in interaction time: `S(t) = S_max / (1 + (t_mid/t)^β)`,
  **mean R² = 0.998** across 402 learning curves — i.e. `dx/d·ln t = β·x(1-x)`,
  frontier-expansion over locked/unlocked task nodes.
- **"Learning speed roughly doubles every 3 months"** (Sep 2025 → May 2026).
- **"Accumulating and reusing task experience drives progress beyond what
  independent restarts achieve"** — reuse beats repeated sampling.
- **Longer context helps throughout** — 1M-context Opus 4.8 stays above the 200k
  variant at *every* checkpoint across the 12h window.

Grading is feedback-signal driven (build logs, test failures, objective/feasibility
checks, rubric critique) — so **mixed** (deterministic env-state + rubric). The full
task set is **gated** (contact ByteDance).

## Why it matters for Prax — it validates three bets and adds one metric

1. **Experience-reuse > resampling** — *the* finding, and it's the empirical case
   for Prax's entire memory stack: `progress_read/append`, `trace_search`, the
   failure journal, trajectory export, skill capture, and the **self-regeneration
   loop**. EDGE-Bench says long-horizon improvement comes from *reusing* experience,
   not restarting — exactly what those mechanisms are for.
2. **Ultra-long horizon (12-72h) is where learning shows** — validates Prax's
   *overnight/multi-day* eval thesis, the **resumable batch runner**, durable
   checkpoints, and the "harden the harness on a slow local model over days" plan.
   EDGE-Bench is the benchmark form of the regime Prax's infra was built for.
3. **Long context helps at every checkpoint (1M Opus 4.8)** — Prax runs on
   **Opus 4.8 1M-context**; this is direct support for the long-context / in-context-
   memory bet the [landscape sweep](agentic-landscape-2026-sweep.md) flagged as one
   of the field's three real leads.

## The concrete adopt — a learning-curve metric

Every Prax eval today is **one-shot** (capability pass-rate) or **pass^k**
(reliability). EDGE-Bench adds a third axis Prax lacks: **improvement per unit
interaction-time**. Adopt it as:

- **Learning-curve harness-lift** — instead of only "full vs bare on one attempt,"
  measure the **slope/`t_mid`/`S_max`** of performance vs submissions (or wall-time)
  on a repeatable task. The right question for a *harness* isn't just "is the score
  higher," it's **"does it learn faster and plateau higher?"** — which is precisely
  what a scaffold + memory should buy.
- **An experience-reuse ablation** — run the same task **with vs without** memory/
  progress-persistence and show the curve separates. That turns "memory helps" from
  a claim into a measured gain, and it's a clean fitness signal for the self-regen
  loop (does a proposed scaffold change raise the *learning rate*, not just one
  static score?).
- **"Learning speed" as a tracked number** — log the 2-hour improvement window per
  eval task, so regressions in *how fast Prax improves* are visible, not just
  regressions in final score.

This composes with the HAL cost-axis (learning gain **per token/hour**) and the
[loops-explained](loops-explained-assessment) cost-per-accepted-change metric.

## Document-don't-adopt

- **The EDGE-Bench task set itself** — gated (contact-to-access) and compute-heavy
  (Lean proofs, production codebases, 72h runs); not a keyless-CI fit. Take the
  *method*; track the leaderboard as an external yardstick.

## Sources

- [edge-bench.org](https://edge-bench.org) · paper `edge-bench.org/paper.pdf`
- Related: [benchmark-scan-2026-adopt](benchmark-scan-2026-adopt.md) · [agentic-landscape-2026-sweep](agentic-landscape-2026-sweep.md) · [autoresearch-labless](autoresearch-labless.md)
