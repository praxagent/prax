# WikiSkill — the wiki is the memory, the skill is the artifact, and the split is the finding

**Source:** [arXiv 2608.27454](https://arxiv.org/abs/2608.27454) — *WikiSkill:
Compiling Agent Experience into Persistent Knowledge for Skill Evolution*. Tang,
Rashtchian, Ferng, Tomkins, Juan, Vu — **Google Research** + Virginia Tech,
27 Aug 2026, CC BY 4.0, 28pp.

**Verdict: document + adopt three things — the layer separation, one
counter-intuitive ablation, and a correction to how we've been reasoning about
#60. Decline the framework.** This is the strongest skills paper we have read
(**4th sighting**, 2nd with numbers), and unusually it hands us a mechanism whose
open problem we assessed a week ago.

## What it does

Four roles over three explicitly separated layers — **raw execution traces**,
a **persistent wiki**, and **executable skills**:

- **Inference Agent** — runs the task with the active skill set injected into its
  system prompt.
- **Wiki Maintainer** — samples successful *and* failing traces, does root-cause
  analysis, and writes `wiki/patterns/` pages via **incremental patch-based
  edits**, updating an `index.md` catalog and appending to an evolution log.
- **Skill Proposer** — reads the wiki *and* the traces, proposes skill updates.
- **Gating + rollback** — a proposal is kept only if it improves validation.
  **Skills roll back; the wiki does not.** Knowledge survives a rejected skill.

Five benchmarks (LiveMathematicianBench, SealQA, SpreadsheetBench, OfficeQA,
ALFWorld), five models (Qwen-3.5-4B/9B, Qwen-3.6-27B, Gemma-4-31B,
Gemini-3.5-Flash), against three skill-evolution baselines (Trace2Skill, EvoSkill,
SkillOpt) plus a no-skill control.

## The ablation is the paper

Everything else is a leaderboard; **Table 3 is the evidence.** With the Inference
Agent's wiki access held off, giving the *Skill Proposer* the persistent wiki
moves the average from **48.7% → 63.7% (+15.0)**, with LiveMath 51.3 → 72.6 and
SpreadsheetBench 49.9 → 76.6.

That is the load-bearing claim, and it is the one to believe — the standing habit
from [PRO-LONG](prolong-programmatic-memory.md) ("believe the +18pp ablation, not
the 97.4% best@2"). Without knowledge accumulated *across iterations*, the
proposer cannot resolve intricate failure modes; it re-derives the same lessons
every round.

**The genuinely surprising result is the other direction.** Giving the *Inference
Agent* wiki access during training rollouts makes things **worse** — 63.7% → 60.9%,
with LiveMath dropping 72.6 → 64.8. Their explanation: when the agent can get
task-solving knowledge directly from the wiki, it stops needing the skill, and the
resulting trajectories become **less informative for skill development**.

Stated generally, that is a real principle and it is not obvious:

> **The artifact you are optimising must be the only channel through which the
> knowledge flows. Give the agent a side channel and you contaminate the very
> signal you are optimising against.**

That lands directly on [#29](../IDEAS_BACKLOG.md). Our self-regen loop optimises
the system prompt while the agent simultaneously has memory, `progress_read`, and
trace search. If any of those carry the lesson the prompt patch was supposed to
encode, the accept-gate is measuring a channel it does not control — the same
family as [harness-delta attribution](harness-delta-attribution.md)'s overfitting
warning, but about *leakage* rather than *train/test split*.

## It corrects how I have been reasoning about #60

[#60](../IDEAS_BACKLOG.md) (adaptive scaffolding) has been carrying a
"middle-tier models benefit most" intuition from
[Weng](weng-harness-engineering.md), alongside
[Niklaus](crush-charm-coding-agent.md)'s finding that vendor harnesses *drop* on
small models while model-agnostic ones climb.

WikiSkill measures the opposite gradient for *skills*: within the Qwen family the
gain is **+12.3 (4B), +17.5 (9B), +23.9 (27B)** — **larger models benefit more**.
And skills partly substitute for scale: **Qwen-3.5-9B with skills (47.4%) beats
Qwen-3.6-27B without (39.4%)**.

These are not contradictory, they are about different artifacts — a *harness* is
scaffolding that compensates for weakness, a *skill* is knowledge a model must be
strong enough to exploit. But it means #60 cannot assume one capability gradient.
**Fifth sighting of capability-dependence**, and the first where the gradient runs
*upward*. The practical consequence: whatever #60 learns per tier must be learned
per *artifact type*, not as a single "how much scaffolding does this tier want"
knob — which is what Prax sets globally today.

## Transfer: skill discovery and skill execution are different capabilities

**Skills evolved by one model transfer across families, and sometimes beat
self-evolved ones.** On ALFWorld, Qwen-3.5-9B scores **70.2% using a
Qwen-3.6-27B-evolved skill vs 63.4% with its own**.

Directly useful, and it fits Prax's shape: if the artifact is portable, you can
**evolve on a strong model and deploy on a cheap one**. That is the model-agnostic
thesis with a cheap-tier economics story attached, and it is testable here — Prax
runs DeepSeek/GLM/OpenAI side by side through one proxy.

## Their open problem is the thing we assessed last week

Their third stated limitation:

> *"the Wiki Layer continuously accumulates pattern pages, evolution logs, and
> proposal diffs across iterations, but WikiSkill currently lacks an automated
> mechanism to prune the wiki."*

That is precisely what [OptMem](optmem-append-only-memory.md) does: an append-only
record with an age-decaying, budget-bounded reading layer over it, and a
derived-cache design where a bad summary is fixed by rebuild rather than surgery.
Their wiki is append-mostly with patch edits and no compaction; OptMem is
compaction with an immutable record.

**The two papers compose**, and neither author knows about the other. That
composition — *WikiSkill's layer separation over OptMem's bounded reading budget*
— is the most interesting design in this note, and it is ours to try because we
have read both.

Worth noting the same week produced [Chroma Foundation](chroma-foundation.md),
whose product is *"a self-improving wiki"* built from agent sessions. A Google
paper, a funded product, and a 1.3k-star hack all converging on
**consolidate-sessions-into-a-persistent-wiki** in one month is a signal about
where the field is, even if none of them has beaten the others in a fair test.

## What not to take

- **The framework itself.** Four LLM roles per iteration on top of a benchmark
  train/val split is a research harness, not a deployment. Prax already has the
  parts: `run_self_regen` + `accept_change` is the propose/gate loop, memory
  consolidation is a Wiki Maintainer with a different output format.
- **The retrieval story, because there isn't one.** Their first limitation is
  explicit: skills are **injected whole** into the prompt, deliberately, so that
  triggering and retrieval failures do not confound the study. That is honest and
  it also means **the paper says nothing about what happens when you have fifty
  skills**, which is the regime any real deployment reaches.
- **The strict accept-gate.** They keep only proposals that *improve* validation,
  excluding neutral ones that might enable later gains — and they flag it
  themselves. Combined with our own
  [judge noise floor](judge-bias-audit-2026-08-20.md) (0.217 mean, 0.450 worst on
  a golden total), a strict improve-or-reject gate on a noisy scorer accepts and
  rejects partly at random. **Their gating assumes a fitness signal cleaner than
  ours is.**

## Honest limits

Google-authored, self-evaluated, no third-party replication, and the baselines are
all 2026 preprints re-implemented by the authors — the usual caution about who
tuned whose baseline. Improvements are reported as accuracy deltas with **no
confidence intervals and no seed variance**, on benchmark subsets whose sizes sit
in an appendix; several of these deltas are large enough to survive that, but the
paper does not show it. **No cost accounting** for four LLM roles per iteration,
which is the number a deployment would actually need. An AI-disclosure note says
models generated some tables and plots. And their headline framing — *"skill
evolution complements model scaling"* — rests on a single model family's three
sizes, i.e. n=3 points on the axis carrying the claim.

## Adopt-tracker rows

| Item | Status |
|---|---|
| **The three-layer separation: raw traces / persistent knowledge / executable artifact — where the ARTIFACT rolls back and the KNOWLEDGE does not** | 📋 queued — the cleanest statement of what #29 is missing; today a rejected patch teaches us nothing |
| **No side channels around the artifact under optimisation** — giving the inference agent the wiki *degraded* final skill quality (63.7 → 60.9) | 📋 queued — audit #29 for leakage: memory, `progress_read` and trace search may already be carrying lessons the prompt patch is credited with |
| **Compose WikiSkill's layering with [OptMem](optmem-append-only-memory.md)'s bounded reading budget** — their stated open problem (no wiki pruning) is exactly what OptMem solves | 📋 queued — the most interesting design here, and available only because we read both |
| **Evolve the artifact on a strong model, deploy it on a cheap one** — skills transfer across families and can beat self-evolved (ALFWorld 70.2 vs 63.4) | 📋 queued — testable on Prax's existing 3-family proxy setup; "discovery and execution are distinct capabilities" |
| **#60 must learn per ARTIFACT TYPE, not one scaffolding knob per tier** — skill gains rise with scale (+12.3/+17.5/+23.9) where harness gains do not | ✅ recorded — **5th sighting of capability-dependence, first with an upward gradient** |
| The WikiSkill framework (4 LLM roles/iteration) · the strict improve-or-reject gate · full-injection skill delivery | ❌ declined — a research harness, not a deployment; their gate assumes a cleaner fitness signal than [ours measurably is](judge-bias-audit-2026-08-20.md); and they explicitly do not evaluate retrieval, so the paper is silent on the many-skills regime |
