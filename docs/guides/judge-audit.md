# Auditing Prax's LLM judges — stability and bias

Prax grades itself with LLMs in several places. Every published eval number, and
the [#29](../IDEAS_BACKLOG.md) self-improvement accept-gate, rests on those
verdicts being *trustworthy* — which means two separate properties that are easy
to confuse:

- **Stability** — the same input yields the same verdict. A judge that flips
  verdicts makes a published number unreproducible.
- **Bias** — the verdict tracks the thing being graded, not something else
  (length, position, family). **A judge can be perfectly stable and reproducibly
  wrong**, so stability is necessary and nowhere near sufficient.

This guide is the standing instrument for both halves. Run it when a judge
changes, when the model ladder moves, or before publishing a number anyone will
act on.

## The judge surfaces

There are more than the eval graders, and they are not all the same kind of thing.

| Surface | What it produces | Temperature | Notes |
|---|---|---|---|
| `eval_judge` — `eval/goldens.py`, `eval/runner.py`, `eval/live_eval.py` | rubric scores (0/1 per criterion) | `JUDGE_TEMPERATURE` (0.0) | the number-producing graders |
| `self_regen_auditor` — `eval/self_regen.py` | approve / veto on a patch | `JUDGE_TEMPERATURE` (0.0) | **the #29 accept-gate**; fail-closed |
| `note_quality_reviewer` — `services/note_quality.py` | `{"approved": bool, ...}` | **0.2** | a *gate* with a stochastic verdict — see below |
| `subagent_content_reviewer` — `agent/spokes/content/reviewer.py` | review prose | **0.3** | reviews, does not gate; warm is defensible |
| `bare_executor` — `eval/capability.py` | an answer | `AGENT_TEMPERATURE` (0.7) | a **generator**, and the control arm for `harness_lift` — correctly warm, and matched to the harness arm |
| `claim_audit` | evidence match | *none* | **deterministic** (regex + tool-name evidence). No context to contaminate — stronger than a fresh-context judge |

Two things fall out of writing that table:

**`note_quality_reviewer` is a gate running warm.** It returns an `approved`
boolean that decides whether a note passes, at temperature 0.2. That is the same
shape of defect the stability half fixed at the eval graders, one layer over. It
is a smaller blast radius (one note, not a published number), which is why it is
recorded here rather than changed silently — but "small" is not "correct".

**The content reviewer already implements the anti-self-preference mitigation,
and the eval judge does not.** `_pick_reviewer_llm` deliberately prefers *a
different provider from the writer* — "auto-select: try a different provider for
diversity". That is maker≠checker enforced at the *model family* level. Meanwhile
`eval_judge` resolves through the same tier ladder as the agent under test, so on
a single-vendor ladder the judge and the graded agent can be **the same model**.
The discipline exists in this codebase; it is just not applied where the numbers
come from.

## The four probes

Implemented in [`prax/eval/judge_bias.py`](../../prax/eval/judge_bias.py), each a
function of an injectable `judge` callable — so the whole module is exercisable
with **no API key** (that is how `tests/test_judge_bias_probes.py` drives it) and
paid only against a real model.

### `length_bias`
Grade the same answer twice: as written, and padded with **information-free
filler**. Effect = padded total − terse total, in rubric-score units.

*The probe's precondition is the whole probe.* The filler carries no entity,
number, citation or claim, and the probe re-checks every deterministic `verify`
criterion on both variants — if any moved, the padding added gradeable content
and the probe **voids itself** rather than reporting a number about the wrong
thing.

### `verdict_drift`
Re-grade one answer N times and count non-unanimous criteria. Threshold **0.0**:
with temperature pinned, *any* drift is a finding.

Pinning temperature is **necessary, not sufficient** — providers are not
bit-deterministic, and an MoE router can put the same prompt on a different
expert. Only criteria *without* a `verify` regex count toward the denominator;
deterministic criteria cannot drift, and including them would dilute the rate and
make a drifting judge look stabler than it is.

### `criterion_order`
The classic position-bias probe compares two *candidates* in fixed order. Prax has
**no symmetric pairwise judge** — nothing anywhere asks "which of these two is
better", so there is no arbitrary placement for a verdict to key on.

The near miss is worth naming precisely, because it looks like one.
`eval/runner.py` does put two outputs in one prompt — `original_output` then
`new_output` — but their **order encodes semantics, not placement**: one is
labelled the known failure and the other the candidate, and the question is "is
this failure fixed", not "which is better". Swapping them would change the
question, so the position-bias probe does not apply.

What that surface *is* exposed to is **anchoring**: the judge is told up front
that the first output failed, and why. That is deliberate — it is the task — but
it is an untested influence on the verdict, and it is a different probe than any
of the four here. Recorded as open, not measured.

So the probe attaches to the ordered structure Prax genuinely has: an *ordered
list of rubric criteria in one prompt*. Permute the order, re-grade, and see
whether a criterion's verdict depends on where it sat. A criterion's verdict must
be a function of the criterion and the answer, never of its position.

### `self_preference`
The only probe needing a 2×2: answers from ≥2 generator families, graded by ≥2
judge families, **every judge grading every answer**. Effect = mean over families
of *(own-family score − mean of what the other judges gave that same answer)*.

A single judge disagreeing with another is **disagreement, not self-preference**.
Self-preference is the interaction, and it cannot be computed from one judge's
scores — which is why the probe voids rather than guesses when the grid is
incomplete.

## The remedy: `JUDGE_VOTES`

Drift is the finding with a fix available today. `JUDGE_VOTES` gives each judged
criterion *k* independent ballots and takes the **strict majority** — a tie falls
to 0, because a split panel is not a satisfied criterion.

Why voting rather than a provider `seed`: `seed` is OpenAI-only and documented as
best-effort, while the live ladder runs DeepSeek and GLM through OpenRouter.
Voting is model-agnostic and works on every provider.

Three properties worth knowing before you raise it:

- **`JUDGE_VOTES=1` is byte-for-byte the previous behaviour** — one call, one
  verdict, no majority logic, no vote metadata in the result. A scorer change that
  silently re-baselined every published number would be worse than the drift it
  fixes.
- **A failed or unparseable ballot is dropped, not counted as a 0.** A crashed
  call is missing evidence, never evidence against the criterion — the same
  distinction as the eval error accounting. If *every* ballot fails, the result
  carries an `error` rather than a silent zero.
- **`verify` criteria consume no ballots.** Deterministic criteria are already
  deterministic.

It costs *k*× judge tokens, and **raising it re-baselines every published
number**, so treat the flip as a scorer change: re-run the affected suites, do not
compare across the boundary.

**Scope, stated plainly:** `JUDGE_VOTES` covers **golden rubric scoring**
(`score_golden`), where verdicts are binary per criterion and a majority is
well-defined. The regression judge in `eval/runner.py` and the live-eval judge
produce *continuous* axis scores, where the analogue is a median rather than a
majority — a different change with different risk, and not made here. Those two
surfaces still carry whatever drift the measurement below found.

## Running it

```bash
# core probes across every golden with judged criteria, using the REAL default judge
uv run python scripts/judge_bias_audit.py --trials 5 --orders 3 \
    --out "$PRAX_EVAL_DIR/judge-bias/core-probes.json"

# add the generator x judge grid that self-preference needs
uv run python scripts/judge_bias_audit.py --goldens research_multiperspective \
    --families openai=openai:gpt-5.4-nano,deepseek=openrouter:deepseek/deepseek-v4-flash-0731
```

Properties the runner enforces, each learned from a specific failure:

- **The judge under test is the shipped judge.** Same `eval_judge` routing, same
  `JUDGE_TEMPERATURE`. Measuring a judge we do not ship measures nothing.
- **The rubric is frozen for the campaign** — fingerprinted before each golden and
  re-checked after; a mid-run change **aborts** rather than producing numbers that
  look comparable and are not. (The read-only-scorer property, learned on
  2026-08-07 when a scorer fix landed mid-campaign and two arms were discarded.)
- **Results are checkpointed after every golden**, so a timeout or a kill still
  reports the goldens that finished. All-or-nothing output means a run that dies
  at 90% reports nothing — the same "count what came back" defect the probes are
  written to avoid.
- **An errored probe is reported as errored, never as an effect of zero.** A
  broken probe is not evidence of an unbiased judge.
- **A skipped probe is named**, so "not measured" is distinguishable from "no bias
  found".

## Reading the output

Every probe reports `effect`, the pre-registered `threshold`, and the `n` it was
computed over — never a bare number. Thresholds are per-probe and fixed in code,
so a run cannot move its own goalposts.

Four outcomes, not two: **flagged** (effect exceeds threshold), **within
threshold**, **void** (the probe's precondition failed, so the effect is
meaningless), and **low power** (the effect is real but the test could barely have
detected one). A void probe is neither clean nor biased — read `void` first.

### Low power — why a null needs a baseline

**A null result is only evidence of absence when the test could have detected a
presence.** A judge that scores every criterion 0 — or every criterion 1 — sits at
the end of the scale and is never near a decision boundary, which is exactly where
bias lives. "No drift, no order effect" measured there says almost nothing.

This was found by reading the first real run rather than by reasoning about it:
the answer scored **0/5 on every criterion** and all three probes returned exactly
`+0.000`. Reporting that as "no bias detected" would have claimed evidence the run
did not contain — the same defect as a pass rate that sheds its caveats on the way
to the reader.

So every probe now reports baseline saturation, and a clean result at a saturated
baseline renders as **LOW POWER** rather than "within threshold". Two asymmetries
worth knowing:

- For `length_bias` only the **ceiling** costs power. A 0.0 baseline still permits
  the upward effect the hypothesis predicts, so it remains a valid one-sided test.
- Saturation explains a **null**; it never softens a positive. A flagged probe
  stays flagged.

The practical consequence for running the audit: **the answer under grading must
land mid-range.** A bare cheap-model call on a hard research golden scores 0 on
everything and wastes the run. Use a stronger `--answer-model`, or an answer
produced through the full harness, so the judge is actually discriminating.
