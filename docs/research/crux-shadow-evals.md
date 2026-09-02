# CRUX — shadow evaluations: can agents do open-ended AI research? (arXiv 2607.27191)

**Kirgis, Kapoor, Schwartz, Rabanser, … Narayanan (Princeton/UK AISI/CRUX,
26 authors, 2026-07-29; artifacts at cruxevals.com).** New method: **shadow
evaluation** — take the central research question of a high-quality
*unpublished* NeurIPS 2026 submission, give a frontier agent six days, $3,000
of API credits, GPUs, a VM and the open web, and have the paper's **original
authors** grade the result as reviewers. Uncontaminated by construction (the
findings aren't on the web yet), expert graders by construction. Two papers
run (OpenClaw + Opus 4.8, extra-high reasoning): both **unambiguously
rejected** (2/6, 1/6 overall). A Codex + GPT-5.6 Sol "ultra" robustness rerun
**reproduced nearly every failure mode** — this is not scaffold overhang.

**Verdict: document + adopt the boundary and two concrete design constraints.
The paper is the sharpest external evidence yet that Prax's #29 scoping —
self-improvement only inside verifiable gates — sits exactly on the line
between what agents can and cannot do.** Their Table 2 is the same split we
practice: every working system they survey (AlphaEvolve, AIDE², NanoGPT
speedrun, DGM) is verifier-scored; the open-ended lane is where both their
agents died.

---

## The five failure modes, mapped onto Prax

1. **No model of the quality bar** (synthetic/hand-picked data, coalesced on
   a direction before evidence warranted it; internal reviews inflated —
   "Weak Reject" for papers experts unambiguously rejected). → The
   maker≠checker stack: [#36]'s judge audit, the supervising auditor, and
   [eval-rigor](eval-rigor-review-2026-07.md). Their agents' self-review *surfaced*
   the right issues and still didn't gate behavior — a review that doesn't
   bind is telemetry, not a gate. Prax's `accept_change` binds.
2. **No creative response to negative feedback** (added caveats, narrowed
   hypotheses, "grew narrower and less interesting as it discarded each
   one"). → The textual-gradients row: turning a failure into a *directed*
   proposal is precisely what doesn't happen by default.
3. **No project-level backtracking** (both retired ambitious targets within
   ~10 hours; clean-context restart subagents existed, were used routinely —
   but almost never used to restart). **Tool presence ≠ disposition.** → The
   population/MCTS row (don't marry the first basin) and failure-provenance
   diagnosis (replan vs retry) are the structural versions of what these
   agents lacked.
4. **No resource awareness** (<50% of budget spent, done 7h before deadline —
   while GPT-5.6 in the rerun burned $3k in two days then wrote the paper on
   fumes: miscalibrated in *both* directions). → cost-budgeted selection +
   budget-as-first-class-knob; agents "are trained on human data but have
   very different affordances."
5. **Instruction drift — explicitly via "context rot during compaction."**
   Rules acknowledged early were lost over days. → **New design constraint
   for the ACM/compaction row: compaction must preserve standing
   instructions** (pin goal/rule blocks through every compaction; re-assert
   on resume). This is the failure our agent_plan-reinjection already guards
   per-turn; multi-day horizons need it across compactions.

## The bracket with the same week's other result

OpenAI's [harness-settings post](openai-arc3-harness-settings.md) showed
settings *tripling* an interactive-benchmark score; CRUX shows a scaffold
swap reproducing every open-ended failure. Read together: **harness
engineering buys execution efficiency wherever feedback is verifiable; it
does not buy research judgment** — what to try, when a bar is met, when to
abandon. That is the exact line Prax draws: scaffolding-over-weights for
capability, and a self-regen loop that never leaves deterministic gates.

## A finding uncomfortably close to home

§4.9: they gave **Claude Fable 5** (goal mode, ultracode) their pilot logs
and scaffold and asked it to fix the failure modes. It "assigned
disproportionate weight to a single n=1 sample, making broad changes …
idiosyncratically swapping rules and heuristics," fixing nothing
fundamental. That is the [grounding-gap](ilands-grounding-gap.md) trap plus
the missing [significance-testing](harness-generalization.md) gate, observed
in the wild — and a live warning for #29's proposer *and for how this
assessment lane reacts to single incidents*. One failure is an anecdote;
the adopt rows exist to stop us hill-climbing on anecdotes.

## Honest limits (theirs and ours)

n=2 papers (+1 rerun); non-blind expert review by authors with disclosed
priors (core team known skeptics of imminent RSI — they say so, release all
artifacts, and surface coauthor disagreement about *why* the agents failed:
creativity vs reasoning vs epistemic lock-in). Fable 5 is deliberately
limited on frontier AI R&D (Anthropic), so "strongest model" is untested.
Six days ≪ the authors' months, though neither agent exhausted the budget it
had. Their own contrast: the same scaffold shipped an iOS app autonomously —
engineering and verifiable research tasks keep working; open-ended judgment
is the wall. For Prax the operational conclusion is not "agents can't do
research" but **"never let a claim about Prax's autonomy cross the
verifiable/open-ended line without saying which side it's on."**

## Related

- [aide2-recursive-self-improvement.md](aide2-recursive-self-improvement.md) +
  [self-improving-agents-survey.md](self-improving-agents-survey.md) — the
  verifiable-gate systems that DO work (their Table 2 agrees).
- [ilands-grounding-gap.md](ilands-grounding-gap.md) — authored-signal traps;
  §4.9 is its case study.
- [harness-generalization.md](harness-generalization.md) — the significance
  gate that would have caught the n=1 scaffold edits.
- [acm-agentic-context-management.md](acm-agentic-context-management.md) —
  gains the instruction-preserving-compaction constraint.
- [openai-arc3-harness-settings.md](openai-arc3-harness-settings.md) — the
  other half of the bracket.
