# Executable world-models — the research foundation for Prax's ARC / world-model build

**Provenance.** Curated from a GPT-5.6 Sol Pro deep-research review (2026-07-16),
verified against primary sources where possible. Companion to the
[ARC-AGI-3 assessment](arc-agi-3-schema-harness.md); this is the *field foundation*
for the "executable world-models" capability that ARC-AGI-2/3 are benchmarks of.
Numbers below are labelled **verified** (by the benchmark operator) vs.
**self-reported** — the distinction is load-bearing.

---

## 1. The definition, sharpened

An **executable world-model** is a *runnable artifact* — usually code / a program
sketch / a DSL expression — that an agent **induces from observations, executes to
simulate consequences, checks against new evidence, and then plans or answers
with.** The distinction from neural/latent world-models: the learned model is
**externally inspectable and directly executable**, not implicit in weights.
Canonical modern systems: **WorldCoder** (synthesizes Python transition+reward
models from interaction), **Code World Models / GIF-MCTS** (induce full Python
simulators, plan inside them).

The frontier moved 2024→2026 from *"can an LLM write a plausible simulator?"* to
*"can an agent **induce, verify, repair, and exploit** a simulator under benchmark
pressure?"* — which is exactly the loop Prax's sandbox+codegen is built for.

## 2. The paradigm map — when each wins

- **Programmatic/symbolic** (code world-models) win when the environment has
  **crisp rules, few observations, compositional structure, or hard legality
  constraints** — they're exactly verifiable and searchable by a classical planner.
  They lose when **perception dominates, dynamics are noisy/continuous, or
  search/repair cost explodes**.
- **Neural/latent** (Dreamer/DreamerV3, MuZero) win on **high-dimensional,
  partially-observed, continuous control** — they absorb ambiguity instead of
  compiling it into brittle code. DreamerV3 = one config across 150+ tasks, first
  to mine diamonds in Minecraft from scratch. MuZero = model only what planning
  needs (reward/policy/value) + tree search.
- **The frontier is hybrid:** neural perception/priors, but **planning,
  constraint-satisfaction, and verification moved into explicit executable
  structure**. The sharpest ARC lesson — *Combining Induction and Transduction* —
  shows **inductive program synthesis wins on precise/compositional tasks,
  transduction wins on fuzzy perceptual ones**; the ensemble approaches human level.

**For Prax:** ARC (and most "reverse-engineer an unknown API/system" work) sits in
the **symbolic-favoured** regime — crisp rules, few observations, exact
verification. That's where executable world-models are strongest, and where Prax's
code-execution substrate is a real advantage.

## 3. The canonical loop (this is the build stack)

Every strong system instantiates the same five-module **verifier-centric** loop:

1. **Perception / state abstraction** — extract objects, relations, symmetries
   (many failures are *upstream* of code synthesis — misreading the scene).
2. **Propose executable hypotheses** — generate short programs (not prose rules).
3. **Execution-based verification** — run on the evidence; surface counterexamples.
   Correctness on observed pairs is *machine-verifiable* — no LLM judge.
4. **Local repair after counterexamples** — "fix the model to match this transition
   while preserving prior successes" (counterexample-guided repair; self-debug).
5. **Planner that exploits + disambiguates** — plan *inside* the induced model
   (MCTS for discrete, CEM for continuous), and deliberately choose actions that
   **separate competing hypotheses**, not just locally-novel ones.

WorldCoder states the invariant: planning with an optimistic induced model either
achieves the goal *or* drives the agent to a **counterexample that shrinks the
hypothesis space**. Calling a synthesized Python model is **4–7 orders of magnitude
cheaper** than querying an LLM as the world-model — which is why planning-in-code
beats token-by-token reasoning about consequences.

## 4. The ARC landscape — verified vs. self-reported (the honest gap)

This is the correction to bank, and it *strengthens* the fair-shot thesis.

