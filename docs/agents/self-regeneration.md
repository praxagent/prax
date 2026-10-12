# Self-Regeneration — the recursive self-improvement loop

[← Agents](README.md)

> *The threshold that matters isn't a chat demo — it's AI becoming part of the loop
> that improves AI.* For an agent **harness**, that means: Prax autonomously produces
> a **better version of its own scaffolding** (tools, prompts, routing, skills) — and
> **proves it's better before adopting it.**

This doc is the *loop*. The two self-modification **surfaces** it drives already
exist: [self-improvement.md](self-improvement.md) (weights — LoRA hot-swap) and
[self-modification.md](self-modification.md) (code — staging-clone + verify + PR).
What's been missing is the **closed, autonomous, safe loop** that ties them to a
fitness function.

## Scope it right: harness self-improvement, not model self-improvement

"Make a better version of yourself" reads as "rewrite my weights" — but that needs
frontier *training* infra Prax will never own (it's a model **consumer** + narrow
LoRA fine-tuner). Prax's achievable — and correct — target is its **harness**: the
tools, prompts, routing, skills, and evals where most agent leverage actually lives
(see [harness-engineering](../research/harness-engineering.md)). The goal is **"Prax
produces a better version of its own scaffolding, verified,"** not "Prax retrains a
frontier model."

## The load-bearing truth: the fitness function is the whole game

Recursive self-improvement optimizes **brutally** against whatever measures "better."
Goodhart isn't a footnote — it's the danger. **An agent told "improve yourself,"
pointed at a *gameable* eval, produces a version that games the eval** (the METR
"environment cheating" finding — [prax-benchmarks §6](../research/prax-benchmarks.md) —
at the *self* level). So:

> **RSI is only as safe as its fitness function is un-gameable. The un-gameable-eval
> work is the *precondition* for letting Prax touch itself at all — not quality
> hygiene.**

