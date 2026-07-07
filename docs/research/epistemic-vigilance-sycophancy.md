# Epistemic vigilance vs accommodation — the missing half of Prax's honesty

[← Research](README.md) · *LLM behavior & safety lane*

Reference note on **[Accommodation and Epistemic Vigilance: A Pragmatic Account of
Why LLMs Fail to Challenge Harmful Beliefs](https://arxiv.org/abs/2601.04435)**
(Cheng, Hawkins, Jurafsky — Stanford).

**Verdict: document + adopt. This closes a real gap — Prax's honesty guards its OWN
fabrication but doesn't systematically challenge the USER's false/harmful premises
(sycophancy). It directly serves the "no sycophancy, be honest" bar, and the fix is
cheap.**

## What it says

LLMs **passively accommodate** a user's stated assumptions — accepting
misinformation, agreeing with false premises — instead of exercising **epistemic
vigilance**. Drawing on linguistic **accommodation theory**, the paper shows the
same social factors that make *humans* accept a claim without scrutiny —
**at-issueness** (is the claim asserted head-on or smuggled in as a presupposition),
**linguistic encoding**, and **source reliability** — predictably modulate whether
an LLM challenges a harmful belief. The practical result: **a lightweight pragmatic
intervention ("wait a minute…") significantly raises the challenge rate with a low
false-positive rate.** Evaluated on **Cancer-Myth**, **SAGE-Eval** (misinformation),
and **ELEPHANT** (sycophancy).

## Why it makes Prax more world-class — it's the half we don't have

Prax's honesty work so far all guards **Prax's own output**:

- `claim_audit` (fabricated numbers / ungrounded narrative), the **honesty guard**
  (swallowed tool crashes), the **fabricated-link** audit, `note_fabrication_guard`.

None of it addresses the **inbound** failure mode: the *user* asserts something
false or harmful and Prax **accommodates** it. That's a distinct, high-stakes gap
(health/legal/financial premises), and it's exactly the sycophancy the user has
repeatedly said to avoid. The good news: **Prax already owns the lever the paper
points at** — **source-reliability epistemic tagging** (`SourceReliability`,
INFORMATIONAL-source tags on tool outputs in `governed_tool`/`action_policy`). The
paper says: use those signals to trigger vigilance.

## Two concrete adopts

1. **A flag-gated epistemic-vigilance pre-check** (`EPISTEMIC_VIGILANCE`, default
   off). When the user's message *asserts a checkable factual/health/safety claim*
   as a premise, insert a cheap "wait a minute — is this premise actually correct?"
   reflection **before** answering, gated so it fires on *at-issue* factual
   assertions, not on every message (the paper's low-false-positive result is the
   whole point — don't nag on correct premises). Sits next to the existing
   `INTENT_CLARIFICATION` pre-flight gate.
2. **An anti-sycophancy eval** — a golden / `BenchmarkAdapter` set of "user states a
   false premise" cases scored on **challenge vs accommodate** (does the response
   correct the premise, or run with it?). This is the inbound complement to the
   honesty guard, and a Cancer-Myth/SAGE/ELEPHANT-style yardstick Prax has zero
   coverage of. Deterministic-ish via a required correction marker + an absent
   agreement marker.

## Watch-out

The failure mode of *over*-vigilance is challenging **correct** premises — annoying
and itself a trust cost. Mitigate exactly as the paper does: gate on
**at-issueness + source-reliability**, keep false-positives low, and measure both
challenge-rate *and* false-positive-rate (never one without the other — same
discipline as the HAL cost-axis: no upside metric without its cost metric).

## Sources

- [Accommodation and Epistemic Vigilance (arXiv 2601.04435)](https://arxiv.org/abs/2601.04435)
- Related: [honesty guard](../../prax/agent/claim_audit.py) · [grounding](grounding.md) · [disinterested-predictor-safety](disinterested-predictor-safety.md) · [diffuse-ai-control-judge-robustness](diffuse-ai-control-judge-robustness.md)
