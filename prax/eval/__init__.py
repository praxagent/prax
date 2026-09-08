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
import re
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


# Errors that are the HARNESS's fault, not the agent's. Everything else is
# attributed to the agent and scored as a failed case.
#
# Fail-closed on purpose: an unrecognised error counts AGAINST the agent. The
# opposite default (exclude anything that errored) is what let a 180s timeout
# on an unsatisfiable request disappear from the pass rate entirely — a run
# that gets worse should never be able to score better by failing harder.
# Adding a pattern here is a deliberate act of saying "this was our fault".
_INFRA_ERROR_PATTERNS = (
    "connection refused",
    "connection reset",
    "connection error",
    "temporarily unavailable",
    "name or service not known",
    "rate limit",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
    "insufficient_quota",
    "invalid api key",
    "authenticationerror",
    # Provider rejected OUR credentials/config. A missing or revoked key is the
    # harness's fault, not the agent's — the first matrix run parsed these as
    # wrong answers and published a fake 0.00. Run-level errors only reach
    # this classifier (tool-level HTTP failures are tool results, not errors).
    "unauthorized",
    "missing authentication",
    "no space left on device",
)

# HTTP statuses that mean the provider refused OUR request as such: bad
# credentials (401/403) or throttling (429).  They count only in an HTTP-status
# context — the number followed by its reason phrase, or introduced by
# "HTTP" / "status [code]" / "error code".  As bare substrings they matched
# "agent run exceeded 403s maximum runtime", "recursion limit of 401 reached"
# and "expected 1403 tokens": agent failures that were then EXCLUDED from the
# score, so a run that crashed hard on a case outscored one that got it wrong.
_INFRA_HTTP_STATUS_RE = re.compile(
    r"\b(?:401|403|429)\b\s*[-:\u2014\u2013]?\s*"
    r"(?:unauthorized|forbidden|too many requests|missing authentication|"
    r"authentication|permission denied|client error|rate limit)"
    r"|\b(?:https?(?:/\d(?:\.\d)?)?|status(?:[\s_]?code)?|error[\s_]?code)"
    r"\s*[:=]?\s*(?:401|403|429)\b",
    re.IGNORECASE,
)


def is_infrastructure_error(error: str | None) -> bool:
    """True when *error* is an environment fault rather than an agent failure.

    An agent timeout is NOT infrastructure — running out of budget on a task is
    a capability outcome and must be scored as one.

    This is THE attribution rule for every eval aggregator (capability suite,
    benchmark adapters, harness-lift, GAIA suite): an infra-classified error is
    excluded from the pass rate and reported as ``excluded_infra``; any other
    error is scored as a failure, keeps its tokens on the cost axis, and is
    reported as ``errored_as_failure``.

    Fail-closed: an unrecognised error is the agent's.  Adding a pattern here
    is a deliberate act of saying "this was our fault", and a pattern must not
    match text an agent failure can plausibly contain (digits in a timeout or
    recursion message, for instance — hence ``_INFRA_HTTP_STATUS_RE``).
    """
    if not error:
        return False
    low = str(error).lower()
    if any(p in low for p in _INFRA_ERROR_PATTERNS):
        return True
    return _INFRA_HTTP_STATUS_RE.search(low) is not None
