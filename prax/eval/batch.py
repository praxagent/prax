"""Resumable, long-horizon batch eval runner.

The single-task GAIA runner can't survive an overnight or multi-day run on a
slow local model (ds4 / vLLM / Ollama): one crash at task 40/165 and you start
over, and its 120-second wall-clock kill murders any task that takes minutes.

This runner fixes both, generically — it knows nothing about GAIA or capability
cases; it just maps a ``run_one`` callable over a list of items with:

- **Idempotent resume.** Each result lands in ``{out_dir}/results/{id}.json``.
  Re-invoking skips every id already on disk, so a killed run resumes exactly
  where it stopped.  ``retry_errors=True`` re-runs only the ones that errored.
- **Crash isolation.** A task that raises is recorded as an error result and
  the batch keeps going — one bad task never sinks the run.
- **Generous / disabled timeouts.** ``per_item_timeout_s=None`` (the default)
  means *no* wall-clock kill — correct for a model generating at 10 t/s.  Set a
  number only when you want a safety rail.
- **Interrupt-safe.** Ctrl-C during an overnight run writes the summary and
  exits cleanly; every completed task is already durably on disk.
- **Live progress.** Each completion appends to ``{out_dir}/progress.jsonl`` and
  refreshes ``{out_dir}/summary.json`` so you can ``tail -f`` an overnight run.

Concurrency defaults to 1 — a single local model server is the common case, and
serial execution keeps a slow GPU box from thrashing.  Bump it for API models.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _id_of(item: Any) -> str:
    """Best-effort stable id for an item (str as-is, dict via ``id``/``task_id``)."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("id", "task_id", "name", "key"):
            if item.get(key):
                return str(item[key])
    return str(item)


def _completed_ids(results_dir: Path, *, retry_errors: bool) -> set[str]:
    """Return the set of ids already done (valid result on disk).

    With ``retry_errors`` set, results carrying an ``error`` are treated as not
    done so the next run re-attempts only the failures.
    """
    done: set[str] = set()
    if not results_dir.exists():
        return done
    for path in results_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue  # corrupt/partial write — re-run it
        if retry_errors and isinstance(data, dict) and data.get("error"):
            continue
        done.add(path.stem)
    return done


