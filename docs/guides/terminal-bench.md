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

## Honest baseline so far

- One-shot protocol (no feedback loop, TB 1.0 trivial-env subset, 26
  scored): **1/26** — a deliberate floor, not the number.
- First live harbor trial (2026-07-30, qwen3-coder-30b, 20-step budget,
  `break-filter-js-from-html`): pipeline verified end to end — 21 terminal
  steps in the task container, verifier scored, reward 0.0. A 30B model on a
  hard task at a tight budget failing is the expected outcome; the run's
  purpose was proving the wiring.

The harbor sweep above is the leaderboard-comparable path. History and
runner-artifact lessons (network, `$TEST_DIR`):
`docs/research/openai-arc3-harness-settings.md` and the adopt-tracker's
coding-agent-benchmark row.

## Keyless on the dev box specifically

The dev box routes OpenRouter through the **forward** MITM proxy — run
harbor with `HTTPS_PROXY=http://127.0.0.1:8786`,
`SSL_CERT_FILE=~/PRAX/prax-proxy-ca-bundle.pem`, and a `NO_PROXY` covering
github/docker/pypi hosts (harbor clones the dataset from GitHub, and git
does not trust the MITM CA).
