# Automating eval design and hillclimbing (Anthropic, Sept 2026)

**Verdict: document + adopt three checks; corroborate the rest.** Most of the
method is already Prax's own discipline, arrived at independently (held-out
selection, anti-spike, audit the checker, judge noise). Three things are new
and cheap, and the first one names a failure our own eval already has:

1. **An eval must score a stronger model higher — check it.** Ours doesn't.
2. **Re-grade every run before trusting it** — make the judge-noise check a
   per-run gate, not a one-off study.
3. **Environmental contamination is a named failure mode** — which raises the
   priority of our known eval-isolation gap.

Source: Lance Martin, ["Automating eval design and hillclimbing with Claude"](https://claude.dev/blog/automating-eval-design-and-hillclimbing/),
claude.dev blog, 2026-09-28. Describes two commands in Claude Code's
`claude-api` skill: one that builds an eval, one that hillclimbs a system
against it. Numbers below are the post's, not ours.

## What the post describes

**Build an eval** with four design properties: tasks mirror production; the
score **rises with stronger models and higher effort**; there is **passable
headroom** (well under 100%); and **low run-to-run variance**. Cases come from
production transcripts first, then bug reports, then hand-written cases, with
synthesis to fill gaps — and "pick hard cases because a human judged them
hard", not by sampling wherever the current model happens to fail. Graders are
programmatic where possible (exact match, schema) or an LLM judge with a
rubric rather than a 1–5 scale, and are **validated before use**: run the
grader twice on identical output, check the infrastructure is reliable, and
read a sample of scored transcripts ("scoring failures are among the most
common ways an evaluation is misconfigured").

**Hillclimb** in rounds: propose one targeted change (prompt, skill, tool
description, model or effort), run the eval, keep or revert. The cases are
split **at random into train and test**; "if the train set scores improve
while the test set scores stay flat, then that is a common overfitting warning
sign", and the change is reverted. Failure transcripts are never pasted into
prompts, and answers are kept structurally out of the model's reach.

**Results reported:** a customer-support system went from Opus 4.8 at high
effort (74.4%, 4.6¢/ticket) to Sonnet 5 at low effort (90.5% on the held-out
test set, ~1¢/ticket — a fifth of the cost). The `claude-api` skill went from
66.1% to 87.9% over 24 rounds, mostly by adding missing documentation sections
and fixing type tables.

**Failure modes named:** ambiguous tasks (fail every run whatever the
replicates), unstable graders, inconsistent effort across runs,
**environmental contamination** (leftover files or git history changing
results), and harness reward-hacking — e.g. an OCR tool that helps specific
eval cases but not production.

## Against Prax

| The post | Prax today |
|---|---|
| Held-out test split; revert if train rises and test stays flat | Have it: public/private golden split (`Golden.visibility`, PR #80, from [AIDE²](aide2-recursive-self-improvement.md)); HDA held-out accept-gate queued ([harness-delta-attribution](harness-delta-attribution.md)) |
| Never paste failure transcripts into prompts | Our **anti-spike rule**, stated as a safety rule ([CLAUDE.md](../../CLAUDE.md)) — theirs is the same rule for the same reason |
| Read scored transcripts before believing the evaluator | "Audit the checker first" — four times the "gap" was our scorer (e.g. [eval-scorer-audit](eval-scorer-audit-2026-08-07.md)) |
| Graders are unstable across identical outputs | Measured: **~30% of judged criteria flip on an identical re-grade**; golden-total noise 0.217 mean, 0.450 worst ([judge-bias-audit](judge-bias-audit-2026-08-20.md)) |
| Hard cases chosen because a human judged them hard | Same as [benchmark-saturation](benchmark-saturation.md): resilience comes from expert curation |
| **Score must rise with stronger models** | **Fails.** The 2026-07-08 [validation campaign](validation-campaign-2026-07-08.md) ran the capability suite at `medium` and `low` tiers: **6/7 and 0.964 in both arms**. An eval that can't tell the tiers apart can't tell whether a change helped |
| Environmental contamination | **Known gap, still open:** eval "isolation" only re-points `workspace_dir`; the sandbox keeps its `/workspace` mount, so sandbox-run eval steps write into the real user workspace, and `source_read` can reach `../prax-evals` ([CLAUDE.md known gaps](../../CLAUDE.md)) |
| Cost hillclimb: downshift the model at held accuracy | Tiers exist per spoke, but tier choices were set by hand, not measured against an eval |

## Adopt

1. **A discrimination check on every suite.** Before a suite is used to judge
   a change, run it at two tiers (and, where it matters, two effort levels).
   If the stronger arm doesn't score clearly higher — beyond the judge noise
   floor — the suite is not fit to gate anything: mark it `non-discriminating`
   and fix the suite first. The July capability suite fails this today, which
   is why its verdicts on the flag campaign were cost-only.
2. **A grader-consistency gate per run.** Re-grade a sample of each run's
   judged cases on identical output; report the flip rate beside the score and
   refuse to report a delta smaller than the measured noise. This turns the
   one-off noise study into a standing property of every result.
3. **Fix eval isolation before any automated hillclimb.** A loop that proposes
   and keeps changes on evidence cannot be trusted while leftover files and a
   shared `/workspace` can move scores. Priority raised from "known gap" to
   "precondition for #29".

A fourth, lower priority: **cost at held accuracy is a good first objective for
#29** — well scoped, attributable, cheap to revert, and useful even when a
suite is saturated. The post's support case (a fifth of the cost at higher
held-out accuracy) is the shape of it; Prax's per-spoke tiers are the knob.

## Don't adopt, and nuance

- **The tooling itself.** The commands target systems built on the Claude API;
  Prax routes across providers and has its own eval engine. The method
  transfers, the command does not need to.
- **A random split is not enough on its own.** It catches overfitting to
  particular cases, not to the case *distribution* the authors chose
  ([iLands](ilands-grounding-gap.md)): train and test are drawn from the same
  authored pool. Prax keeps both — a random split for the per-round revert
  rule, and the authored private battery plus grounded live-use signal for
  whether a change generalizes.
- **Vendor post, vendor benchmarks.** The 66.1 → 87.9% skill result is on the
  author's own eval of their own skill, and the post does not say that number
  is held-out (the support result explicitly is). Treat it as an existence
  proof, not a benchmark.

## Adopt tracker

| Item | Status |
|---|---|
| Discrimination check: a suite must score a stronger tier clearly higher, beyond judge noise, before it gates anything | **queued** — capability suite fails it today |
| Grader-consistency gate: re-grade a sample every run, report the flip rate, refuse deltas under the noise | **queued** |
| Eval isolation (sandbox `/workspace`, source tools, leftover files) as a precondition for #29 | **raised priority** — existing known gap |
| Cost-at-held-accuracy as #29's first objective | **queued**, low priority |
