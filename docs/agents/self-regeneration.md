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
