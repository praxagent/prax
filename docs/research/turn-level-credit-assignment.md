# AgentOPSD — turn-level credit assignment, and the question our evals can't answer

**Source:** [arXiv 2608.05987](https://arxiv.org/abs/2608.05987) — *AgentOPSD:
Recursive Self-Distillation for Agentic Reinforcement Learning*. Wang, Lu, Yao,
Wu, Wu, Cai, Sun, Ye, Hao, Gu, Cai, Shen, Yang.

**Verdict: document-don't-adopt the method (weights-level RL, the GPU wall
again) — bank the QUESTION, which we cannot currently answer and have three
open items that need it.**

## What it does

RL with verifiable rewards gives a **trajectory-level** advantage estimate:
the episode succeeded or it didn't. Their observation is that this "often fails
to credit the few pivotal decisions that determine outcomes in long-horizon,
multi-turn agentic tasks" — the reward is real but it is smeared across twenty
turns, nineteen of which were fine.

Their mechanism: aggregate **token-level teacher-student log-probability gaps**
into turn-level evidence, then recursively update a Bayesian belief in
**log-odds space** to identify which turns were pivotal. No extra critic, no
extra rollouts. Reported: **89.1% success on ALFWorld with Qwen2.5-7B**, above
GRPO and self-distillation baselines, on ALFWorld / WebShop / Search-QA with
Qwen2.5 3B and 7B.

## Why we don't adopt it

It needs a teacher/student pair and a training loop. That is the GPU wall this
project has now documented enough times that the pattern is the finding, not
the paper: the *diagnosis* in agentic-RL work is usually portable and the
*remedy* usually isn't.

Honest limit on this note: I have the abstract-level description only. The
89.1% figure is theirs and unverified here, and I have not read the ablations
that supposedly attribute the gains to turn-level aggregation.

## Bank the question, because it is ours

**Our evals are trajectory-level in exactly the way they criticise.** The
capability suite returns pass/fail per case. When a case fails we know *that*
it failed, never *which turn* went wrong — and this week that cost real time:

- **Four cases failed by every model in every arm** (2026-08-10). Diagnosing
  them meant reading answer previews by hand and inferring backwards. Three of
  the four turned out to be defects in the *case*, and one was a harness file
  leaking into the graded answer. Turn-level attribution would have pointed at
  all of it directly.
- **The 727k-token escalation** (#58). We know the turn count (146 tool calls)
  and the total cost. We do not know which turn the run stopped making
  progress, which is precisely the number that would size the intervention.
- **Self-regeneration** (#29) has to decide *what to patch*. A trajectory-level
  "this failed" is the weakest possible input to that decision, and
  [ARTS](arts-assessment.md) already flagged failure-provenance diagnosis as
  the thing to extract.

## What we could actually build

Prax already has most of the substrate, which is what makes this worth writing
down rather than filing under someday:

- `ExecutionGraph` records per-span tool calls, tokens, model and status.
- `prax/agent/logprob_analyzer.py` already computes **per-tool-call entropy**
  (`ToolCallEntropy`, `get_entropy_for_tool`) from logprobs on the OpenAI
  chat-completions path. That is a per-turn confidence signal we collect and
  currently use only for the entropy machinery.
- Traces are searchable (`trace_search`) and now carry honest token and cache
  accounting.

The cheap analogue of their idea, with no training: **treat per-turn entropy
and per-turn progress as evidence, and find the turn where the trajectory's
prospects changed most.** Their log-odds belief update is the principled
version; a first pass could be far dumber — the turn after which no new
grounded material entered the context, or the first repeated tool call. The
`SteadyingCounsel` thresholds (repeat count, budget fraction, step count)
are already a crude version of this, computed live rather than post-hoc.

**Do not build the Bayesian machinery.** Build the attribution *field* first:
when a case fails, record which span was last making progress. If that number
turns out to be useful, the smarter estimator can come later — and if it
doesn't, nothing was spent.

## Adopt-tracker rows

| Item | Status |
|---|---|
| **Turn-level failure attribution on eval traces** — record WHICH span a failed case stopped progressing at, not just that the case failed | 📋 queued; unblocks faster case diagnosis, sizes #58, and is the missing input to #29 |
| Recursive Bayesian belief update in log-odds space over teacher-student logprob gaps | ❌ declined — needs a teacher/student pair and a training loop |
