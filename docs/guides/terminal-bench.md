# Running Terminal-Bench 2.0 with Prax as the agent

Terminal-Bench 2.0 (89 tasks) runs through **harbor**, the official harness
from the Laude Institute. Prax plugs in as a harbor agent:
`prax/eval/tb_agent.py` adapts **Prax's own agent loop** (`build_agent_loop`,
so the middleware stack, model routing, keyless proxy path, and token
accounting are the production ones) to harbor's `BaseAgent` contract, with a
`terminal` tool bound to the task container's `environment.exec`.

**Label results honestly.** What runs is Prax's loop + model plumbing with a
terminal tool — NOT the 97-tool orchestrator (spoke tools point at surfaces a
benchmark container doesn't have: the Library, memory stores, Prax's own
sandbox). Every published number must say so; `AgentContext.metadata.harness`
records it per trial.

## Setup (once)

harbor is **not** a prax dependency — give it its own venv with prax
installed editable alongside:

```bash
cd ~/PRAX
uv venv tb-venv --python 3.14
uv pip install --python tb-venv/bin/python harbor -e ./prax
```

## Run

Keyless as always — the model goes through the secrets proxy / OpenRouter
path exactly like `make eval CHEAP=1`:

```bash
export PRAX_TB_PROVIDER=openrouter          # or unset for the default provider
export PRAX_TB_MODEL=qwen/qwen3-coder-30b-a3b-instruct
export PRAX_TB_MAX_STEPS=40                 # per-task step budget

~/PRAX/tb-venv/bin/harbor run \
  -d terminal-bench@2.0 \
  -a prax.eval.tb_agent:PraxAgent \
  -m "$PRAX_TB_MODEL" \
  -n 2                                      # 2-core box: keep concurrency low
```

Useful additions: `-i <task-name>` to run a single task; `--n-attempts k`
for pass^k. Results land in harbor's output dir; per-trial metadata carries
steps/tokens/cost (cost is `None` when the model has no known rate — unknown
is never rendered as $0.00).

## Cost expectations (measured, 2026-07-30)

The pre-harbor one-shot probe measured this model at ~$0.0002/task on TB 1.0
trivial-env tasks. The agentic loop multiplies tokens by roughly the step
count (each step re-reads history); budget **single-digit dollars** for a
full 89-task run at ≤40 steps — still cheap, but measure a 3-task
`--task-name` sample before a full sweep.

## Measured result — full Terminal-Bench 2.0 sweep (2026-08-02)

**Prax's agent loop + terminal tool, `qwen/qwen3-coder-30b-a3b-instruct`,
40-step budget, all 89 tasks, `-n 2` on a 2-core box.**

| | |
|---|---|
| **Pass rate** | **13.5%** (12/89 attempted) · **15.6%** (12/77 excluding infra errors) |
| **Total cost** | **$0.46** — $0.0051 per attempted task |
| Tokens | 5,860,785 in / 165,221 out (35:1 — agentic loops re-read history every step) |
| Wall clock | 9h 30m |
| Infra errors | 16 of 89: 6 environment-start timeouts, 4 verifier timeouts, 4 agent timeouts, 2 runtime errors |

**Read the two pass rates as a range, not a choice.** 16 trials never produced
a score, and most of those are this box rather than the model: environment-start
and verifier timeouts are a 2-core machine losing races that a bigger one
wins. The 4 *agent* timeouts are arguably genuine failures (too slow inside the
allotted budget). A faster host would score somewhere at or above 13.5%, and
the honest statement is "13.5–15.6% on this hardware", not a single number.

**What this is not.** It is not the 97-tool orchestrator — spoke tools point at
surfaces a benchmark container doesn't have. It is Prax's loop, middleware and
model plumbing driving a terminal, which is what the `harness` field records
per trial. And it is a **30B open model**: for scale, the NOOA paper reports
73.0% on this benchmark with GPT-5.5. The interesting result here is not the
rank, it is that **a full agentic sweep of a marquee coding benchmark costs
under fifty cents**, which makes it repeatable rather than an event.

