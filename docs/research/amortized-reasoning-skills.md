# Reason Wide, Not Deep — compiling the reasoning premium into skills

**Source:** [arXiv 2608.07885](https://arxiv.org/abs/2608.07885) — *Reason Wide,
Not Deep: Amortizing the Reasoning Premium into Distilled Skills*. Singh,
Gautam, Gupta, Mehrotra, Bakshi, Gulwani.

**Verdict: document + ADOPT.** This is the strongest adopt candidate in weeks,
for a reason that has nothing to do with the results: **every prerequisite
already exists in Prax, and the mechanism is scaffolding, not weights.**

## The claim

Reasoning modes beat non-reasoning modes on multi-step agentic tasks but pay a
**3–6× premium in output tokens on every episode** — and much of that premium
is spent *re-deriving procedures that are shared across episodes of the same
domain*. So don't pay it per episode: have a coding agent read a corpus of
existing trajectories from a training split, compile a **compact
natural-language skill**, and inject it into a *non-reasoning* model's system
prompt.

Reported on ALFWorld, Tau²-bench (telecom + retail) and
SpreadsheetBench-Verified: skills recover **55%–100%+** of the reasoning gap on
held-out tasks, **beat reasoning mode outright on 2 of 4** benchmarks, cut
output tokens **2.7–6×**, and use **zero reasoning tokens**.

## Why this one is different

The last several papers we assessed named a real problem and then solved it
with a training loop we cannot run. This one's remedy is:

1. a corpus of past trajectories,
2. a coding agent to read them,
3. a system prompt to inject the result into.

Prax has all three, today. Traces are recorded and semantically searchable
(`trace_search`, `trace_detail`); `.progress/` holds the complete session
record and, since 2026-08-08, is **greppable** (`progress_search`) rather than
date-addressed only — which is precisely the corpus-mining step this needs. The
sandbox is a coding agent. Prompt injection is the existing mechanism
(`prompt_selectivity`, the per-turn `agent_plan` injection).

It also lands directly on the cost thread: the live box moved to cheaper models
on 2026-08-09, and the 2026-08-10 model campaign measured output-heavy runs
(luna-pro at ~230k tokens/case against glm-5.2's 63–107k). Output tokens are
the expensive half of every bill. A 2.7–6× reduction there is real money, not a
rounding error.

## The third sighting of skills, and the first with numbers

[Crush](crush-charm-coding-agent.md) ships the `SKILL.md` standard;
[Prime Agent](prime-agent.md) named skills-as-packages as a gap. Both were
"other people do this". **This is the first with a measured effect size**, and
it reframes what a skill is *for*: not reusable prose for a human to invoke,
but **a compiled artifact that buys back a specific, measurable token premium.**

It is also the fourth independent sighting of capability-dependence. The
[filesystem-memory](filesystem-agent-memory.md) paper found *"a verbatim
episode log serves a strong execution agent best, distilled guidance a weak
one"* — a distilled skill IS distilled guidance, and this paper shows it
lifting a *weaker* (non-reasoning) configuration toward a stronger one. Same
finding from the other side, and further reason to run **#60**'s
verbatim-vs-distilled arm.

## ⚠ The way this goes wrong for us: it is a spike generator

The method compiles a skill from trajectories and evaluates on held-out tasks.
Applied carelessly here it becomes **benchmark contamination with extra steps**:
compile a skill from capability-suite trajectories and the suite stops
measuring anything, while the number goes up. That is the definition of a spike
under this project's prime directive, and it would be *especially* insidious
because the artifact is natural language rather than a code patch — nobody
reading the system prompt would obviously see the eval encoded in it.

Non-negotiable constraints if this is built:

- **Compile only from PRODUCTION traces**, never from eval-case runs. The
  capability suite and `prax-eval-battery` are off-limits as corpus.
- **Validate on held-out cases the skill's corpus never touched** — the
  public/private split already exists for exactly this
  ([prax-eval-battery](https://github.com/praxagent/prax-eval-battery)).
- **A skill must be readable and reviewable.** If someone who knows the eval
  can read the compiled skill and tell which tasks it targets, it is a spike
  and must be rejected. That is the existing anti-spike test, applied to a new
  artifact type.
- **Skills are untrusted content that shapes behaviour** — the constraint
  already recorded for the Crush adopt. Compiled from production traces, a
  skill can carry whatever an injected web page put in that trace. It must
  arrive tainted and must not be able to widen a capability ceiling.

## Where it plausibly pays in Prax, and where it will not

Skill distillation works when episodes **share procedures**. Prax is an
open-ended personal assistant, which is the unfavourable case in general — but
it has genuinely repetitive lanes:

- Library workflows (capture → note → notebook → space), which follow the same
  shape every time.
- The deploy/verify loop on the live box.
- Eval-campaign setup itself.
- Note authoring and the progress read/append discipline.

It will *not* help open-ended research turns, and the paper's own residual gaps
on telecom and SpreadsheetBench say the same thing: some domains need genuine
per-instance reasoning that no corpus distillation replaces. Do not expect this
to be a general win.

## Honest limits

Abstract-level reading only; the 55%–100%+ and 2.7–6× figures are theirs and
unverified here. Note the range on the headline: the **low end recovers barely
half** the gap, and "100%+" on 2 of 4 benchmarks is doing a lot of work in a
summary that reads as uniformly positive. The method also presumes a *training
split of same-domain trajectories* — Prax's corpus is one user's traffic, which
is thinner and far less homogeneous than ALFWorld.

## Adopt-tracker rows

| Item | Status |
|---|---|
| **Compile a skill from production traces for one repetitive lane, measure the output-token delta on held-out cases** | 📋 queued — narrow first target, not a general capability |
| Skills-as-packages (`SKILL.md`-style unit) — **3rd sighting**, first with a measured effect | 📋 queued (was already open from Crush/Prime Agent) |
| Anti-spike constraints on any compiled skill (production corpus only; held-out validation; human-readable; arrives tainted) | 📋 **binding precondition**, not optional |