def _run_with_timeout(fn: Callable[[], Any], timeout_s: float | None) -> Any:
    """Run ``fn`` with an optional wall-clock timeout.

    ``timeout_s=None`` runs to completion (the default — a slow local model may
    legitimately take an hour).  On timeout we raise ``TimeoutError`` and abandon
    the work in a **daemon** thread: we never block waiting for a hung task, and
    the thread dies with the process (Python can't force-kill a thread).  This is
    the documented behaviour of the original GAIA runner.
    """
    if timeout_s is None:
        return fn()
    box: dict[str, Any] = {}

    def _wrapped() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller thread
            box["error"] = exc

    thread = threading.Thread(target=_wrapped, daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        # The task IS abandoned here (we return control to the caller), but the
        # daemon worker keeps running until whatever it's blocked on returns —
        # Python can't force-kill a thread. So a py-spy taken later may still
        # show this worker deep in a blocking call; that is a LEAKED-but-
        # abandoned thread, NOT evidence the timeout failed to fire. Bound the
        # blocking op itself (e.g. WEB_SEARCH_TIMEOUT_S) to cap the leak.
        logger.warning(
            "task exceeded %ss wall-clock limit — abandoning; the worker thread "
            "may keep running until its current blocking call returns", timeout_s,
        )
        raise TimeoutError(f"task exceeded {timeout_s}s wall-clock limit")
    if "error" in box:
        raise box["error"]
    return box.get("result")


def run_batch(
    items: Sequence[Any],
    run_one: Callable[[Any], dict],
    *,
    out_dir: str | Path,
    label: str = "batch",
    concurrency: int = 1,
    resume: bool = True,
    retry_errors: bool = False,
    per_item_timeout_s: float | None = None,
    summarize: Callable[[list[dict]], dict] | None = None,
    on_result: Callable[[str, dict], None] | None = None,
) -> dict:
    """Map ``run_one`` over ``items``, durably and resumably.

    Args:
        items: tasks to run (strings, or dicts with an ``id``/``task_id`` key).
        run_one: ``run_one(item) -> dict`` — runs one task, returns a
            JSON-serializable result.  Should be deterministic per id.
        out_dir: run directory; results go to ``{out_dir}/results/{id}.json``.
        label: human label for logs/summary.
        concurrency: parallel workers (1 = serial, right for one local server).
        resume: skip ids already completed on disk.
        retry_errors: when resuming, re-run ids whose stored result errored.
        per_item_timeout_s: per-task wall-clock cap; ``None`` = no limit.
        summarize: optional ``(results) -> dict`` for domain aggregates
            (e.g. GAIA pass-rate); merged into the summary under ``aggregate``.
        on_result: optional ``(id, result)`` callback fired as each completes.

    Returns:
        A summary dict (also written to ``{out_dir}/summary.json``).
    """
    out_dir = Path(out_dir)
    results_dir = out_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    progress_path = out_dir / "progress.jsonl"

    done = _completed_ids(results_dir, retry_errors=retry_errors) if resume else set()
    pending = [it for it in items if _id_of(it) not in done]

    total = len(items)
    logger.info(
        "Batch %s: %d total, %d already done, %d to run (concurrency=%d, timeout=%s)",
        label, total, len(done), len(pending), concurrency,
        per_item_timeout_s if per_item_timeout_s is not None else "none",
    )

    counter = {"done": len(done), "ok": 0, "err": 0}
    start = time.monotonic()

    def _execute(item: Any) -> tuple[str, dict]:
        item_id = _id_of(item)
        t0 = time.monotonic()
        try:
            result = _run_with_timeout(lambda: run_one(item), per_item_timeout_s)
            if not isinstance(result, dict):
                result = {"result": result}
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}", "id": item_id}
            logger.warning("Batch %s: task %s errored: %s", label, item_id, exc)
        result.setdefault("id", item_id)
        result.setdefault("duration_s", round(time.monotonic() - t0, 2))
        # Durable, atomic-ish write: temp then replace.
        dest = results_dir / f"{item_id}.json"
        tmp = dest.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        tmp.replace(dest)
        return item_id, result

    def _refresh_summary(interrupted: bool = False) -> dict:
        """Recompute summary.json from disk (atomic write) so `tail`/a glance
        sees a live aggregate mid-run, not last session's stale numbers."""
        summary = _build_summary(
            out_dir, label=label, total=total, counter=counter,
            elapsed_s=round(time.monotonic() - start, 2),
            interrupted=interrupted, summarize=summarize,
        )
        tmp = out_dir / "summary.json.tmp"
        tmp.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        tmp.replace(out_dir / "summary.json")
        return summary

    def _record(item_id: str, result: dict) -> None:
        counter["done"] += 1
        if result.get("error"):
            counter["err"] += 1
        else:
            counter["ok"] += 1
        line = {
            "ts": datetime.now(UTC).isoformat(),
            "id": item_id,
            "ok": not result.get("error"),
            "duration_s": result.get("duration_s"),
            "done": counter["done"],
            "total": total,
        }
        with open(progress_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, default=str) + "\n")
        if on_result is not None:
            try:
                on_result(item_id, result)
            except Exception:
                logger.debug("on_result callback failed", exc_info=True)
        # Keep summary.json live without rereading every result on every task.
        if counter["done"] % 10 == 0:
            _refresh_summary()
        logger.info("Batch %s: %d/%d done (%s)", label, counter["done"], total, item_id)

    interrupted = False
    if concurrency <= 1:
        try:
            for item in pending:
                item_id, result = _execute(item)
                _record(item_id, result)
        except KeyboardInterrupt:
            interrupted = True
            logger.warning("Batch %s: interrupted — %d/%d done, resume to continue",
                           label, counter["done"], total)
    else:
        # submit + as_completed so (a) Ctrl-C cancels the not-yet-started tasks
        # instead of draining the whole queue, and (b) progress records the
        # moment each task finishes, not in submission order (no head-of-line lag).
        pool = ThreadPoolExecutor(max_workers=concurrency)
        futures = [pool.submit(_execute, it) for it in pending]
        try:
            for fut in as_completed(futures):
                item_id, result = fut.result()
                _record(item_id, result)
        except KeyboardInterrupt:
            interrupted = True
            for f in futures:
                f.cancel()
            logger.warning("Batch %s: interrupted — cancelled pending, %d/%d done",
                           label, counter["done"], total)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    return _refresh_summary(interrupted)


def load_results(out_dir: str | Path) -> list[dict]:
    """Load every stored per-task result from a batch run directory."""
    results_dir = Path(out_dir) / "results"
    out: list[dict] = []
    if not results_dir.exists():
        return out
    for path in sorted(results_dir.glob("*.json")):
        try:
            out.append(json.loads(path.read_text()))
        except Exception:
            continue
    return out


def _build_summary(out_dir: Path, *, label: str, total: int, counter: dict,
                   elapsed_s: float, interrupted: bool,
                   summarize: Callable[[list[dict]], dict] | None) -> dict:
    results = load_results(out_dir)
    summary = {
        "label": label,
        "out_dir": str(out_dir),
        "total": total,
        "completed": len(results),
        "ok": sum(1 for r in results if not r.get("error")),
        "errored": sum(1 for r in results if r.get("error")),
        "interrupted": interrupted,
        "elapsed_s_this_session": elapsed_s,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    # Reproducibility: pin the exact flags/models/commit this run used, so the
    # published number can't be reproduced under a different config and called a
    # cheat. Secret-free by construction. Best-effort — never break a run.
    try:
        from prax.eval.config_snapshot import eval_config_snapshot
        summary["config"] = eval_config_snapshot()
    except Exception:
        logger.debug("config snapshot failed", exc_info=True)
    if summarize is not None:
        try:
            summary["aggregate"] = summarize(results)
            # Statistical honesty: every pass-rate carries its 95% CI so
            # small-subset numbers can't be over-read (see prax/eval/stats.py).
            from prax.eval.stats import attach_ci
            if isinstance(summary["aggregate"], dict):
                attach_ci(summary["aggregate"])
        except Exception as exc:
            summary["aggregate_error"] = str(exc)
    return summary