Earlier, weaker measurement kept for contrast: a one-shot protocol (no
feedback loop) on the TB 1.0 trivial-env subset scored 1/26.

## The baseline that makes the number readable (2026-08-03)

A pass rate in isolation measures **the model far more than the harness**. So
the same 30-task subset was re-run with harbor's own reference agent,
**terminus-2**, on the *same model, same box, same tasks*:

| agent | scored rate | no-score | cost/task |
|---|---|---|---|
| **PraxAgent** | **8.0%** (2/25 scored) | 5/30 | **$0.0051** |
| terminus-2 (reference) | 5.6% (1/18 scored) | 11/29 | $0.053 |

**What this does NOT show: that Prax's harness is better.** Two passes versus
one is not a difference — at this n it is noise, and reading it as a win would
repeat the mistake the [retained-reasoning A/B](../research/retained-reasoning-ab-2026-08-02.md)
caught (one run said +1 case; replication said identical means).

**What it does show, and why it is recorded:**

1. **The harness is not the bottleneck.** The official reference
   implementation scores the same, within noise, on the same model. 13.5% is
   near what this *model* yields on this benchmark, so the dominant lever for
   a higher score is a stronger model — not harness tuning.
2. **Cost differs by ~10×, and that is not noise.** $0.0051 vs $0.053 per
   task, consistent across the subset: the whole 89-task Prax sweep cost less
   than a third of a 30-task reference run.
3. **The non-scoring trials are the box, not the agent.** terminus-2 lost
   11/29 to timeouts where Prax lost 12/89 — infrastructure hits both, and
   hits the reference agent harder.

Publish the pass rate with this baseline attached. A score with no same-model
comparison says almost nothing about the harness that produced it.

**The measured failure signature** (from the 89-task sweep, worth fixing
independently of score): of 65 scored failures, **55 (85%) called `task_done`
claiming success** and were overruled by the verifier. Note also that no
passing run used more than 33 of its 40 steps (median 22), so raising the step
budget is not the fix the numbers suggest — failures wander rather than run
out of room.

## Disk: a sweep will fill it

harbor **pulls a prebuilt environment image per task** from the registry
(`alexgshaw/<task>:<date>`, ~6 GB each — verified: the images carry registry
digests and predate our run by months, so nothing is built locally). Across 89
tasks that took this box from comfortable to **97% full** — the 2026-07-08
outage mode, where at 100% the sandbox and every Prax tool call start failing.

**Between runs, use harbor's own cleanup.** It targets exactly these artifacts
and is the supported path:

```bash
harbor cache clean    # removes alexgshaw/*, hb__*, sb__* images + ~/.cache/harbor
```

**During a run, don't** — `cache clean` also clears `~/.cache/harbor`, which a
job in flight is using. Prune first, then keep a plain `docker rmi` loop
alongside the sweep:

```bash
docker container prune -f && docker image prune -f    # before starting

while pgrep -f "[h]arbor run" >/dev/null; do          # during
  for img in $(docker images --format '{{.Repository}}:{{.Tag}}' | grep '^alexgshaw/'); do
    docker rmi "$img" >/dev/null 2>&1    # refuses images backing a live container
  done
  sleep 240
done &
```

`docker rmi` will not remove an image a running container depends on, so a
task in flight can never be reaped out from under itself. On the real sweep
this held free space steady (it recovered 5 GB → 51 GB mid-run). Budget
**~15 GB of headroom** even with the reaper running.

## Keyless on the dev box specifically

The dev box routes OpenRouter through the **forward** MITM proxy — run
harbor with `HTTPS_PROXY=http://127.0.0.1:8786`,
`SSL_CERT_FILE=~/PRAX/prax-proxy-ca-bundle.pem`, and a `NO_PROXY` covering
github/docker/pypi hosts (harbor clones the dataset from GitHub, and git
does not trust the MITM CA).
