# SDFT — self-distillation for continual learning, and why it matters that we *can't* run it

**Source:** [arXiv 2601.19897](https://arxiv.org/abs/2601.19897) —
*Self-Distillation Enables Continual Learning*. Idan Shenfeld, Mehul Damani,
Jonas Hübotter, Pulkit Agrawal.

**Verdict: document-don't-adopt (weights-level fine-tuning — the GPU wall
again) — but read it next to
[Reason Wide, Not Deep](amortized-reasoning-skills.md), because the two are the
same insight at different rungs of the ladder, and the pair is more useful than
either alone.**

## What it says

Continual learning — acquiring new skills without degrading old ones — is
blocked by a practical gap. On-policy RL reduces forgetting but needs an
explicit reward function, which usually doesn't exist. The alternative,
learning from expert demonstrations, is dominated by SFT, which is inherently
**off-policy** and forgets.

**SDFT (Self-Distillation Fine-Tuning)** closes it with an elegant move: put
the demonstration *in context*, and use the **demonstration-conditioned model
as its own teacher**. In-context learning yields an on-policy training signal
for free — the model trains on its own outputs, produced while looking at the
demonstration. Claimed: consistently beats SFT on new-task accuracy while
substantially reducing catastrophic forgetting, and in sequential experiments a
single model accumulates multiple skills **without performance regression**.

## Why we decline it, briefly

It is a fine-tuning method. Neither box has a GPU, and the
[Tinker assessment](tinker-training-api.md) already established a *structural*
ceiling beyond hardware: Prax's training and serving paths are separate
processes handing off a directory, so no on-policy method is reachable
regardless. SDFT is on-policy by construction. This is the wall again, and by
now the wall is the finding rather than the news.

**Honest limit on this note:** I have the abstract only, and it carries **no
extractable numbers** — "consistently outperforms SFT" with no absolute
figures in what I could read. Any claim about effect size here would be
invented, so there is none.

## The connection worth having

Read alongside [Reason Wide, Not Deep](amortized-reasoning-skills.md), assessed
the same day, the pair says something neither says alone:

| | mechanism | where the knowledge lands | cost per episode |
|---|---|---|---|
| **Reason Wide** | compile a skill from trajectories, inject into the prompt | **context** | compact skill tokens |
| **SDFT** | condition on demonstrations, distil own outputs | **weights** | zero |

They share the core move — *let the model see the demonstration, then keep what
it produces* — and differ only in where the result is consolidated. SDFT is the
weights version of what we just decided to adopt at the prompt layer.

That is quietly reassuring about the choice. Stopping at the prompt captures
the same mechanism minus the consolidation; the price is paying context tokens
every episode instead of amortising into weights — and *Reason Wide* measures
that price as still a 2.7–6× **saving**, because a compact skill is far cheaper
than re-derived reasoning. So the rung we can actually reach keeps most of the
benefit. That is a better argument for the scaffolding-first thesis than any
assertion of it, and it comes from the weights side.

## The one idea to bank: forgetting has a prompt-layer analogue

SDFT's headline worry is **catastrophic forgetting** — new skills degrading old
capabilities — and its sequential experiments exist to show accumulation
without regression.

The prompt-layer version of that question is real for Prax and currently
unanswered: **as compiled skills accumulate in the system prompt, do earlier
ones stop working?** The system prompt is already ~67KB, `PROMPT_SELECTIVITY`
exists precisely because of that, and the
[flag campaign](flag-eval-campaign-2026-07-08.md) made selectivity a
recommended default on measured evidence. Adding skills makes the pressure
worse, not better.

So if the *Reason Wide* adopt proceeds, the sequential question comes with it:
compile skill A, verify it holds; compile skill B, **re-verify A**. Prompt-layer
forgetting is cheap to measure — it is just the capability suite run again —
and it would be an easy thing to never think to check.

Note the tension with [MORPHEUS](morpheus-assessment.md), which argued LLMs are
*not* continual learners. These are not straightforwardly reconcilable, and I
am not going to reconcile them from two abstracts; the honest position is that
one paper reports a method that works on its benchmarks and the other reports a
limit on a different setup.

## Adopt-tracker rows

| Item | Status |
|---|---|
| **Prompt-layer forgetting check** — after compiling skill B, re-verify skill A still holds | 📋 rides with the [Reason Wide](amortized-reasoning-skills.md) adopt; cheap (just re-run the suite) and easy to forget |
| SDFT fine-tuning itself | ❌ declined — on-policy weights method; GPU wall *and* the train/serve process split makes it structurally unreachable |
