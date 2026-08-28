# Prime Agent — self-improving RLM agent (PrimeIntellect-ai)

**PrimeIntellect-ai/prime-agent, MIT, TypeScript. Created 2026-05-08, 4,468
commits, 71 stars, 27 open issues / 26 open PRs.** A funded AI-infrastructure
company's coding-agent product. Two core abstractions: a **Recursive Language
Model** (context as variables, programmatic tool/sub-agent calls inside a
persistent IPython REPL) and a **Continual Harness** — durable state holding
supplemental prompts, memories, skill descriptions and subagent specs that the
agent "can refine through small, evidence-backed updates," with rollback, via
a `/refine` command that reviews trajectories.

**Verdict: document + adopt THREE concrete gaps it exposes — a persistent REPL
session, skills-as-importable-packages, and wiring our own `/refine` equivalent
which already exists and has zero consumers. Decline its security posture,
which is the opposite of Prax's thesis.**

> **Correction (same day).** An earlier draft of this note said "the README
> contains no benchmarks at all" and built an argument on it. That was true of
> the README and **false of the project**: the launch blog reports
> **ARC-AGI-3 95.5% RHAE Best@1 with Opus 5**, against a human-expert baseline
> of 95.4%, plus 99.97% Best@3 and three consistent runs (95.0 / 95.2 / 95.5),
> along with long-context results (OOLONG, OBLIQ-Bench, LongBenchPro, ManyIH
> Coding) and Rust emulator builds (SEGA Genesis, Game Boy Color) where other
> approaches reportedly failed. I audited one surface and generalised from it —
> the exact failure the [harness survey](agent-harness-engineering-survey.md)
> note warned about when a third-party summary invented numbers. The numbers
> below are theirs and unreplicated by us, but they exist and they are strong.

---

## What it genuinely has that we do not

Three real gaps, stated without flinching:

1. **A persistent REPL.** Their IPython environment survives across calls, so
   variables, imports and intermediate state carry between steps. Prax's
   `run_python` is `subprocess.run` per call — a fresh interpreter every time,
   no state. That is the difference between a REPL and a calculator, and it is
   the [RLM](rlm-harness-lid.md) code-as-action lane we banked as
   a "narrow experiment" and never built.
2. **Skills as importable packages.** Recurring workflows become Python
   packages the agent imports. Prax has spokes (fixed, code-owned) and
   plugins (human-authored) — nothing the agent promotes from its own repeated
   work.
3. **`/refine` — a wired continual-harness loop.** This is the sharp one.
   Prax **has** `prax/eval/self_regen.py` with a proposer, an auditor, overlay
   application, and `inoculate()` — and grep shows **zero consumers** outside a
   CLI script, exactly like `accept_change`. We built the parts and never
   closed the loop. They shipped the loop.

Their `/refine` is also [Harness-R1](harness-r1.md)'s and
[HarnessCompass](harness-compass.md)'s thesis in product form: durable harness
state, evidence-backed small updates, rollback. Three sources in one week now
describe the same mechanism, and one of them is a shipping product.

## What Prax has that it explicitly does not

Their README states it plainly: Prime Agent "executes model-generated Python
and project commands with your user permissions" and is "**not** a security
sandbox." Users must review changes themselves and only use trusted sources.

That is not a small architectural difference — it is the inverse of Prax's
entire premise. Prax runs model-generated code in `prax-sandbox`, wraps every
tool in `governed_tool.py` with risk tiers and a lethal-trifecta guard, holds
real credentials in a separate process so a compromised agent has nothing to
steal, and gates high-risk actions on approval. Prime Agent's answer to all of
that is "review the diff yourself."

For a coding tool a developer babysits, that trade is defensible. For an agent
answering Discord and SMS on a live box, it is not available to us. **We should
not close this gap by copying them.**

## The ARC-AGI-3 number, and what it actually says

**95.5% RHAE Best@1 on ARC-AGI-3, above the 95.4% human-expert baseline**, is
the most striking agent result in this whole assessment lane. For scale, in the
same week's reading: [NOOA](nooa-object-oriented-agents.md) reported 85.1% and
[OpenAI's own harness post](openai-arc3-harness-settings.md) 38.3%. The
[saturation study](benchmark-saturation.md) specifically names ARC-AGI as one
of the few benchmarks *still discriminating*, so this is not a saturated-target
win.

Two things must be held together, though. First, **they ran Opus 5** — the
strongest model available. That is precisely the variable our own
Terminal-Bench baseline isolated: harbor's reference agent scored the same as
ours on the same weak model, so **the model is the dominant term** and a
harness comparison across model tiers is not a comparison at all. Prax at 13.5%
on qwen3-coder-30B and Prime Agent at 95.5% on Opus 5 are not measurements of
the same quantity.

Second, their own honest note: "**currently no model has been trained around
Prime Agent**." They are claiming a harness result on an untrained-for model,
which makes the number *more* impressive as harness engineering, not less.

## Where the comparison is still not what it looks like

We measured Prax on the full 89-task Terminal-Bench 2.0 at **13.5%**, published
it, then ran harbor's own reference agent on the same tasks, same model, same
box — **5.6%** — and recorded that the difference was noise while the 10× cost
difference was not. That process is why our number feels bad: **we have one.**
An unmeasured system always looks better than a measured one, and the felt gap
between Prax and a product with no published numbers is not evidence about
capability.

This is worth naming as a standing trap, because it will recur: honest
measurement is a competitive disadvantage in *appearance* and an advantage in
*fact*. The saturation study makes the same point from the other end — a score
you cannot interpret is worse than no score, and a score you can is worth
having even when it is low.

**The sharper version of the lesson, given the correction above:** the right
response to a strong external number is to find out what model produced it
before concluding anything about the harness. Our 13.5% and their 95.5% differ
by model tier, benchmark, and task type. The one number that would actually
compare the harnesses is Prax on ARC-AGI-3 with Opus 5 — which is buildable
(the adapter is parked, not blocked) and is the only way to answer "why can't
Prax be this good" with evidence instead of vibes.

Also relevant to scale: 4,468 commits from a funded company with a team,
against one founder and agent sessions. That is context, not an excuse — but it
does mean "why can't Prax be this good" is partly a question about headcount,
and the honest answer is that on the axes Prax chose (sandboxing, governance,
keyless credentials, measured evals, an agent-agnostic collaboration shell)
Prime Agent is not competing at all.

## Honest limits

README- and blog-level read; I have not installed or run it, and none of
their numbers are replicated here. Star count (71) and age
(three months) are small; 27 open issues against 26 open PRs suggests active
but early. TypeScript, so nothing lifts directly into Prax. No paper, no
evaluation, no independent replication — every capability claim here is theirs.
"Self-improving" is doing some marketing work: `/refine` updating supplemental
prompts and skills is real and useful, and is not the same thing as the
recursive self-improvement the phrase invites you to imagine. The ARC-AGI-3
figure is Best@1 over three runs on the public set; Best@3 at 99.97% is a
different and much weaker claim than a single-shot score, and "surpasses the
human expert baseline" rests on whichever baseline figure they adopted.

## Related

- [harness-r1.md](harness-r1.md) + [harness-compass.md](harness-compass.md) —
  the research versions of `/refine`; this is the shipped one.
- [rlm-recursive-language-models.md](rlm-harness-lid.md) — the
  RLM lane we banked as a narrow experiment; they built it.
- [weng-harness-engineering.md](weng-harness-engineering.md) — rung 4 again.
- [benchmark-saturation.md](benchmark-saturation.md) — why a measured low
  score beats an unmeasured impression.
