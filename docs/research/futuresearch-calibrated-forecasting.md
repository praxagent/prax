# FutureSearch — calibrated forecasting as a grounded signal

**Dan Schwarz (@dschwarz26), FutureSearch launch announcement, 2026-08-03.**
AI forecasting company (founded Aug 2023) exiting public beta. Claims: **#1 of
194** in "the most competitive AI forecasting tournament"; scores above the #2
and #3 human forecasters in mixed human-bot tournaments; beating the crowd on
Kalshi with public forecasts and trades; >10k high-effort forecasts run in
beta. Now supports **decision forecasts** ("if I do X, will I achieve this
outcome?"). The part they say they are proudest of: forecasts draw on a
**persistent latent world model** that they claim improves accuracy the more
you forecast in a domain.

**Verdict: document-don't-adopt the product; adopt ONE idea that we needed
this week — calibration as a first-class, grounded signal on Prax's own
completion claims.** A launch tweet is marketing, not evidence. But the shape
of what they measure answers a problem we just measured in our own harness.

---

## Why this landed at the right moment

The full Terminal-Bench sweep produced one dominant failure signature: of 65
scored failures, **55 (85%) called `task_done` claiming success** and were
overruled by the hidden verifier. That is not a knowledge failure, it is a
**calibration failure** — the agent had no working model of whether it had
actually met the bar ([CRUX](crux-shadow-evals.md) failure-mode 1, measured at
scale in our own code).

Forecasting is the discipline that takes calibration seriously, because it is
the only field where being confidently wrong is scored rather than forgiven.
The transferable move is small and concrete: **make the agent state a
probability, then score that probability against what actually happened.**

Shipped in `prax/eval/tb_agent.py` the same day:

- `task_done` now takes a **verification command and runs it**, returning the
  output before the claim is accepted. An assertion of success is not evidence
  of success.
- It also takes a **`confidence` in [0,1]** — the agent's probability that an
  independent grader would call the task complete — recorded into the trial
  metadata alongside the verifier's actual verdict.

That second field is the FutureSearch idea in miniature. It gives a
**grounded** score in the [iLands](ilands-grounding-gap.md) sense: resolution
comes from the hidden verifier, not from a rubric we wrote, so it cannot be
gamed by writing a friendlier rubric. Over a sweep it yields a Brier score on
Prax's own self-assessment — the first number we would have that says whether
Prax *knows* when it has succeeded, as distinct from whether it succeeds.

## Two ideas banked, not built

**Decision forecasts** ("if I do X, will I achieve this outcome?") are the
natural front half of an agent's planning step, and the natural companion to
the [failure-provenance](arts-agentic-tree-search.md) row: forecast before
committing to an approach, then compare to outcome, and the gap is the signal
for whether to replan or persist. Distinct from the world-model row already
tracked — that one induces *mechanics*, this one estimates *outcomes*.

**A persistent world model that improves with use** is the same bet as the
memory-consolidation weakest cell ([survey](self-improving-agents-survey.md),
[NOOA](nooa-object-oriented-agents.md)'s ACT-R row). Their claim that accuracy
improves with domain-specific use is exactly what a consolidation loop should
deliver, and exactly what we have never measured. No evidence offered here, so
it stays a hypothesis with a good pedigree.

## Honest limits

This is a **launch tweet**, and every number in it is self-reported: the
tournament is unnamed, "approximately superhuman" is a marketing sentence, the
Kalshi claim is checkable in principle but not checked here, and the
world-model accuracy claim ("we've shown it improves accuracy") is asserted
with no link to a result. None of that matters for the adopt, which rests on a
problem measured in *our* data and a mechanism that is sound independent of
whether FutureSearch's rankings hold up. Forecasting a market is also a
genuinely different task from an agent forecasting its own work — the analogy
is a design borrowing, not a transfer of their results.

Also worth flagging against ourselves: adding a confidence field is cheap; the
hard part is that a miscalibrated model will happily emit miscalibrated
numbers. The value only arrives once we *score* them — an unscored confidence
field is decoration, and would be exactly the kind of theatre this project is
supposed to avoid.

## Related

- [crux-shadow-evals.md](crux-shadow-evals.md) — failure-mode 1 is the problem
  this addresses; our sweep measured it at 85%.
- [ilands-grounding-gap.md](ilands-grounding-gap.md) — why verifier-resolved
  confidence is a grounded signal rather than an authored one.
- [arts-agentic-tree-search.md](arts-agentic-tree-search.md) — decision
  forecasts as the front half of failure-provenance diagnosis.
- [self-improving-agents-survey.md](self-improving-agents-survey.md) —
  persistent-model-improves-with-use is the consolidation cell.
