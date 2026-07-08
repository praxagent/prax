"""Key-free tests for the resumable batch runner (prax.eval.batch)."""
from __future__ import annotations

import time

import pytest

from prax.eval.batch import _run_with_timeout, load_results, run_batch


def test_runs_all_and_writes_durable_results(tmp_path):
    def run_one(x):
        return {"x": x, "ok": True}

    summary = run_batch(["a", "b", "c"], run_one, out_dir=tmp_path, label="t")

    assert summary["total"] == 3
    assert summary["completed"] == 3
    assert summary["ok"] == 3
    assert summary["errored"] == 0
    assert (tmp_path / "results" / "a.json").exists()
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "progress.jsonl").exists()


def test_resume_skips_completed(tmp_path):
    calls: list[str] = []

    def run_one(x):
        calls.append(x)
        return {"x": x}

    run_batch(["a", "b"], run_one, out_dir=tmp_path)
    calls.clear()
    # Re-run with an extra item: only the new one should execute.
    run_batch(["a", "b", "c"], run_one, out_dir=tmp_path)
    assert calls == ["c"]


def test_errors_are_recorded_and_do_not_stop_the_batch(tmp_path):
    def run_one(x):
        if x == "b":
            raise ValueError("boom")
        return {"x": x}

    summary = run_batch(["a", "b", "c"], run_one, out_dir=tmp_path)
    assert summary["ok"] == 2
    assert summary["errored"] == 1
    by_id = {r["id"]: r for r in load_results(tmp_path)}
    assert "error" in by_id["b"]
    assert "ValueError" in by_id["b"]["error"]


def test_retry_errors_reruns_only_failures(tmp_path):
    state = {"fail_b": True}

    def run_one(x):
        if x == "b" and state["fail_b"]:
            raise ValueError("boom")
        return {"x": x}

    run_batch(["a", "b"], run_one, out_dir=tmp_path)
    state["fail_b"] = False
    calls: list[str] = []

    def run_one2(x):
        calls.append(x)
        return {"x": x}

    run_batch(["a", "b"], run_one2, out_dir=tmp_path, retry_errors=True)
    assert calls == ["b"]  # 'a' already ok, only the errored 'b' re-runs


def test_dict_items_use_id_field(tmp_path):
    items = [{"task_id": "t1", "q": "?"}, {"task_id": "t2", "q": "?"}]
    run_batch(items, lambda it: {"seen": it["task_id"]}, out_dir=tmp_path)
    assert (tmp_path / "results" / "t1.json").exists()
    assert (tmp_path / "results" / "t2.json").exists()


def test_summarize_hook_populates_aggregate(tmp_path):
    def summarize(results):
        return {"sum": sum(r["val"] for r in results)}

    summary = run_batch(
        ["a", "b"], lambda x: {"val": 2}, out_dir=tmp_path, summarize=summarize,
    )
    assert summary["aggregate"]["sum"] == 4


def test_timeout_none_runs_to_completion():
    assert _run_with_timeout(lambda: 42, None) == 42


def test_timeout_propagates_inner_exception():
    with pytest.raises(ValueError, match="inner"):
        _run_with_timeout(lambda: (_ for _ in ()).throw(ValueError("inner")), 5)


def test_timeout_fires_without_blocking():
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        _run_with_timeout(lambda: time.sleep(5), 0.2)
    # The daemon-thread design must NOT block until the 5s sleep finishes.
    assert time.monotonic() - start < 2.0


def test_timeout_logs_abandonment(caplog):
    # A fired timeout must log that the worker is ABANDONED (may keep running) —
    # so a later py-spy of the leaked thread isn't misread as "the timeout never
    # fired" (the 2026-07 eval-timeout misdiagnosis).
    import logging
    with caplog.at_level(logging.WARNING), pytest.raises(TimeoutError):
        _run_with_timeout(lambda: time.sleep(5), 0.2)
    assert any("abandoning" in r.message for r in caplog.records)


def test_concurrency_records_every_result(tmp_path):
    # The concurrent path (submit + as_completed) must record all results, not
    # just those finishing in submission order.
    summary = run_batch(list("abcdef"), lambda x: {"x": x}, out_dir=tmp_path, concurrency=4)
    assert summary["completed"] == 6
    assert summary["ok"] == 6
    assert summary["errored"] == 0


def test_summary_carries_out_dir(tmp_path):
    summary = run_batch(["a"], lambda x: {"x": x}, out_dir=tmp_path)
    assert summary["out_dir"] == str(tmp_path)
