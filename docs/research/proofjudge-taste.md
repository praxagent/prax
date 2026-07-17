# ProofJudge & "taste" — correctness isn't enough; the missing measurable axis

**Source.** Shane Caldwell, [*ProofJudge and Taste*](https://hackbot.dad/writing/proofjudge-and-taste/)
(hackbot.dad; companion to his PentestJudge). Research assessment, house pattern.

**Verdict: document + adopt the *pattern* (not the Mathlib dataset).** ProofJudge
names and *measures* the axis Prax's verification lane is missing — **taste
(quality/utility) as distinct from correctness** — and its method (pairwise,
human-preference-aligned, context-aware judging) is directly adoptable for
self-regeneration (#29), judge design, and the world-model loop. It is the exact
complement to [Lanyon](lanyon-formal-verification.md): correctness is the floor,
**taste is what makes an output actually wanted.**

## What ProofJudge is

An eval that tests whether an LLM can judge the **quality** of Lean4 proofs the way
Mathlib's human experts do. Method:

- **Pairwise, preference-based:** show the judge a **rejected initial PR vs. its
  improved merged version** (100 Mathlib PRs, 123 pairs); success = rating the
  merged one higher. **Ties and inversions are failures.**
- **Context-aware:** the judge gets **tools to explore the surrounding code**, so it
  evaluates *fit within the library*, not isolated correctness.
- **Results:** GPT-5.4 **65.9%** aligned, Kimi K2.5 59.3% (much cheaper). Frontier
  failures are mostly **ties (27.6%)** not inversions → the limit is **rubric
  design**, not raw model ability.

## The load-bearing insight

> **"The tests all pass, nobody wants the PR."**

Mathlib reviewers systematically **rejected correct AI proofs** — "unnecessarily
long, awkward, with no re-usable pieces." Correctness is necessary and *not
sufficient*. And crucially: **without a measurable taste signal, RL / self-
improvement can't progress beyond basic correctness** — you can only optimize what
you can score.

## Why this is the complement to Lanyon (and completes the picture)

| | Correctness | Taste / quality |
|---|---|---|
| Source | [Lanyon](lanyon-formal-verification.md), `lean_check`, deterministic verify | **ProofJudge** |
| Nature | verifiable (symbolic/exact) | judgeable (preference-aligned) |
| Failure | *misformalization* — proves the wrong thing | *"tests pass, nobody wants it"* |
| For Prax | the un-gameable floor | the signal that makes output **valued** |

Neither alone is enough; both are **floors, not guarantees**. Together they're the
honest model of "a good output."

## Where it lands in Prax (the adoptable patterns)

1. **Self-regeneration (#29): add a *taste* signal to the accept-gate.** #29 selects
   changes on a deterministic verifier (correctness) + the [aide2](aide2-recursive-self-improvement.md)
   complexity/dead-code gate. ProofJudge supplies the missing **measurable quality
   axis** — a pairwise "is the new version *better*, not just passing?" preference
   judge. This is exactly what stops the loop from evolving correct-but-bloated code
   (aide2 literally found dead code + a broken-but-passing layer). **Correctness +
   simplicity + taste**, all measured.

2. **Judge/auditor design — prefer pairwise preference over absolute scoring for
   subjective quality.** ProofJudge's rejected-vs-accepted pairwise signal is more
   robust than an absolute 1-7 (it's the RLHF insight). Prax's golden judge
   (`score_golden`) and supervising auditor ([diffuse-ai-control](diffuse-ai-control-judge-robustness.md))
   could use pairwise comparison where "how good" is fuzzy. And **give judges
   context/tools to evaluate *fit*, not isolated correctness.**

3. **"Ties ⇒ fix the rubric, not the model."** A practical judge-prompt heuristic:
   an indecisive auditor signals an underspecified rubric. Tighten the criteria
   before reaching for a bigger model.

4. **Timely for the world-model loop + the ARC writeup.** A world-model solution can
   be *correct but bad* (the diagnosis we're running distinguishes real-miss from
   artifact — taste is the next axis up). And the ARC-2/Paper-Track grand prizes are
   literally scored on taste-like criteria (**universality, theory, novelty,
   elegance**) — building for measurable taste *is* building for the writeup.

5. **If the Lean lane lands:** a ProofJudge-style taste gate is the natural quality
   layer atop `lean_check` (see the parked Lean eval adapter in the adopt-tracker).

## Honest caveats

- **AI taste is ~66% aligned with experts — it is *hard*, not solved.** Use a
  taste-judge as a **signal, not a hard gate**, and keep a human in the loop for the
  highest-stakes quality calls. Same "floor, not guarantee" stance as everywhere
  else in Prax's judgment lane.
- **Taste is preference, and preference has bias** (multiple reviewers, community
  norms). A taste signal encodes *whose* taste — worth stating explicitly wherever
  it gates a decision.
- Small dataset (123 pairs) — directional evidence, not settled.

## Takeaways

1. **Correctness ≠ value.** "Tests pass, nobody wants it." Taste is a distinct axis
   and must be *measured* for self-improvement to progress.
2. **Pairwise, human-preference-aligned, context-aware judging** beats absolute
   scoring for subjective quality — adopt for #29's accept-gate and the auditor.
3. **Complements Lanyon:** correctness (verifiable floor) + taste (judgeable value);
   both floors, not guarantees.
4. **Ties ⇒ rubric problem.** Fix the criteria before the model.
5. **Don't over-trust the taste-judge** (~66%): signal, not gate; name whose taste.
