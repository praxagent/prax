"""Key-free tests for the real-dataset loader (prax.eval.benchmarks.datasets).

Degradation to the seed set (so CI/offline never touch the network) + the
subset limit are the load-bearing behaviors.
"""
from __future__ import annotations

import json

from prax.eval.benchmarks import datasets as ds

SEED = [{"id": "seed1"}, {"id": "seed2"}]


def test_cases_for_uses_seed_when_not_full():
    assert ds.cases_for("nope", SEED, full=False) == SEED


def test_cases_for_falls_back_to_seed_when_uncached(monkeypatch):
    # full requested but nothing cached → seed set (never raises, never networks)
    monkeypatch.setattr(ds, "load_cached", lambda name, limit=None: None)
    assert ds.cases_for("nope", SEED, full=True) == SEED


def test_cases_for_uses_cached_when_full(monkeypatch):
    real = [{"id": "r1"}, {"id": "r2"}, {"id": "r3"}]
    monkeypatch.setattr(ds, "load_cached", lambda name, limit=None: real)
    assert ds.cases_for("x", SEED, full=True) == real


def test_dataset_limit_precedence(monkeypatch):
    monkeypatch.delenv("PRAX_EVAL_DATASET_LIMIT", raising=False)
    assert ds.dataset_limit(None) is None
    assert ds.dataset_limit(25) == 25
    monkeypatch.setenv("PRAX_EVAL_DATASET_LIMIT", "100")
    assert ds.dataset_limit(None) == 100
    assert ds.dataset_limit(25) == 25  # explicit still wins


def test_load_cached_reads_jsonl_and_caps(tmp_path, monkeypatch):
    import prax.eval as _e
    monkeypatch.setattr(_e, "PRAX_EVAL_DIR", tmp_path)
    monkeypatch.setattr(ds, "_cache_path",
                        lambda name: tmp_path / "datasets" / f"{name}.jsonl")
    p = tmp_path / "datasets" / "d.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("".join(json.dumps({"id": i}) + "\n" for i in range(10)))
    assert len(ds.load_cached("d")) == 10
    assert len(ds.load_cached("d", limit=3)) == 3


def test_full_datasets_enabled_env(monkeypatch):
    monkeypatch.delenv("PRAX_EVAL_FULL_DATASETS", raising=False)
    assert ds.full_datasets_enabled() is False
    monkeypatch.setenv("PRAX_EVAL_FULL_DATASETS", "1")
    assert ds.full_datasets_enabled() is True
