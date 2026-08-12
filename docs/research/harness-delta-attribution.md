# Harness-Delta Attribution — how much of a harness "improvement" is real

**Source:** [What Evolves When We Talk About Harness Evolution?](https://wenwen-d.github.io/blog/harness-delta-attribution/)
— Wenxuan Ding, Qin Lu, Changlong Yu, Sha Li, Shuowei Jin, Xin Liu, Greg
Durrett (Amazon & NYU), 6 August 2026.

**Verdict: document + ADOPT the decomposition as the accept-gate for
[#29 self-regeneration](../IDEAS_BACKLOG.md).** This is the strongest empirical
evidence we have seen for a rule this project already asserts — and it is a
direct, immediate caution on the
[skills adopt](amortized-reasoning-skills.md) made three days ago.

## The finding

Harness evolution — automated optimization of prompts, tools and control flow
around a *fixed* LLM executor — is exactly what #29 does. The authors ask
whether the resulting benchmark gains are real, because *"a higher aggregate
score does not reveal what changed in the system"*.

**Harness-Delta Attribution (HDA)** decomposes an observed gain into three
mechanisms with different generalization properties:

1. **Overfitting** — dataset-specific patterns that do not transfer.
2. **Test-time scaling** — gains from more inference compute (retries,
   sampling), not from a better harness.
3. **Generalizable improvement** — what actually transfers.

The method is the useful part: compare the baseline harness **B** against a
**compute-matched** variant (`Bcc`) and an **overfitting-neutralized** variant
(`Eneutral`) of the evolved harness. Without the compute-matched control, an
evolved harness that simply retries more looks better for reasons that have
nothing to do with the harness.

## The numbers, which are brutal

Across LiveMath, CREATE, ALFWorld and SWE-bench Verified, with Claude Opus as
proposer:

| benchmark | attribution |
|---|---|
| **ALFWorld** | **98% of gains = overfitting** |
| **LiveMath** | **79% overfitting** — a recurring answer pattern appearing *in 21 of 35 training questions* |
| **CREATE** | **73% from test-time scaling** |
| SWE-bench Verified (Qwen3-4B) | 62% residual improvement, 15% overfitting, 23% test-time scaling |

And the line that matters most for an accept-gate:

> **"in 4 of the 16 settings, the train-selected harness even performs worse
> than the baseline on the held-out set."**

Selecting a harness on training performance does not merely fail to help — a
quarter of the time it **actively harms**. Validation-based selection reduced
but did not eliminate the problem; residual gains stayed well below training
gains (5.9%, 11.7%, 9.3%, 9.3 CU).

Their own caveat, which strengthens rather than weakens the result: the
overfitting estimate is *"necessarily a lower bound, since some may appear as
implicit or entangled"*, and they note ALFWorld's apparent generalization may
itself reflect data leakage.

## What this does to us

**It quantifies the prime directive.** `CLAUDE.md` says never spike a
benchmark, and argues it as a safety property. This paper says that when you
automate harness improvement *without controls*, spiking is not an occasional
temptation — it is **most of the measured gain**, up to 98%.

**It lands on the skills adopt immediately.** Compiling a skill from
trajectories ([Reason Wide, Not Deep](amortized-reasoning-skills.md)) **is**
harness evolution: an automated artifact injected into the prompt, selected
because it improves a score. I flagged it three days ago as "a spike
generator"; this paper turns that from a worry into a measured base rate. The
constraints recorded there — production corpus only, held-out validation,
human-readable — are now the *minimum*, and even they are shown to be
insufficient on their own.

**It exposes a control our campaigns lack.** Prax's flag campaigns compare arms
that spend different amounts of compute. The 2026-08-10 model campaign is the
clean example: luna-pro averaged ~230k tokens per case against glm-5.2's
63–107k. I reported the token axis honestly, but I did **not** compute-match —
so any pass-rate difference could partly be test-time scaling wearing a model
comparison's clothes. That is a real gap in how we run comparisons, not a
hypothetical.

## Adopt

**1. HDA as #29's accept-gate.** A proposed harness patch must not be accepted
on a score delta. It must be decomposed:

- score it on **held-out** cases the proposer never saw
  ([prax-eval-battery](https://github.com/praxagent/prax-eval-battery) exists
  for exactly this),
- against a **compute-matched** baseline, so extra retries or longer outputs do
  not read as capability,
- and reject when the residual generalizable component is not clearly positive.

The "4 of 16 worse than baseline" result is the argument for the gate being
*rejection-biased*: with automated proposal, no-change is frequently better
than the change.

**2. Compute-matching in flag and model campaigns.** At minimum, report the
token delta alongside every pass-rate delta and refuse to call a pass-rate
difference a capability difference when the compute differs materially. The
honest accounting shipped on 2026-08-08 (errors counted, tokens included)
already gives us the numbers; what is missing is the *rule* that they must be
read together.

## Honest limits

This is a blog post, not a peer-reviewed paper — though all major claims come
from the authors' own controlled experiments with held-out evaluation, which is
more than most. Figures are theirs and unverified here. The decomposition also
needs a proposer strong enough to evolve a harness at all (they used Claude
Opus), so the base rates may not transfer to a cheaper proposer.

## Adopt-tracker rows

| Item | Status |
|---|---|
| **HDA decomposition as #29's accept-gate** — held-out + compute-matched, reject unless the generalizable residual is clearly positive | 📋 queued — the strongest specification #29's gate has had |
| **Compute-matched comparison in flag/model campaigns** — never read a pass-rate delta without the token delta beside it | 📋 queued; the 2026-08-10 model campaign is the worked example of the gap |
| Tightened preconditions on the [skills adopt](amortized-reasoning-skills.md) — measured base rate for overfitting is up to 98% | 📋 **binding**, upgrades the existing warning |
