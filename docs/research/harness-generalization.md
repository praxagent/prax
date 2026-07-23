# Automated Discovery Has No Universally Superior Harness (Gupta et al., 2026) — assessment

**Source:** [arXiv 2607.18235](https://arxiv.org/abs/2607.18235), Akshat Gupta, Jermaine
Lei, Alexander Lu, Gopala Anumanchipalli (UC Berkeley), Leshem Choshen (MIT / MIT-IBM
Watson), 20 Jul 2026. Code: [github.com/akshat57/harness-generalization](https://github.com/akshat57/harness-generalization).
**Verdict:** **Document + adopt the *pattern*; don't vendor the machinery.** This is an
*inference-only, scaffolding-level* paper — the good kind for Prax. Its evolutionary
program-search "harnesses" (OpenEvolve, TTT-Discover, MAP-Elites, island evolution)
don't drop into a tool-using agent orchestrator, but its two load-bearing ideas —
**(1) statistical rigor before declaring an eval win, and (2) treating harness/config
choice as an online, early-signal-driven allocation rather than a fixed recipe** — map
cleanly onto Prax's **eval engine and self-regeneration loop (#29)**, and directly
reinforce the flag-eval and public/private-split discipline already in place.

## What the paper actually shows

LLM-guided *discovery* systems (FunSearch, AlphaEvolve, OpenEvolve) bundle many design
choices — archive construction, parent selection, exploration policy, budget split —
into one "harness," and are usually reported with too few trials to tell a real gain
from run-to-run variance. The authors decompose two harness families down to a greedy
**Sequential Best-of-N** baseline and add components one at a time, then evaluate under
**budget-matched, repeated-trial statistics** (>3.1M rollouts, 4 models 3B–120B, 3 math
tasks = 12 model×problem pairs, 30 harnesses).

Three findings, with the numbers that matter:
- **No fixed harness wins after correction.** The best fixed config (ε-greedy, K=1,
  ε=20%) reached `P_maj=0.914`, raw `p=0.023` — but **Holm-corrected `p=0.678`**, not
  significant. The *full* OpenEvolve config was among the **worst** (`P_maj=0.033`). So
  the elaborate machinery generally **underperforms** the simple baseline, and no tested
  harness beats it significantly. "Harnesses have a generalization problem."
- **Early progress predicts final performance.** Spearman(partial, final) is weak at the
  10% checkpoint (0.00–0.47) but **>0.70 for 11/12 pairs by the 50% checkpoint** — enough
  signal to pick a winner online at half budget.
- **Online allocation beats fixed choice, at equal budget.** Adaptive prune-and-reallocate
  schedules hit **85.75%** (best: three-stage `12→5→2→1`, pruning at 25/50/75%) vs.
  **84.35%** Sequential-BoN, **84.54%** unpruned portfolio, **82.49%** single-harness
  commitment — beating the unpruned portfolio on 11/12 pairs. Modest (~+1.2 pts over the
  portfolio) but *real* (SE 0.02) and **budget-neutral** by construction.

The statistical protocol is the actual contribution: build an **empirical null** from
repeated baseline runs, test each harness with a **best-of-five bootstrap** (R=100k),
aggregate to a cross-pair **majority-win** statistic, and apply **Holm correction**.

## Why the *machinery* doesn't port — but the *discipline* does

- **Domain gap, stated honestly:** the paper's "harness" always means an
  *evolutionary/discovery-search* recipe over math problems (circle packing, Heilbronn,
  autocorrelation) — not an agent orchestrator. The specific components (UCT/PUCT trees,
  MAP-Elites, multi-island evolution) are program-search apparatus Prax doesn't run. The
  finding "complex OpenEvolve underperforms simple BoN" is a *within-discovery-search*
  result — **do not over-generalize it to "simpler is always better" for agent
  scaffolding.**
- **But the method needs no weights and no GPU.** Every rollout is off-the-shelf LLM
  *inference*; the adaptive-allocation mechanism is budget-neutral (same best-of-five
  spend, allocated differently). Fully realizable in Prax's hosted-LLM, keyless-CI,
  CPU-only setting.

## What's worth adopting (two patterns, both eval-engine-level)

1. **Significance testing before an eval "win" — highest-confidence adopt.** Prax runs
   flag A/Bs on the eval gate ([flag-eval-campaign](flag-eval-campaign-2026-07-08.md)) and
   a public/private golden split ([aide2](aide2-recursive-self-improvement.md), PR #80),
   but doesn't yet gate a config change on a **repeated-trial, corrected significance
   test**. This paper's recipe — *empirical-null distribution from repeated baseline runs
   + best-of-N bootstrap p-value + Holm/majority-win correction across tasks* — is a clean,
   keyless, CPU-only way to answer "is this a real improvement or run-to-run noise?" It is
   the [prime directive's](../../CLAUDE.md) "audit the measurement before the model"
   discipline made quantitative, and it directly hardens the **self-regen accept-gate**
   (don't adopt a change whose apparent win is inside the noise band). 📋 — lands in
   `prax/eval/` as a significance helper the flag/golden campaigns call.
2. **Adaptive harness/config allocation — treat config as a problem-dependent
   hyperparameter chosen online.** The winner varies by model×problem, and early partial
   scores can pick it. For #29 and `make eval-matrix`, an **"adaptive ensemble"** — start
   N candidate configs, prune the laggards at an early checkpoint, reallocate budget to
   survivors (Successive-Halving/ASHA shape; their `12→5→2→1`) — gets a real gain at *zero*
   extra budget, and it's the natural next step past "pick one global winner." 📋 — pairs
   with the AIDE² population-search adopt already tracked. **Guardrail:** honour the
   fixed-budget constraint, or a naïve ensemble just multiplies eval cost before pruning.

## Bottom line

A rigorous, refreshingly negative result — *your fancy harness probably isn't beating the
simple baseline, you just didn't run enough trials to see the variance.* The evolutionary
machinery is un-portable and domain-specific, but the **statistical-rigor protocol** and
the **online early-signal allocation** are exactly the fitness-and-measurement mechanisms
the self-regeneration cluster still needs, they cost nothing to run keyless, and they
reinforce the flag-eval / public-private-split / eval-rigor direction Prax is already
committed to. **Document + adopt both patterns into the eval engine; don't vendor the
search harnesses.** Complements [aide2](aide2-recursive-self-improvement.md) (the RSI
fitness gate), [flag-eval-campaign](flag-eval-campaign-2026-07-08.md) (the A/B lane this
would make statistically honest), and [edge-bench](edge-bench-learning-curves.md)
("verifiable beats judgeable"). Caveat: the "no universal harness" claim is proven on 3
math tasks / 4 models — strong for discovery search, a *hypothesis* for general agent
tasks.
