# HarnessCompass — Guiding Automatic Harness Evolution toward Generalizable Harnesses (arXiv 2608.01918)

**Zhang, Zhou, Song, Chen, Tian, Yang, Ma, Li, Feng, Li, Jin, Xu (12 authors),
2026-08-03, cs.LG/cs.CL.** Arrived two days after
[Harness-R1](harness-r1.md) and attacks the failure mode that work leaves open.

Its diagnosis of existing automatic harness evolution is three-part: methods
**overfit to the evolution tasks**, rely **exclusively on trajectory-derived
signals**, and optimize harness components **jointly**, causing cross-component
interference. Its three answers:

1. **Constrained evolution** — global constraints restricting modifications to
   **task-agnostic** harness changes that generalize beyond the evolution
   tasks.
2. **Proactive feedback** — augment trajectory evidence with **first-person
   feedback from the agent about harness usage**.
3. **Component-wise optimization** — decouple components, then consolidate,
   "reducing cross-component interference while preserving component synergy."

Reported: SWE-bench Verified with GPT-5.4, **Pass@1 54% → 66% in 5 evolution
iterations**, beating AHE on effectiveness and efficiency, with the evolved
harness transferring to held-out tasks and other models.

**Verdict: document + adopt TWO — constrained evolution is our anti-spike rule
arrived at independently, from the harness-evolution side, and it makes the
rule a *precondition of the loop* rather than a review check. Component-wise
optimization is the second. This is the fourth independent source in a week
telling us the accept-gate is under-specified.**

---

## The headline: they rediscovered "never spike," and mechanised it

Prax's oldest rule says a fix must abstract the problem class, and that a
reader who knows the benchmark must not be able to tell which task it targeted.
HarnessCompass's constrained evolution says: restrict modifications to
**task-agnostic** changes that generalize beyond the evolution tasks.

Same rule. Different starting point — they got there from *overfitting hurts
your numbers*, we got there from *reward hacking generalises to misalignment*.
Together with [Bennett](weakest-not-shortest.md)'s epistemic argument (a spike
is a strong hypothesis and therefore cannot generalise), the anti-spike rule
now has **three independent justifications**: safety, epistemics, and measured
performance. Rules with converging independent support are the ones to build
machinery around.

And the machinery is the contribution. We enforce anti-spike by *review* —
`/code-review`, the prime directive, my own judgement. They enforce it as a
**global constraint on what the evolution loop may propose at all**. That is
the same policy→property move as [Weng](weng-harness-engineering.md)'s
read-only scorer and [Harness-R1](harness-r1.md)'s frozen target, applied to
the *content* of a patch rather than its *surface*. For #29 P1 the three
compose into a clean specification:

> The scorer is read-only (Weng). The agent is frozen (Harness-R1). The patch
> must be task-agnostic (HarnessCompass). Anything else the loop proposes is
> rejected before it is ever scored.

That is a materially better P1 design than we had a week ago, and none of it
requires training anything.

## The second adopt: optimize components separately

"Optimize harness components jointly, causing interference across components"
is a precise description of a risk in our own flag surface. Prax has ~30
reliability flags that interact — middleware, tier escalation, compaction,
retrieval, prompt selectivity — and the 2026-07-08 campaign A/B'd them
**one at a time against a fixed baseline**, which is the right instinct but
does not detect interference between two flags that are individually neutral
and jointly harmful.

Their decouple-then-consolidate sequencing is the discipline: improve
components independently, then verify the *consolidated* harness rather than
assuming the improvements add. For us that is a concrete addition to the flag
campaign — after picking per-flag winners, run the combined configuration and
confirm it beats each part. Cheap, and it is the step we skipped.

## The third idea, noted but not adopted

**Proactive first-person feedback** — asking the agent about harness usage
rather than only mining its trajectories. Appealing, and Prax has the surface
(`review_my_traces`, `trace_search`). But it runs straight into the finding we
measured ourselves: **85% of Terminal-Bench failures declared success**. An
agent that cannot tell whether it succeeded is a doubtful witness on why the
harness failed it. [CRUX](crux-shadow-evals.md) saw the same thing — self-review
that surfaced the right issues and never bound behaviour.

Not rejected, but sequenced: it becomes trustworthy input *after* the
calibration work has numbers on it. Ask an agent whose self-reports are scored,
not one whose self-reports are unexamined.

## Honest limits

Abstract-level read; the paper's own limitations section was not in the fetched
content, which usually means it exists and I have not seen it — not that there
is none. No code or venue named. **54% → 66% is a single number on a single
benchmark with a single model** (SWE-bench Verified, GPT-5.4), and SWE-bench
Verified is precisely the kind of widely-adopted benchmark the
[saturation study](benchmark-saturation.md) warns is losing discriminative
power — though at 54% baseline there is genuine headroom, which is the better
sign. The transfer claim ("transfers to held-out tasks and other models") is
the load-bearing one for us and is stated rather than quantified in the
abstract. Twelve authors, no affiliations given in the listing.

Compared against [Harness-R1](harness-r1.md): same week, same lane, and their
claims are complementary rather than competing — R1 supplies the reward
mechanism and the frozen-target invariant, Compass supplies the constraint on
what may be proposed. Neither is replicated by us.

## Related

- [harness-r1.md](harness-r1.md) — the counterpart two days earlier; together
  they specify #29 P1's invariants.
- [weng-harness-engineering.md](weng-harness-engineering.md) — the ladder and
  the read-only scorer; the third invariant.
- [weakest-not-shortest.md](weakest-not-shortest.md) — the epistemic argument
  for the same anti-spike rule.
- [flag-eval-campaign-2026-07-08.md](flag-eval-campaign-2026-07-08.md) — the
  one-flag-at-a-time campaign that component-wise consolidation would extend.
- [crux-shadow-evals.md](crux-shadow-evals.md) — why first-person harness
  feedback waits on calibration.
