# Tinker — a training API for researchers (Thinking Machines)

**Source:** <https://thinkingmachines.ai/tinker/> (vendor page; closed hosted
service, no code to read)
**Assessed:** 2026-08-06
**Question asked:** *"Not to use — but can we add something like this to the
lab? Or would it be best as say prax-infra?"*

**Verdict: document + adopt ONE (a named ceiling on a feature we already
shipped). Do not add a training API to prax-lab. Do not create `prax-infra`.**
The capability this points at is already modelled by the `JobSpec` contract and
already refused honestly; what is missing is an *executor*, not an *API* — and
when there is hardware to point it at, that executor belongs in `prax-sandbox`,
next to the container target it is a sibling of.

---

## What Tinker actually is

Four primitives, and the choice of *which* four is the whole contribution:

| primitive | what it does |
|---|---|
| `forward_backward` | forward + backward pass, accumulating gradient |
| `optim_step` | update weights from the accumulated gradient |
| `sample` | generate tokens — for interaction, evaluation, or RL actions |
| `save_state` | checkpoint for resumption |

LoRA rather than full-weight updates ("trains a streamlined adapter instead of
updating all base model weights"), over 20+ open-weight models (GPT-OSS 20B/120B,
Qwen 3.5 4B→397B, DeepSeek V3.1, Nemotron, and their own Inkling).

The seam it draws: **the researcher owns data, algorithm and reward; the
provider owns the fleet, the scheduler and the allocation.** That is stated
plainly on the page — "full control of their data and algorithms without
worrying about infrastructure management."

The reason this is interesting and not just another fine-tuning product is the
*level*. A fine-tuning API takes a dataset and returns a model. Tinker
deliberately sits one rung lower: it hands you the training loop's inner
statements and lets you write the loop. You cannot build an RL method on
"fine-tune this dataset"; you can build one on `forward_backward` /
`optim_step` / `sample`.

## Why it does not belong in prax-lab

Not a hardware argument — a structural one.

prax-lab is worth having for exactly one reason: **every object it touches is
durable, hashable and citable.** A Plan validates against a schema or it is not
a plan. A JobSpec is a typed handoff. An Artifact has a `content_hash`, and the
reporter will delete a claim that cites a hash the run did not produce. The
control plane's authority comes from the fact that it only ever holds things
that can be checked later.

**A gradient step is none of those things.** It is not durable, it produces no
citable artifact, and there is nothing about step 4,312 for a reviewer to
verify. Putting `forward_backward`/`optim_step` into the control plane would
mean the lab drives an inner loop at a granularity where its only real
mechanism — validate, hash, cite, reject — does not apply. It would be ceremony
on top of a training script.

The right granularity for the lab is the one it already has: **a training run
is a JobSpec.** And the schema already says so —

```
job_spec.schema.json:  target.kind ∈ {local, managed-k8s, slurm}
                       resources.gpu (integer ≥ 0), resources.gpu_type
                       slurm_args
artifact.schema.json:  kind ∈ {..., model_checkpoint, activation_shard, ...}
plan.schema.json:      resources.gpu_hours_estimate
```

— and `prax/exec_api.py:141` refuses `resources.gpu > 0` with an explicit error
rather than silently running it on a CPU. That is the contract already being
honest about the hole. Nothing needs designing here; something needs *building*,
and only once there is something to build against.

## Why not `prax-infra`

Three reasons, in order of weight.

1. **The executor already has a home.** `prax-sandbox` is the harness-agnostic
   execution repo. It is already what `prax.execute` calls; it already
   parameterises local in-process versus a remote FastAPI daemon
   (`SANDBOX_DAEMON_URL`). A Slurm or managed-k8s GPU backend is a *third
   adapter in that same abstraction* — a sibling of the container target, not a
   new product. Splitting execution across two repos by accelerator type would
   put the same decision ("where does this command run?") in two places.

2. **The name would be wrong for what it holds.** "Infra" reads as the Docker
   stack, compose files, the Lightsail deploy, systemd units — which today are
   split across `prax/Makefile`, `prax-sandbox/`, and `prax/deploy/`. There is a
   real (small) argument for consolidating *that*. If `prax-infra` is ever
   created it should be for the deploy surface, and calling the GPU lane by that
   name now would foreclose the better use of it.

3. **Seven repos ahead of the hardware is accretion.** The suite is at six code
   repos plus two data repos, and every one of them earns its boundary by being
   independently useful with a different agent as the brain. A repo whose entire
   contents are an interface to hardware we do not have fails that test. This is
   the [Weng](weng-harness-engineering.md) *adaptive simplification* point
   pointed at ourselves: delete scaffolding as it stops paying, and do not
   pre-build scaffolding that has not started paying.

## The one thing worth taking: train and sample must share weights

Prax already ships a LoRA pipeline — `prax/services/finetune_service.py`,
`scripts/finetune_train.py` (unsloth), and eight tools in
`prax/agent/finetune_tools.py`: harvest corrections from conversations → train →
verify → load into vLLM → promote → rollback → list adapters, all behind
`FINETUNE_ENABLED` (default off).

Tinker's decomposition makes a ceiling on that pipeline visible, and it is worth
naming: **`sample` is in the *training* API on purpose.** You sample from the
policy you are updating — that adjacency is the precondition for any on-policy
method. Prax's training path (a subprocess running unsloth) and its serving path
(vLLM loading an adapter directory) are separate processes that hand off a
directory on disk. That is entirely adequate for what we built it for —
supervised fine-tuning on harvested corrections, where sampling and training
never need to interleave — and it is **structurally incapable of an RL loop**,
no matter how much GPU you give it.

This is a ceiling, not a bug. It should be written down so that "add RL to the
finetune pipeline" is understood as a rebuild of the serving/training seam
rather than a feature on top of it.

## What we would actually do if a training lane were wanted

The cheapest grounded path is **rent, don't build**: a hosted training service
(Tinker itself, or any GPU provider) sits behind the existing
`target.kind: managed-k8s` and needs *no new architecture* — a JobSpec whose
command runs the training script, whose artifacts are `model_checkpoint`s, and
whose `content_hash`es the reporter can cite like any other evidence. The
adapter would be perhaps a few hundred lines in `prax-sandbox`, and it would be
grounded by construction because it would run.

What we should not do is build the abstraction first. Per
[iLands](ilands-grounding-gap.md), an interface that has never executed is
*authored*, not grounded; it will encode whatever we imagined the fleet looked
like.

## Honest limits of this assessment

- **Vendor page only.** Tinker is a closed hosted service. There is no code, no
  paper, and no independent evaluation here — the primitive list and the LoRA
  claim are transcribed from marketing copy. The cookbook is referenced but the
  page shows no signatures, so the four names above are all we actually have.
- **No numbers.** Nothing here is a performance claim about Tinker, and none was
  offered.
- **Tenth GPU-wall sighting.** Every assessment this session that touched
  weights hit the same constraint ([RLM](rlm-harness-lid.md),
  [LM-sleep](lm-sleep-consolidation.md), [ARTS](arts-agentic-tree-search.md),
  [MORPHEUS](skyfall-morpheus-continual-learning.md),
  [Harness-R1](harness-r1.md), [ACM](acm-agentic-context-management.md),
  [agentic-RL control plane](agentic-rl-evolution-control-plane.md), and others). The
  novel part of this one is that it is the first where the wall is *not* the
  reason to decline — the structural argument against putting a training loop in
  the control plane stands even with a GPU cluster available.
- **The shipped finetune pipeline has never been run end to end here** — neither
  box has a GPU and `FINETUNE_ENABLED` is off. A ledger row now records that.
