"""Load REAL benchmark test sets (from HuggingFace) into the adapters.

The adapters ship a small inline **seed set** for keyless CI. For a real
accountability run you want the actual benchmark data — but the full sets are
large (MMLU-Pro 12k, GSM8K 1.3k) and, at Prax's ~28K-token-per-task harness
overhead, a full sweep is expensive. So this loader:

- Downloads the real set once, **caches it as JSONL under
  ``$PRAX_EVAL_DIR/datasets/<name>.jsonl``** (PRAX_EVAL_DIR is data-only, never
  committed — the benchmark-contamination firewall), and
- Returns up to ``limit`` cases (default from ``PRAX_EVAL_DATASET_LIMIT``, else
  all) so runs default to a **representative subset** — the "-lite"/-500 configs
  labs report — for a few dollars, not the whole set.

Adapters opt in: ``Adapter(full=True)`` (or ``PRAX_EVAL_FULL_DATASETS=1``) loads
the real set; otherwise the inline seed set is used. Fully offline/keyless when
the dataset isn't present or ``datasets`` isn't installed — it just falls back to
the seed set, so ``make ci`` never touches the network.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def _cache_path(name: str) -> Path:
    from prax.eval import PRAX_EVAL_DIR
    return Path(PRAX_EVAL_DIR) / "datasets" / f"{name}.jsonl"


def dataset_limit(explicit: int | None = None) -> int | None:
    """Case cap: explicit arg wins, else PRAX_EVAL_DATASET_LIMIT, else None (all)."""
    if explicit is not None:
        return explicit
    val = os.environ.get("PRAX_EVAL_DATASET_LIMIT", "").strip()
    try:
        n = int(val)
        return n if n > 0 else None
    except ValueError:
        return None


def full_datasets_enabled() -> bool:
    return os.environ.get("PRAX_EVAL_FULL_DATASETS", "").lower() in ("1", "true", "yes")


def sample_seed() -> int:
    """Deterministic sampling seed (PRAX_EVAL_SAMPLE_SEED, default 0).

    Subsets are a seeded random sample, not first-N: several source datasets are
    ordered (MMLU-Pro by category, MATH by topic), so first-N would be a biased
    slice. A fixed default seed keeps runs reproducible and comparable; vary the
    seed to estimate sampling variance.
    """
    try:
        return int(os.environ.get("PRAX_EVAL_SAMPLE_SEED", "0"))
    except ValueError:
        return 0


def load_cached(name: str, limit: int | None = None) -> list[dict] | None:
    """Return cached cases for *name*, or None if not downloaded.

    When a limit applies, returns a **seeded random sample** (see
    :func:`sample_seed`) rather than the first N — representative and
    reproducible.
    """
    import random

    path = _cache_path(name)
    if not path.exists():
        return None
    cases = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    lim = dataset_limit(limit)
    if lim and lim < len(cases):
        rng = random.Random(sample_seed())
        cases = rng.sample(cases, lim)
    return cases


def fetch_and_cache(name: str, hf_id: str, split: str, map_row: Callable[[dict], dict | None],
                    *, config: str | None = None, hf_token: str | None = None) -> int:
    """Download *hf_id* [*config*]/*split*, map each row to a case, cache as JSONL.

    ``map_row(row) -> case | None`` maps a raw HF row to the adapter's case dict
    (return None to skip a row). Returns the number of cases cached. Needs the
    ``datasets`` lib + network; the contamination firewall means the cache lives
    under PRAX_EVAL_DIR and is never committed.
    """
    from datasets import load_dataset  # local import: optional, not needed for CI

    token = hf_token
    if not token:
        try:
            from prax.settings import settings
            token = getattr(settings, "hf_token_ro", None)
        except Exception:
            token = None
    token = token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    kwargs = {"split": split}
    if config:
        kwargs["name"] = config
    if token:
        kwargs["token"] = token
    ds = load_dataset(hf_id, **kwargs)

    path = _cache_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w") as f:
        for row in ds:
            case = map_row(dict(row))
            if case is not None:
                f.write(json.dumps(case) + "\n")
                n += 1
    logger.info("Cached %d %s cases -> %s", n, name, path)
    return n


def cases_for(name: str, seed_cases: list[dict], *, full: bool, limit: int | None = None) -> list[dict]:
    """Pick real cached cases when *full* (and available), else the seed set.

    The single entry point adapters call. Never raises on a missing dataset — it
    degrades to the seed set so CI and offline runs always work.
    """
    if full or full_datasets_enabled():
        cached = load_cached(name, limit)
        if cached:
            return cached
        logger.warning("Full dataset '%s' requested but not cached (run "
                       "scripts/fetch_eval_datasets.py); using seed set.", name)
    return seed_cases
