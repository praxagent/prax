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

## Keyless on the dev box specifically

The dev box routes OpenRouter through the **forward** MITM proxy — run
harbor with `HTTPS_PROXY=http://127.0.0.1:8786`,
`SSL_CERT_FILE=~/PRAX/prax-proxy-ca-bundle.pem`, and a `NO_PROXY` covering
github/docker/pypi hosts (harbor clones the dataset from GitHub, and git
does not trust the MITM CA).
