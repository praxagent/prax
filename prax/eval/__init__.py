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


def _default_eval_dir() -> Path:
    """Repo-sibling ``prax-evals/`` — outside the repo, outside workspaces/.

    Derived from this file's location so it's correct on *any* clone instead of
    a hardcoded path from one developer's machine.  ``__file__`` is
    ``<repo>/prax/eval/__init__.py``; ``parents[2]`` is the repo root, so the
    sibling is ``<repo>/../prax-evals`` (e.g. ``/home/ubuntu/PRAX/prax-evals``).
    Override with ``PRAX_EVAL_DIR`` for any other location.
    """
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root.parent / "prax-evals"


# Single isolation root for external benchmark runs.  Outside the
# repo, outside workspaces/, outside every Prax tool's scope.
PRAX_EVAL_DIR: Path = Path(
    os.environ.get("PRAX_EVAL_DIR") or _default_eval_dir()
).resolve()


def resolve_task_timeout(override: float | None = None) -> float | None:
    """Per-task wall-clock timeout in seconds, or ``None`` for no limit.

    Resolution order: an explicit *override* wins; otherwise the
    ``PRAX_EVAL_TASK_TIMEOUT_S`` setting (0 = disabled).  ``None`` means a task
    runs to completion — the correct default for a slow local model where one
    task may take minutes-to-hours and the suite runs overnight.
    """
    if override is not None:
        return override if override and override > 0 else None
    try:
        from prax.settings import settings
        val = int(getattr(settings, "eval_task_timeout_s", 0) or 0)
    except Exception:
        val = 0
    return float(val) if val > 0 else None
