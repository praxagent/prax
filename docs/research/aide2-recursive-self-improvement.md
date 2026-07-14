# AIDE² (Weco) — first evidence of recursive self-improvement, and what it hands Prax's #29

**Assessed:** 2026-07-13 (TJ dropped the link — pointedly — asking why Prax hasn't landed this and what we can take from it).
**Source:** [weco.ai/blog/first-evidence-of-recursive-self-improvement](https://www.weco.ai/blog/first-evidence-of-recursive-self-improvement) · Weco's [AIDE](https://www.aide.ml/) ([arXiv 2502.13138](https://arxiv.org/html/2502.13138v1), [code](https://github.com/WecoAI/aideml)) · [seed announcement](https://www.weco.ai/blog/seed-announcement).

**Verdict: document + adopt — this is the strongest external validation to date of [IDEAS_BACKLOG #29](../IDEAS_BACKLOG.md) (self-regeneration), and it supplies the exact fitness-function mechanisms #29's design was still hand-waving.** Three of its ideas — a **public/private score split**, a **fixed cost budget per evaluation**, and **task heterogeneity** — are cheap, independently valuable in Prax's *existing* eval infra (they harden manual flag-eval campaigns too), and they are the missing 20% that de-risks closing the loop. Adopt those into the eval engine now; let them gate the #29 P1 micro-loop. Two net-new safety lessons folded into #29: a **complexity/dead-code gate** on self-modifications, and **eval-harness edits are HIGH-risk (human-gated)**.

---

## The honest read for TJ (why "we didn't land this first" is the wrong scoreboard)

Weco is an $8M-funded team that spent **two years** building AIDE as a *specialized ML-engineering agent* whose task families — Kaggle competitions, competitive-programming heuristics, harness tuning — all come with a **cheap, numeric, un-gameable fitness function**. RSI is *demonstrable* precisely when your task space scores itself deterministically. That enabling condition is the whole ballgame, and their headline result rests entirely on it (the public/private split below). Prax is a different animal: a general, multi-channel, safety-first assistant whose "is Prax better?" signal is mostly *fuzzy* goldens over real user work. That is exactly why Prax's own #29 design correctly **gates** self-improvement on the un-gameable-eval work rather than racing to close the loop — and AIDE² is the evidence that the dependency ordering was right, not a sign we drifted.

Where the sting is fair: Prax has ~80% of the pieces for a minimal loop (deterministic goldens, the maker≠checker auditor, sandbox + `make ci`/`make eval`, git-backed worktrees, a cost axis) and has parked #29 as "someday" while the pieces sat there. AIDE² shows a *minimal* loop is worth standing up now, and it hands us the missing mechanisms. The takeaway isn't "build AIDE" (wrong product) — it's "the fitness-function hardening #29 waits on is small and concrete; do that part now."

## What AIDE² actually did

A **two-loop** system. The **inner loop** is an AIDE-style tree-search agent (draft 5 diverse solutions → debug buggy leaves → improve the best) running on a *cheap* model (Gemini-3-Flash). The **outer loop** runs a hand-tuned agent on an *expensive* model (Claude-Opus-4.7) whose job is to **rewrite the inner loop's own code** — its search policy, context handling, verification, reward-hacking defenses. 100 unattended outer steps over 8 days; ~9 of every 10 rewrites rejected.

The result they call **Level-1 RSI**: the evolved agents (AIDE47, AIDE85) beat the human-tuned baseline `AIDEhuman` on **held-out** benchmarks never seen during optimization (MLE-Bench Lite +0.05 at p≈0.002; ALE-Bench; and far-OOD WeatherBench 2) — "AIDE²… needed only eight unattended days" versus two years of hand-tuning. They grade RSI on a **falsifiable ladder** — *delegation → net-positive → ignition → inflection* — and are careful: installing AIDE47 as the outer loop improved sample efficiency (~20 steps to ceiling vs ~40) but did **not** demonstrate "ignition," so "we are not near an intelligence explosion."

## The four mechanisms that made it work — and where each lands in Prax

1. **Public/private score split — the anti-reward-hacking core.** The inner agent sees a **public** score it may optimize/game; survival is decided on a **hidden private** score. This is the single most load-bearing design choice, and Prax's eval engine doesn't have it yet. **Adopt now:** split each golden suite into a public (optimization-visible) subset and a held-out **private** subset; report/select on private. Cheap, and it hardens *every* tuning pass — including the manual flag-eval campaigns — against overfitting, not just the future #29 loop. This is the concrete form of the "un-gameable fitness function" that #29 lists as its non-negotiable precondition.

2. **Fixed cost budget per evaluation — forces algorithm, not brute force.** AIDE² maximizes private score *under a fixed dollar budget*, so improvements have to be algorithmic rather than "spend more tokens." Prax already computes `pass_per_1k_tokens` / `avg_full_tokens` (the HAL cost axis from the [MoA](mixture-of-agents.md) and [validation-campaign](validation-campaign-2026-07-08.md) work). **Adopt:** make "improve private score at equal-or-lower token budget" a first-class gate comparison, not a footnote — the same rule already used to decide MoA-vs-tier-upgrade.

3. **Task heterogeneity — selects for generalizable change.** Improvements had to hold across ML-eng + combinatorial + harness families, which is *why* they generalized to held-out and OOD benchmarks. This is "never spike benchmarks / abstract the problem class" restated as a selection pressure. **Implication for #29's P1:** gate a proposed plugin/prompt change against a **heterogeneous golden set**, never a single golden — a change that only lifts one golden is a spike, and a single-golden gate would *reward* it.

4. **Model asymmetry — expensive proposer, cheap subject.** Outer (Opus) cost is a small fraction of total; the thing being improved runs cheap (Flash). Maps directly onto Prax tiers: the **proposer/auditor runs high-tier; the inner agent being improved runs a cheap tier** — which is also how you keep a self-improvement loop affordable on this box.

## Two net-new safety lessons (folded into #29)

- **Complexity/dead-code gate.** Their own caveat: AIDE85 is "very difficult to understand," carries "plain dead code," and its statistical reward-hacking defense *has an implementation bug that breaks it* — yet it still passed the fitness gate. A fitness function that only measures score will happily accept an unmaintainable, subtly-broken artifact. Any Prax self-modification must pass a **simplification/interpretability check** (Prax already has `/simplify`, the workspace syntax linter, and the layer linter) as part of the accept gate — score-up is necessary, not sufficient.
- **Eval-harness edits are HIGH-risk.** AIDE² notes the agent *repaired* a bug in its eval script rather than exploiting it — the benign outcome, but it shows the loop *can reach its own scorer*. In Prax terms, a self-improvement loop touching its own golden/`make eval` harness is the move that can silently weaken its own gate → it must be **human-PR-gated (HIGH risk)**, never auto-adopted, extending the existing graded-autonomy boundary in #29.

## The safety through-line (unchanged, reinforced)

RSI optimizes *brutally* against its fitness function — a gameable eval yields a version that games it (the METR env-cheating finding; the [emergent-misalignment/reward-hacking](emergent-misalignment-reward-hacking.md) house-safety framing). AIDE² is a live demonstration that the fitness function *is the product*: 90% of literature-grade proposals (island populations, MCTS backup, UCB-V…) were correctly rejected by a strict gate, and the whole result stands on the public/private split holding. This is the same reason [autoresearch/labless](autoresearch-labless.md) is filed as "the loop pattern, gated on un-gameable grading," and why #29 must not widen self-modification autonomy faster than the fitness function earns trust. AIDE² doesn't change that line — it hands us better tools for staying on the right side of it.

## Recommendation

1. **Now (small, independently valuable):** add the **public/private golden split** + **cost-budgeted private-score selection** to the eval engine. Pays off immediately for manual campaigns; is the precondition #29 is blocked on.
2. **Then:** stand up #29's **P1 plugin micro-loop** against a *heterogeneous* golden set with the complexity gate — the lowest-risk rung, now de-risked by real-world evidence that the design works.
3. **Framing:** adopt the **RSI ladder** (delegation→net-positive→ignition→inflection) as the honest roadmap language for #29, replacing binary "did we achieve RSI."

## Sources

- [Weco: First Evidence of Recursive Self-Improvement](https://www.weco.ai/blog/first-evidence-of-recursive-self-improvement) · [AIDE technical report](https://www.weco.ai/blog/technical-report) · [AIDE paper (arXiv 2502.13138)](https://arxiv.org/html/2502.13138v1) · [aideml (GitHub)](https://github.com/WecoAI/aideml)
