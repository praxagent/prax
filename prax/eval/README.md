# prax/eval — External benchmark runners

This package runs Prax against external agentic benchmarks (GAIA,
τ-bench, AgentDojo, etc.) under **strict data isolation** and
**cost-controlled execution**.

## The isolation rule (important)

**All eval data lives outside the repository.**  The single root is
`$PRAX_EVAL_DIR` (default: `/Users/d7082791602/PROJECTS/prax-evals`),
a sibling of the gpt-transcriber repo.  It is:

- **Outside git** — not committed, not tracked, not visible to `git`.
- **Outside `workspaces/`** — Prax's `workspace_list` / `workspace_read`
  / `workspace_search` tools are scoped to `workspaces/{user}/` and
  CANNOT reach a sibling PROJECTS directory.
- **Outside the sandbox container mount set** — the sandbox spoke
  runs in Docker with `workspaces/` mounted in, but NOT `prax-evals/`.

### Why this matters: contamination prevention

If eval data lived inside `workspaces/`, Prax could literally
`workspace_search("answer")` during a run and find the ground-truth
answer for the task he's being evaluated on.  That's not a
hypothetical — `get_workspace_context()` auto-injects a workspace
summary into every turn's system prompt, so eval content could leak
without Prax even calling a tool.  The `../prax-evals/` path kills
all three contamination vectors structurally.

### Why this matters: HuggingFace compliance

GAIA and several other benchmarks are gated HuggingFace datasets
whose terms prohibit resharing.  By keeping raw content exclusively
in `$PRAX_EVAL_DIR/` (never committed anywhere), we honor the terms
while still being able to run evals and publish *scrubbed* receipts
to `docs/research/receipts/`.

## Directory layout

```
$PRAX_EVAL_DIR/
├── gaia-cache/                      # HF datasets cache (via HF_DATASETS_CACHE)
├── runs/
│   └── gaia-{task_id}-{run_id}/
│       ├── task.json                # full GAIA task (question, ground truth)
│       ├── workspace/               # per-run isolated Prax workspace
│       ├── response.txt             # Prax's full final response
│       ├── answer.txt               # extracted "FINAL ANSWER: X"
│       ├── grade.json               # pass/fail, normalized diff
│       ├── trace.jsonl              # full orchestrator trace
│       ├── cost.json                # tokens, $ estimate
│       └── meta.json                # model, git sha, timestamp, task_id
└── .gitignore                       # belt-and-suspenders: `*`
```

## Compliance-scrubbed public receipts

After a run, the runner also writes a **scrubbed** receipt to
`gpt-transcriber/docs/research/receipts/gaia-run-{date}.md` that
contains:

- `task_id` (public identifier)
- Level, pass/fail, cost, token breakdown, wall time
- Tool call summary, plan step summary
- Our retro notes (what went wrong, root cause, fix applied)

The receipt **does NOT** contain the question text, the ground-truth
answer, or Prax's verbatim response.  The `_receipt.py` dumper
enforces this at runtime — not just discipline.

## Pre-run contamination assertions

Before any task runs, `_guards.assert_eval_isolation()` verifies:

1. `PRAX_EVAL_DIR` is outside `git rev-parse --show-toplevel`
2. `PRAX_EVAL_DIR` is outside `settings.workspace_dir`
3. Resolving any workspace path does not land inside `PRAX_EVAL_DIR`

If any check fails, the runner refuses to run.  Fail-fast.

## Eval-mode tool denylist

In eval mode, the orchestrator disables tools with unscoped
filesystem access:

- `self_improve_*` (can touch the repo itself)
- `plugin_write` (can write arbitrary files)
- Direct shell access outside the sandbox container

Keeps every *useful* capability tool (browser, fetch, notes, memory,
research, sandbox, scheduler) enabled.

## Running

```python
from prax.eval import run_gaia_task

result = run_gaia_task(
    task_id="c61d22de-5f6c-4958-a7f6-5e9707bd3466",  # example
    cost_limit_usd=2.0,
)
print(result["grade"]["pass"], result["cost"]["usd_estimate"])
```

See `prax/eval/gaia_single.py` for the runner implementation.
