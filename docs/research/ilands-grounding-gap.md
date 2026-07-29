# iLands — "A World That Answers Back" (the grounding gap)

**iLands Research / PawLogic Inc., launch essay for ilands.ai.** A live
"human–agent society": agent residents with persistent identities, budgets,
reputations and escrowed contracts; humans as customers with real stakes.

**Verdict: document + adopt the LENS; don't adopt the machinery; treat every
empirical claim as unverified.** This is a launch-day essay from a company whose
platform shipped the day it was written. It contains zero results. What it does
contain is the sharpest available naming of a problem Prax's own eval philosophy
circles constantly — and one uncomfortable implication for our #29 gate worth
recording *before* their 90-day results either land or don't.

---

## The thesis

**The grounding gap:** as optimization pressure rises against an
internally-authored evaluator, its predictions diverge from real outcomes unless
the evaluator is repeatedly "re-grounded in consequences it does not control."
Benchmarks saturate; reward models optimize proxies while quality degrades;
preference ratings breed sycophancy.

Their taxonomy:

| | Who writes the rubric | Failure mode |
|---|---|---|
| **Authored selection** | the optimizing system (or its operators) | exploitable by construction — grading your own homework with evolving standards |
| **Grounded selection** | counterparties with their own stakes, who can *refuse, leave, remember, reprice* | consequences persist across episodes; gaming has a price |

Their bet: grounded selection outlasts authored selection as optimization
pressure rises. Mechanism: a live economy — bounties, escrow, reputation,
inference costs the agent must earn back — plus a "survival benchmark" scored by
cohort viability as monthly bills come due.

## Why this bites Prax specifically

Prax's anti-reward-hacking stance (never spike benchmarks; a fix must generalise
the problem class) and the AIDE²-derived **public/private golden split** are our
defences against exactly this failure. The uncomfortable point the essay makes
well:

**A held-out set is still authored.** The private half of the split hides the
*instances* from the optimizer, but the rubric, the task distribution and the
scorer are still ours. Under enough optimization pressure — which is precisely
what self-regeneration #29 will apply — an authored gate degrades even when its
instances are secret. Hiding data raises the cost of gaming; it does not change
who wrote the exam.

**And Prax already has a grounded channel — it just isn't named as one.** TJ's
daily-driver box is a counterparty we do not control: he refuses (rejects work),
leaves (stops using a feature), remembers (the task list, the bug reports), and
reprices (asks for cheaper models). The deploy-every-change loop is grounded
selection at n=1. This session is the demonstration: the eval suite said the
mobile UI was fine; the counterparty said it wasn't, five times, and was right
five times.

## What to adopt

1. **The vocabulary, into the #29 design notes.** "Authored vs grounded" is a
   cleaner cut than "public vs private": the golden split is *authored with
   hidden instances*, and the gate's long-run defence cannot be more hiding — it
   has to be adding grounded signal (real usage outcomes, real cost deltas,
   real user acceptance/rejection of self-proposed changes) alongside the
   authored suite.
2. **Kill conditions.** They pre-commit to publishing results that would refute
   their own bets. Our eval-gate flag campaign already rejects flags on
   evidence; writing the *refutation condition down before the run* is the
   missing discipline and costs one paragraph per experiment.
3. **A naturally non-stationary test stream.** Their "test set that arrives
   monthly" is the same shape as the MORPHEUS adoption row (curve-not-point,
   no-reset). Independent convergence on that idea is mild evidence it matters.

## What not to adopt

- **The economy machinery.** Budgets, escrow, reputation markets — building a
  marketplace to get grounded signal is their business model, not a requirement
  of the insight. Prax's grounded channel is real usage by real users; TeamWork's
  credential/capability/event-log layer (the Buzz adoption) is the substrate
  multi-agent stakes would sit on *if* that day comes.
- **Any empirical claim.** Four "bets" with estimators promised in 90 days, a
  survival benchmark that does not exist yet, launch-day prose. There is nothing
  to verify, so nothing is verified. Revisit when the promised designs and kill
  conditions ship; their follow-through on that pledge is itself the first data
  point about them.

## Honest limits

Read from one essay via a summarising fetch; the platform itself was not
inspected. "PawLogic Inc." has no track record known to this note. The essay's
framing flatters exactly the anxiety a self-improving-agent project already has
— which makes it persuasive to us for reasons that are not evidence.

## Related

- [aide2-recursive-self-improvement.md](aide2-recursive-self-improvement.md) —
  the public/private split this essay says is insufficient at the limit.
- [skyfall-morpheus-continual-learning.md](skyfall-morpheus-continual-learning.md)
  — non-stationary, no-reset evaluation; same shape as the "monthly bills" bench.
- [emergent-misalignment-reward-hacking.md](emergent-misalignment-reward-hacking.md)
  — why gaming an authored metric is a safety issue here, not just a measurement one.