This reframes Prax's readiness. The **easy** half of RSI — surfaces to change itself —
Prax has (`plugin_write`, prompt-as-file, `finetune_service`, sandbox + `make ci`).
Everyone has that now. The **hard** half — a fitness function that survives an
optimizer attacking it, plus safe graded adoption — is exactly what's been built:
deterministic [`verify`](../research/awesome-evals.md), the supervising
[auditor](../research/diffuse-ai-control-judge-robustness.md), **accept-rate not
self-report** ([#22](../IDEAS_BACKLOG.md)), the "never spike / fix the problem class"
rule, and flag-gated-default-off rollout + durable checkpoints. **That stack *is* the
RSI substrate.**

## The loop (mapped to what Prax has)

**Notice → Propose → Isolate → Verify → Canary-adopt → Rollback / Record**, run by
Prax on itself:

1. **Notice** — a concrete weakness from the eval/signal stack, *not vibes*: a
   failing golden, a recurring failure (`metacognitive.py` ≥3), a low accept-rate
   behavior (#22), a pass/fail trace-diff ([#19](../IDEAS_BACKLOG.md)). ✅ inputs exist.
2. **Propose** — an **abstracted** fix (never-spike): a prompt edit, a new/edited
   plugin (`plugin_write`), a routing tweak, a LoRA. ⚠️ have code-authoring; no
   systematic prompt/routing proposer yet.
3. **Isolate** — apply it in a sandbox / git worktree. ✅.
4. **Verify** — `make ci` (correctness/lint) + `make eval` (goldens, gaming-resistant).
   **Adopt only if it improves without regressing or spiking.** ✅✅ the over-invested
   part — correctly.
5. **Canary-adopt** — flag-gated default-off → measure accept-rate on real traffic
   (#22) → promote or roll back (checkpoints). ✅ substrate.
6. **Record / compound** — log *what kind* of change helped as a signal/skill
   ([#17](../IDEAS_BACKLOG.md), [#23](../IDEAS_BACKLOG.md)) so the loop learns its own
   improvement priors. ⚠️ tracked, not closed.

**The missing piece is the *orchestrator*** — which is **#23 (the ambient initiative
loop) pointed at Prax itself**, governed by #22. The engine is designed; it isn't
aimed inward yet.

## The prompt-overlay loop as built (`prax/eval/self_regen.py`)

The first loop that exists is narrower than the plugin rung below: it edits a
**system-prompt overlay** and is graded by the deterministic capability suite.
It is propose-only unless `--apply` and `SELF_REGEN_ENABLED` are both set, and
nothing calls it but the CLI:

```bash
uv run python scripts/eval_suite.py self-regen --rounds 5 --revise-kept --split
```

**What it enforces, whatever the mode:**
- **The scorer is fingerprinted** before and after: the capability cases, the
  generators, the goldens and the loop itself. If any moved mid-run, nothing is
  kept.
- **The keep decision needs a margin of at least one case**, or an Occam tie
  (shorter at equal score).
- **The winner must pass the private golden split** (`accept_change`), measured
  against a replicate noise floor. Without private goldens it fails closed.
- **Two vetoes run before any evaluation:**
  - deterministic: over-length patches, and patches containing an answer number
    from any check, `regex` checks included;
  - an LLM auditor, on the full patch.

**Two protocol options** (both off by default; [harness
learning](../research/harness-learning.md) is the evidence for both):

| Flag | What changes |
|---|---|
| `--split` | **Three disjoint case sets.** The pool is the authored suite plus generated cases (`prax/eval/case_generators.py`; `--generated` per family, computation only today), split *within each family* (`split_cases`). The proposer sees the **feedback** set's failures: the task, the answer given, the checks missed. A change is kept only on the disjoint **scoring** set. The private goldens stay the **held-out** set. A one-case family is scored, never shown. |
| `--revise-kept` | Each round **revises the kept overlay**, with a fresh report from running it, instead of drawing an independent addition from one fixed signal. |

Without `--split`, the proposer still sees only dimension averages: hiding the
cases is the only defence against memorizing them when the same cases decide
the keep. With `--split`, that defence is the measurement. A fix that works
only on the cases shown earns nothing on the scoring set.

**Every proposal is recorded** in `revisions.jsonl` beside the run's
`summary.json`, in every mode:
- the exact proposer input;
- the patch, and its parent;
- the measured score and delta;
- the vetoes and the audit;
- whether it was kept;
- the case ids on each side.

That is the (context, edit, reward) record a trained proposer learns from: SFT
on kept revisions, RL on all of them. It accumulates now, for when a training
GPU is available. Held-out content never enters a proposer input, so it never
enters the record. The files live in `$PRAX_EVAL_DIR` (data only, never
committed); `prax-eval-battery` is the private place to back them up.

**Model weights.** "Never own training infra" above is about frontier models. A
small proposer that edits the harness, with the agent's model frozen, is a
different size of job: the harness-learning paper trained a 4B one. It is
parked until a GPU is free, not declined.

## The two things that make it safe (the real bottleneck — not code-writing)

1. **A graded self-modification boundary** (the capability-gateway / tool-risk model
   applied to *self*-changes, deny-by-default):
   - **Low risk** (a new/edited plugin/tool) → **auto-adopt behind the eval gate**.
   - **Medium** (prompt tweak) → **canary + accept-rate gate**.
   - **High** (orchestrator policy, weights) → **human gate** (the existing
     self-modification.md PR path).
2. **Blast-radius containment** — every self-change reversible (durable checkpoints),
   canaried, with accept-rate (#22) as the early-warning tripwire.

## The first rung (small, bounded, uses only what exists)

Close the loop on the **lowest-risk, most-measurable** self-change: **Prax proposes +
tests a new/edited *plugin* against a golden, auto-adopting behind a flag only if it
improves the eval without regression.** That's *"make a better version of one of my
tools, proven"* — a genuine RSI micro-loop. Prove the fitness function holds there,
**then** widen scope to prompts, then routing. Tracked as
[IDEAS_BACKLOG #29](../IDEAS_BACKLOG.md).

## The line not to cross (yet)

**Autonomous edits to the orchestrator policy / system prompt, before the fitness
function is proven un-gameable.** That's where a gamed eval turns self-*improvement*
into confident self-*degradation* — undetectable precisely because the grader is the
thing being corrupted. Widen autonomy **only as fast as the fitness function earns
trust**; until then, high-risk self-changes stay on the human-gated PR path.

## Bottom line

Prax's gating resource for "AI in the loop that improves AI" is **not** capability to
change itself — it has that. It's **a measure of "better" that survives an optimizer
attacking it, plus a safe graded-adoption boundary.** Both are unusually far along.
What remains is the **one spoke that closes the loop inward** (#29) and the discipline
to widen its autonomy only as the eval proves un-gameable.
