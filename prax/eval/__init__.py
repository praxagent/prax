"""Prax eval — two distinct evaluation systems.

## 1. Regression testing from observed failures (existing)

The regression system turns production failures into reproducible
test cases.  Each failure in the journal becomes a guard: replay the
input, score the output, confirm the fix didn't regress.

Pipeline:
  feedback → failure_journal → runner.run_eval → pass/fail report

Usage::

    from prax.eval.runner import run_eval, run_eval_suite
    result = run_eval(case_id="abc123")
    report = run_eval_suite(user_id="user1")

## 2. External benchmark harness (GAIA, τ-bench, AgentDojo, etc.)

The external harness runs Prax against public agentic benchmarks
under **strict data isolation**.  Raw benchmark content — questions,
ground-truth answers, traces, responses — lives OUTSIDE the
repository at ``PRAX_EVAL_DIR`` (default
``/Users/d7082791602/PROJECTS/prax-evals``), a sibling of the repo
and outside ``workspaces/`` so Prax's workspace tools cannot reach
it (contamination prevention).  Public scrubbed receipts go to
``docs/research/receipts/``.

See ``prax/eval/README.md`` for the full isolation + compliance
story, and ``prax/eval/gaia_single.py`` for the runner.

Usage::

    from prax.eval.gaia_single import run_gaia_task
    result = run_gaia_task(task_id="...", cost_limit_usd=2.0)
"""
from __future__ import annotations

import os
from pathlib import Path

# Single isolation root for external benchmark runs.  Outside the
# repo, outside workspaces/, outside every Prax tool's scope.
PRAX_EVAL_DIR: Path = Path(
    os.environ.get("PRAX_EVAL_DIR", "/Users/d7082791602/PROJECTS/prax-evals")
).resolve()
