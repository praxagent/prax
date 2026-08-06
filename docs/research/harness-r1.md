# Harness-R1 — Learning to Edit Executable Runtime Harnesses from Agent Failure Trajectories

**DeepExperience, Apache-2.0, first pushed 2026-07-31 (13 stars, very new).**
Trains a **"harness engineer"** — a model that reads *batches of failed agent
trajectories* and emits **reusable runtime patches** that improve a **frozen**
target agent. The loop: frozen target runs tasks → failures collected →
engineer generates a patch → patch validated and sandboxed → **the same target
reruns the same tasks** → reward is the measured performance delta.

Reported (Qwen3.5-9B engineer, three AgentBench-family benchmarks —
WebShop/ALFWorld/DBBench): frozen baseline **44.3% → 53.6% (+9.3 pp)**;
against an agent-SFT target **59.2% → 64.2% (+5.0 pp)**; **+7.06 pp** across 20
unseen targets; **+8.9 ± 1.5 pp** over 1,270 held-out tasks. Two-stage
training: cold-start SFT on 877 editing examples, then GRPO (rollout batch 4, 8
samples/prompt, lr 1e-6).

**Verdict: document + adopt THREE mechanisms, decline the training. This is the
closest external work to [#29](../IDEAS_BACKLOG.md) we have found — it is
literally the rung-4 project from [Weng's ladder](weng-harness-engineering.md),
built and measured — and the parts worth taking are exactly the parts that
need no GPU.**

---

## Why this one is different from the other eight GPU-wall papers

Every previous "self-improvement" assessment ended at *this needs training
hardware*. This one does too — GRPO on a 9B engineer is out of reach, and the
engineer is the trained artefact. **But its architecture is the transferable
part, and the architecture is inference-only.** Three mechanisms, in order of
value to us:

### 1. Reward = the measured delta from rerunning the same target on the same tasks

"Rewards computed from actual performance deltas (**not learned judges**)."

That is the un-gameable fitness function #29 has been circling, stated as
plainly as it can be. Not a rubric, not an LLM grader, not a proxy: apply the
patch, rerun the identical tasks with the identical frozen agent, and the
reward *is* the difference. It cannot be talked into a better score.

We already hold `accept_change` (private↑ at ≤cost, fail-closed) — but with
**zero consumers**, verified by grep. Harness-R1 is the existence proof that
the loop around it works when the scorer is a rerun rather than a judge.

### 2. Freeze the target; edit only the harness

The agent under improvement never changes. Only the harness around it does.
This is a **safety property we should adopt regardless of whether we ever
train anything**: the blast radius of a self-proposed change is bounded to the
scaffold, and any regression is reverted by dropping a patch rather than by
recovering a model.

It also sharpens the [Weng](weng-harness-engineering.md) read-only-scorer row
into a pair of invariants for #29 P1: **the scorer is read-only, and the agent
is frozen.** The loop may write to exactly one surface.

### 3. Four named lifecycle intervention points

Their patch surface is not "edit arbitrary code" — it is four hooks:

| Hook | What it may do |
|---|---|
| `on_init` | initialise state, inject skills |
| `make_pre_hint` | emit soft guidance before a decision |
| `on_before_action` | **allow, block, rewrite, or force** an action |
| `on_post_step` | update state after feedback |

That is a **constrained edit surface**, and constraining it is what makes the
patches validatable, sandboxable and revertible. Prax has the analogous seams
already — `loop_middleware.py` (provenance tainting, per-step heartbeat behind
`AGENT_MIDDLEWARE_ENABLED`) and `governed_tool.py` (risk tiers, the
lethal-trifecta guard) — and `on_before_action` is *precisely* the governed-tool
wrapper. So the #29 P1 edit surface should be **the existing middleware
hooks**, not free-form code edits. Same instinct as the Weng row: turn a policy
("only edit safe things") into a property (only these four things are
editable).

## What we decline, and why

The training pipeline: SFT + GRPO on a 9B model. Ninth GPU-wall sighting, and
this time the wall is load-bearing — the engineer *is* the trained weights.
Also worth noting for anyone tempted: their engineer is "**only meaningful
against the target it was trained for**," so a downloaded checkpoint would not
transfer to Prax's orchestrator anyway.

## Honest limits — and one that should give us pause

Their own list is candid, and one item is a warning about our whole eval
posture: results are "**reportable only when `webshop_goal_seed=233`** and task
manifests strictly match." A result that requires a specific seed to reproduce
is a fragile result. Combined with **13 stars, first pushed five days ago, no
paper linked from the repo, and benchmark assets, weights and training data all
absent**, the numbers should be treated as *unreplicated vendor-grade claims*.
I have not run any of it.

Two further caveats: the benchmarks (WebShop/ALFWorld/DBBench) are
comparatively narrow, well-trodden environments, and per
[benchmark-saturation](benchmark-saturation.md) they are exactly the vintage
where headroom flatters an intervention. And "+9.3 pp on a 44.3% baseline" is a
gain measured where there was a great deal of room; it says little about
whether harness-editing helps a strong agent — which is the case we care about.

Its **+7.06 pp across 20 unseen targets** is the most interesting number,
because generalisation across targets is the thing that would make harness
patches worth having; it is also the number I can least verify.

## Related

- [weng-harness-engineering.md](weng-harness-engineering.md) — the ladder;
  this is rung 4 built and measured, and their four hooks are how you make
  rung 4 safe.
- [aide2-recursive-self-improvement.md](aide2-recursive-self-improvement.md) —
  our `accept_change` gate, still with zero consumers; this is the loop that
  would consume it.
- [crux-shadow-evals.md](crux-shadow-evals.md) — the verifiable side of the
  boundary again: rerun-the-same-tasks is about as verifiable as a reward gets.
- [asari-inference-optimization.md](asari-inference-optimization.md) — same
  pattern (self-improvement works where the evaluator is objective).
