# When AI Benchmarks Plateau: A Systematic Study of Benchmark Saturation (arXiv 2602.16763)

**Akhtar, Reuel, Soni, … Biderman, Talat, Ghosh, Solaiman (37 authors;
Stanford/EleutherAI/HuggingFace and others). ICML 2026.** Systematic study of
**60 language-model benchmarks** against **14 properties**, with a defined and
computable saturation metric.

**Saturation** = "the loss of reliable discriminative power among
state-of-the-art models" — when top performers score statistically
indistinguishably near the ceiling. Measured as
**S_index = exp(−R_norm²)** where **R_norm = (s₁ − s_k) / SE_Δ**, binned from
very low (<0.01) to very high (≥0.9).

Headline findings: **29 of 60 benchmarks are highly saturated** (S_index ≥ 0.7),
14 very highly; saturation rises with age (**42.9%** under 24 months →
**54.5%** over 60 months); resilience tracks **expert curation** (p = 0.0017,
confounded by age) and **not** whether the test set is private — "**no
statistically meaningful difference in S_index between the two groups**", and
hiding test data "does not prevent saturation once benchmarks are widely
adopted."

**Verdict: document + adopt TWO things — compute S_index over our eval matrix
and retire what no longer discriminates, and stop treating privacy as a
durability strategy. This is the most directly actionable eval paper we have
assessed, and it corrects an assumption of ours.**

---

## The correction: privacy ≠ longevity

Prax has invested in a private held-out battery (`prax-eval-battery`, "private
forever"), and the AIDE² public/private golden split selects on held-out score.
The implicit theory has been that hiding instances protects the signal.

This paper says hiding does not prevent **saturation**. Two things must be kept
apart, and we have been sloppy about it:

- **Contamination** — the benchmark leaks into training data, so scores stop
  measuring capability. **Privacy does protect against this.** Our battery's
  stated purpose is exactly this firewall, and it remains valid.
- **Saturation** — the cases stop *discriminating* because models got good
  enough that everyone scores near the ceiling. **Privacy does nothing here.**
  A pristine, uncontaminated, secret test set whose items are simply easy is
  worthless for ranking, and it fails silently — the number still looks fine.

So the battery is not wasted, but it buys one property and we have been
half-assuming it buys both. What actually correlates with resilience is
**expert curation**. That reframes where eval effort should go: not "hide more"
but "author harder, with real domain expertise and difficulty calibration."
praxbench's dual-axis grading (answer *and* trace) is already a curation-depth
move of exactly this kind; the lesson is to do more of that and expect less
from secrecy.

**Honest counterweight before we over-update:** their private group is
**N = 4** against 56 public. That is a very thin basis for a null result, and
the authors report no effect size — only the absence of a significant
difference. "No evidence that privacy helps" at N=4 is not "evidence that
privacy doesn't help." The expert-curation finding is also **confounded by
age** by their own admission. The direction is worth acting on; the strength
is not.

## The adopt: measure our own matrix

S_index is computable and we have never asked the question it answers. Our
`make eval-matrix` runs 15+ benchmarks, and several are exactly the ones this
literature flags — **HumanEval is named in the paper as rapidly saturated**,
and GSM8K, MMLU-style and TruthfulQA-style suites are of the same vintage.

If a benchmark no longer separates frontier models, it cannot separate *our
configurations either*, which means:

1. it burns budget and wall-clock in every matrix run,
2. it pads the public scorecard with numbers that look like evidence and are
   not, and
3. worst, it can make a real regression invisible — everything scores near the
   ceiling, so a change that hurts shows nothing.

That third point is the reason this matters beyond tidiness: a saturated
benchmark in an accept-gate is a gate that does not gate. Concretely: compute
S_index per benchmark from published frontier leaderboards, publish it
**beside** each score in the matrix, and retire or rotate anything in the high
bins. A score without a discriminative-power figure is the same class of
omission as the sampling-honesty problem TJ caught earlier — the number is
true and the impression is false.

**Caveat on applying it:** their limitation #6 says the uncertainty estimates
are built for "accuracy-like metrics over fixed test sets," and that "Elo
ratings, pass@k, or judge-based evaluations require tailored variance
estimates." That covers our multiturn `pass^k` and every judge-scored golden,
so S_index transfers cleanly only to the deterministic adapters. Do not quote
it for the rest without doing the variance work.

## What it endorses

- **ARC-AGI remains unsaturated** despite prolonged exposure, alongside
  BIG-Bench Hard. That is direct support for the parked
  [ARC-AGI-3](arc-agi-3-schema-harness.md) flagship plan — the target is one of
  the few benchmarks still discriminating.
- **Dynabench resists saturation through dynamic updates.** That is empirical
  backing for the [self-authored task generators](arc-agi-3-schema-harness.md)
  row (re-arc procedural generation): a benchmark you can *regenerate* is
  structurally resistant in a way a fixed set never is. Regeneration beats
  secrecy — the same conclusion as the privacy finding, from the other side.
- Choosing **Terminal-Bench 2.0** looks right by these criteria: recent,
  expert-curated, hidden per-task verifiers, and nowhere near ceiling at
  13.5%.

## Honest limits

The authors are unusually forthcoming (six limitations). Most consequential
here: benchmark selection "may overrepresent widely-adopted benchmarks";
leaderboard snapshots may miss dynamics for sparsely evaluated benchmarks;
frontier-model results are "incomplete, selectively reported, or
inconsistently updated"; properties are annotated as time-invariant when
things like annotation diversity evolve; and benchmarks themselves change via
revised splits. Add the N=4 private group above. The saturation *metric* is
the durable contribution; several of the correlational findings are suggestive
at best.

## Related

- [ilands-grounding-gap.md](ilands-grounding-gap.md) — "a held-out set is
  still authored"; this adds that hidden-ness does not buy longevity either.
- [aide2-recursive-self-improvement.md](aide2-recursive-self-improvement.md) —
  the public/private split whose theory this partially corrects.
- [eval-rigor-review.md](eval-rigor-review-2026-07.md) + the scorecard-honesty thread —
  a score published without its discriminative power is the same omission
  class as a sample published as a suite.
- [arc-agi-3-schema-harness.md](arc-agi-3-schema-harness.md) — the flagship
  this endorses twice (ARC-AGI unsaturated; regeneration resists).