**ARC-AGI-2** (static; 120 public-eval tasks; semi-private/private for prizes):
- **Verified, offline contest (the Kaggle regime we'd compete in):** ARC Prize 2025
  winners were **NVARC 24.03%** (Qwen3-4B + Tiny-Recursive-Model, synthetic data,
  ~**$0.20/task**), **ARChitects 16.53%** (LLaDA-8B masked-diffusion), **MindsAI
  12.6%**. Independently verified. **The 85% grand-prize bar is far above the
  offline SOTA** — nobody is close.
- **Verified, unrestricted API models (a *different* track, no compute cap):** GPT-5.6
  Sol Max **92.5%**; earlier, Opus 4.5 Thinking **37.6%** ($2.20/task), a Gemini-3-Pro
  refinement (Poetiq) **54%** ($30/task). Not the offline regime.
- **Self-reported:** Imbue's **code-evolution** on the *public* eval — Kimi K2.5
  12.1%→**34.0%** ($2.67/task), Gemini 3.1 Pro **95%** ($8.71/task). Public-set,
  not semi-private-verified.

**ARC-AGI-3** (interactive; 25 public / 55 semi-private / 55 private; RHAE):
- **At release, verified frontier was ~0%:** Opus 4.6 Max **0.50%**, Gemini 3.1 Pro
  **0.40%**, GPT-5.4 High **0.20%**.
- **Best verified as of July 2026:** GPT-5.6 Sol — **13.33%** public demo, **7.78%**
  semi-private; first to win a *single* public game (ft09, 87%).
- **Self-reported research baseline:** *Executable World Models for ARC-AGI-3*
  ([arXiv 2605.05138](https://arxiv.org/abs/2605.05138), released
  `arc-3-agents-baseline1`) — GPT-5.5 high solved **15/25** public games at mean
  **RHAE 58.12%**; GPT-5.4 high **8/25** at 41.29%. Public-set only.

**⚠️ Correction to the earlier "Schema 99%" framing.** The deep review could **not
verify the name "Schema" in the primary text of arXiv 2605.05138**, and that
paper reports **~58% RHAE**, not 99%. The [schema-harness.github.io](https://schema-harness.github.io/)
99% claim (Opus 4.8 + Fable 5) is a **self-reported website number** that is far
above both the closest primary source (58%) *and* ARC Prize's verified frontier
(**7.78% semi-private**). Treat 99% as unverified and likely not reproducible on
the private set. **The real, verified ARC-AGI-3 field is single-digit % — it is
wide open**, and ARC Prize itself warns that public-game harness scores over-fit and
**won't be used for official scoring**.

## 5. The offline-regime playbook (answer to "small model, P100, 12h, $50")

**The 120 ARC-AGI-2 tasks / 12h = ~6 min/task** → adaptive budget allocation is
mandatory (easy tasks terminate fast so hard tasks get minutes of search). The
evidence-ranked strategy:

1. **Execution-guided program synthesis + evolution — most of the budget.**
   Generate a short `solve(grid)->grid`, run on all demos, feed exact failure
   traces back, repair/evolve. Machine-verifiable, partial-credit-guidable. This is
   **Prax's sandbox+codegen wheelhouse.** For free-form Python, **evolutionary /
   best-first search beats token-MCTS** (MCTS only pays off over a *structured* DSL/AST).
2. **Selective per-task TTT (QLoRA) — fallback, not primary.** Akyürek's TTT hit
   **53% ARC-1** (8B), **61.9%** ensembled with program synthesis — but needed
   ARC-like pre-finetuning + leave-one-demo-out augmentation + a per-puzzle adapter,
   and ~12h/100 tasks on an A100. Under our budget: apply only to the **hardest
   10–25%**, rank-8–32 LoRA, a few dozen aug examples, on a 4–8B model.
3. **Tiny direct-prediction model (TRM) — orthogonal 2nd attempt.** ~7M params,
   45% ARC-1 / 8% ARC-2; cheap; use as the *second* of the two allowed attempts and
   as a disagreement signal for where to spend more search. Don't let it displace
   the program branch.
4. **Pure sampling / long CoT — only to generate hypotheses.** Direct grid guessing
   has no verifier; spend tokens on several concise *executable* hypotheses instead.

**Selection is lexicographic, not one gameable scalar:** demos solved exactly →
output-shape correctness → object/pixel partial accuracy → leave-one-out
reconstruction → equivariance consistency → program simplicity → novelty vs. kept
hypotheses. Keep **behavioural niches** (copy / geometric / object-extraction /
pattern-completion / path / cellular-rule) rather than one top-pixel candidate.
**Anti-overfit checks before accepting:** color-permute, rotate/reflect when the
rule should be equivariant, reorder demos, leave-one-out, penalize exact-hash /
demo-specific dims / unexplained constants, prefer shorter programs.

### Model shortlist (open, offline-feasible)

- **16 GB (P100/T4) — primary generator: `Soar-qwen-7b`** (Qwen2.5-Coder trained on
  SOAR's ARC program-synthesis corpus; open weights + dataset; SOAR = **52% ARC-1**
  self-reported). Direct branch: **NVARC's Qwen3-4B** stack (the 24% ARC-2 winner —
  best proven offline starting point, open, incl. 103k synthetic + 3.2M augmented
  puzzles + TRM). Second attempt: **TRM**. QLoRA-TTT on a 4–8B model for the hardest
  quarter. *A high-throughput 7B often beats a slower 14B at fixed wall-clock — measure
  solved-demos-per-GPU-minute, not pass@1.*
- **48 GB (RTX 6000) — primary: `Qwen3-Coder-30B-A3B-Instruct`** (MoE, 30B total /
  **3.3B active** → far more candidates/hour; agentic-coding-tuned). Alt: Gemma-4-26B-A4B.
  Keep TTT on the small specialized model.

**Recommended build (closest to the best verified low-cost result):**
> **NVARC-derived specialized prediction + SOAR-style executable Python evolution +
> selective per-task QLoRA + TRM as an orthogonal second attempt.**

## 6. Procedural generation, contamination, and the recursive loop

- **`re-arc`** (Hodel) — procedural generators for **all 400 ARC training tasks**;
  sample far more examples from the inferred task distribution to test whether an
  induced program captures the *rule* vs. memorizes the demos. **Adopt directly** as
  the seed for our own task generators (§ ARC assessment) rather than build from
  scratch.
- **Contamination is explicitly benchmarked.** ARC Prize's position: public games
  are for demonstration, harness scores over-fit seen games and collapse on unseen,
  and public/community numbers ≠ verified signal. H-ARC: humans **76.2%** train /
  **64.2%** public-eval (790/800 solved by ≥1 human in 3 tries) — the human bar is
  far above every AI system.
- **The un-gameable loop is real and proven:** **FunSearch** (frozen LLM creativity
  + deterministic evaluator + evolutionary population) and **AlphaEvolve** (same
  pattern, larger edits; found a **rank-48 4×4 complex-matmul** algorithm — first
  improvement in 56 years). This is exactly Prax's [self-regen #29](../IDEAS_BACKLOG.md)
  direction: **deterministic verifier + self-generated tasks = the fitness function**.
  Caveat ARC Prize stresses: "un-gameable" only holds if the evaluator is aligned
  with the intended capability — benchmark *design*, not just determinism, decides
  whether you select for robust world-modeling or clever gaming.

## 7. What this means for Prax

The review **validates the whole direction** and hands us concrete starting points:

- **The 5-module verifier-centric stack (§3) IS the "executable world-models"
  capability** we scoped — and Prax already owns the load-bearing module (sandbox +
  codegen for propose/execute/repair). The gaps are perception/state-abstraction
  helpers and a planner-that-disambiguates.
- **Don't start from a generic model — start from the ARC-tuned open ones**
  (Soar-qwen-7b, NVARC Qwen3-4B, TRM). The step-down ladder lands *here*, not on a
  raw 14B.
- **Program synthesis + evolution > TTT > direct prediction** under the budget —
  which is fortunate, because program-synthesis-in-a-sandbox is Prax's strength and
  TTT/finetune is its weakest lane.
- **`re-arc` + a deterministic verifier closes the recursive loop** (#29) in a
  domain where success is exactly checkable — the safest place to turn the loop on.
- **Honest bar:** the offline verified SOTA is **~24% ARC-2 / ~8% ARC-3**; the
  85%/100% grand prizes are a moonshot **for everyone**. The realistic, still-
  world-class goal is a **credible open-source entry in the 20–30% ARC-2 range** and
  a **broadly-useful executable-world-models capability** — the capability pays off
  regardless of the leaderboard.

## 8. Ranked papers / repos to build on

1. **WorldCoder** — cleanest online executable world-model induction (LLM as
   simulator *builder*, optimism, counterexamples, planning).
2. **Code World Models / GIF-MCTS** ([arXiv](https://arxiv.org/abs/2405.15383)) —
   best generate→validate→fix→search recipe; CWMB benchmark.
3. **Executable World Models for ARC-AGI-3** ([2605.05138](https://arxiv.org/abs/2605.05138),
   `arc-3-agents-baseline1`) — the on-target interactive baseline (58% RHAE public,
   self-reported).
4. **ARC-AGI-3 tech report** ([2603.24621](https://arxiv.org/abs/2603.24621)) — RHAE,
   contamination policy, the design constraints.
5. **SOAR** ([2507.14172](https://arxiv.org/abs/2507.14172)) — self-improving
   evolutionary program synthesis; **open Qwen2.5-Coder 7B/14B models + dataset**.
6. **Imbue code-evolution** ([blog](https://imbue.com/blog/2026-02-27-arc-agi-2-evolution)) —
   evolution amplifies a commodity model on ARC-2 (public-eval, self-reported).
7. **FunSearch / AlphaEvolve** — deterministic-evaluator evolutionary search; the
   RSI template.
8. **Combining Induction and Transduction** — the hybrid lesson (both, they solve
   different task types).
9. **Akyürek TTT** ([2411.07279](https://arxiv.org/abs/2411.07279)) — the reference
   test-time-training recipe (and its cost).
10. **TRM** ([2510.04871](https://arxiv.org/abs/2510.04871)) — 7M-param recursive
    model; cheap orthogonal ensemble member.
11. **NVARC** (ARC Prize 2025 winner, 24.03% ARC-2 private) — best proven offline stack.
12. **`re-arc`** (Hodel) — procedural generators for the 400 training tasks.
13. **DreamerV3 / MuZero** — the neural baselines; know when latent > code.

## Takeaways

1. **Executable world-models are now a distinct, mature agent pattern** — induce
   code → verify → repair with counterexamples → plan through it cheaply. Prax's
   sandbox is the natural substrate.
2. **The verified ARC field is wide open** — ~24% ARC-2 / ~8% ARC-3 offline; the
   self-reported 58–99% numbers don't survive to the private leaderboard. A
   budget-constrained open entry has a genuinely fair shot.
3. **Start from ARC-tuned open models** (Soar-qwen-7b, NVARC Qwen3-4B, TRM), not a
   generic 14B; **program synthesis + evolution > TTT > direct**.
4. **`re-arc` + deterministic verifier = the safe recursive-improvement loop** (#29)
   — a domain where the fitness function can't be gamed by construction.
5. **Neural still wins on perception/continuous control** — keep the capability
   scoped to the symbolic-favoured regime (ARC, unknown APIs, discrete systems), and
   go hybrid (neural perception + executable verification) where perception bites.
