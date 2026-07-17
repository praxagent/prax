"""Capture the exact configuration a run executed under — for reproducibility.

Published *with* eval results (embedded in every ``summary.json``) so anyone
reproducing a number knows precisely which flags, models, and dataset knobs were
set. If a reproduction gets a different result with a different configuration,
the record shows exactly which flags to enable — "you didn't set the flags we
published" instead of an unfalsifiable "you cheated."

**Never captures secrets.** Only booleans (feature flags), non-secret
model/provider names, and eval dataset knobs are recorded — API keys and other
secret settings are excluded by construction (booleans can't be secret; the
string fields are a whitelist).
"""
from __future__ import annotations

import os
import subprocess

# Non-secret run knobs worth pinning so a reproducer can match the harness.
_RUN_STR_FIELDS = (
    "llm_provider", "search_provider", "embedding_provider", "embedding_model",
    "base_model", "low_model", "medium_model", "high_model", "pro_model",
)

# Eval dataset / execution env knobs (set per run, not in settings).
_ENV_KNOBS = (
    "PRAX_EVAL_FULL_DATASETS", "PRAX_EVAL_DATASET_LIMIT",
    "PRAX_EVAL_TASK_TIMEOUT_S", "PRAX_EVAL_CONCURRENCY", "MATRIX_LIMIT",
    "EVAL_TIER", "PRAX_EVAL_SAMPLE_SEED",
)


def _git_commit() -> str | None:
    """Best-effort HEAD commit so a run is pinned to exact code."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=5, cwd=os.path.dirname(__file__),
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def eval_config_snapshot() -> dict:
    """Return the reproducibility snapshot: git commit + every feature flag +
    non-secret models/providers + eval env knobs.

    Flags are keyed by their SCREAMING_CASE env alias so the value can be copied
    straight into a ``.env`` to reproduce. Never raises — degrades to whatever it
    can read.
    """
    snap: dict = {"git_commit": _git_commit(), "flags": {}, "run": {}, "env": {}}
    try:
        from prax.settings import settings
    except Exception:
        return snap

    # EVERY boolean feature flag, captured wholesale — the published record can't
    # be accused of quietly omitting a behaviour-affecting flag.
    flags: dict[str, bool] = {}
    for name, field in type(settings).model_fields.items():
        try:
            val = getattr(settings, name)
        except Exception:
            continue
        if isinstance(val, bool):
            flags[str(field.alias or name).upper()] = val
    snap["flags"] = dict(sorted(flags.items()))

    run: dict[str, str] = {}
    for name in _RUN_STR_FIELDS:
        val = getattr(settings, name, None)
        if val:
            run[name.upper()] = str(val)
    snap["run"] = run

    snap["env"] = {k: os.environ[k] for k in _ENV_KNOBS if k in os.environ}
    return snap
