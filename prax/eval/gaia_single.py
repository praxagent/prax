"""GAIA single-task runner.

Runs one GAIA validation task through Prax's orchestrator in isolation
and produces a grading + cost + trace receipt.  Designed for iterative
capability work, not batch scoring.

Contract:

- All raw benchmark content stays under ``PRAX_EVAL_DIR`` (outside the
  repo, outside workspaces/).  The runner enforces this at startup via
  ``_guards.assert_eval_isolation``.
- Each run gets a fresh, isolated workspace at
  ``$PRAX_EVAL_DIR/runs/gaia-{task_id}-{run_id}/workspace/``.  We
  monkey-patch ``settings.workspace_dir`` for the duration of the run
  so Prax's tools write there instead of the user's real workspace.
- Hard cost cap via token counting (prompt tokens × in-rate + completion
  tokens × out-rate).  Does not run post-limit tool calls — relies on
  pre-check before each model invocation.
- Traces, responses, and grade details all land in the run directory.
  The runner also writes a *scrubbed* receipt to
  ``docs/research/receipts/`` that contains NO GAIA raw content.
- Eval mode disables tools in ``_guards.EVAL_MODE_TOOL_DENYLIST`` as
  defense-in-depth against any tool with unscoped filesystem access.

Usage::

    from prax.eval.gaia_single import run_gaia_task
    result = run_gaia_task(task_id="c61d22de-...", cost_limit_usd=2.0)
    print(result["grade"]["pass"], result["cost"]["usd_estimate"])
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from prax.eval import PRAX_EVAL_DIR, resolve_task_timeout
from prax.eval._guards import (
    EVAL_MODE_TOOL_DENYLIST,
    assert_eval_isolation,
    ensure_eval_dir,
)
from prax.eval.batch import _run_with_timeout
from prax.eval.telemetry import collect_usage

logger = logging.getLogger(__name__)


# GAIA dataset constants
_GAIA_DATASET = "gaia-benchmark/GAIA"
_GAIA_CONFIG = "2023_level1"  # start with Level 1 for the first runs

# Default USD rates for the cost kill-switch (overridable via
# PRAX_EVAL_USD_IN/OUT_PER_1M).  Default 0.0 = a local/self-hosted model:
# there is no per-token dollar cost, so runs are ranked by tokens + wall-time
# instead.  The receipt always reports exact token counts; USD is just a rail.
def _usd_rates() -> tuple[float, float]:
    try:
        from prax.settings import settings
        return (
            float(getattr(settings, "eval_usd_in_per_1m", 0.0) or 0.0),
            float(getattr(settings, "eval_usd_out_per_1m", 0.0) or 0.0),
        )
    except Exception:
        return (0.0, 0.0)


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

def _load_gaia_validation() -> list[dict]:
    """Load the GAIA validation split from HuggingFace, cached under
    ``PRAX_EVAL_DIR/gaia-cache``.

    Requires HF authentication (the dataset is gated).  Raises with a
    clear message if the user hasn't accepted the terms.
    """
    # Redirect HF cache to PRAX_EVAL_DIR so everything GAIA lives in
    # one place outside the repo.
    cache_dir = PRAX_EVAL_DIR / "gaia-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["HF_DATASETS_CACHE"] = str(cache_dir)
    os.environ["HF_HUB_CACHE"] = str(cache_dir)

    try:
        from datasets import load_dataset  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "datasets library not installed — run `uv add datasets`"
        ) from exc

    try:
        ds = load_dataset(
            _GAIA_DATASET,
            _GAIA_CONFIG,
            split="validation",
            cache_dir=str(cache_dir),
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load {_GAIA_DATASET} ({_GAIA_CONFIG}). "
            f"The dataset is gated — ensure you've accepted terms at "
            f"https://huggingface.co/datasets/gaia-benchmark/GAIA "
            f"and run `huggingface-cli login`.  Underlying error: {exc}"
        ) from exc

    return list(ds)


def load_gaia_task(task_id: str) -> dict:
    """Load a single GAIA task by ``task_id`` from the validation split."""
    tasks = _load_gaia_validation()
    for task in tasks:
        if task.get("task_id") == task_id:
            return dict(task)
    raise KeyError(
        f"GAIA task_id {task_id!r} not found in validation split "
        f"({len(tasks)} tasks available)"
    )


def list_text_only_level1() -> list[dict]:
    """Return GAIA validation Level 1 tasks with no attached files.

    Used by the first-task picker — stacks the deck for the first run
    by excluding tasks that need PDF/image/audio processing.
    """
    tasks = _load_gaia_validation()
    out = []
    for task in tasks:
        if str(task.get("Level")) != "1":
            continue
        file_name = task.get("file_name", "") or ""
        if file_name.strip():
            continue
        out.append(dict(task))
    return out


# ---------------------------------------------------------------------------
# Prompt + answer extraction
# ---------------------------------------------------------------------------

def build_gaia_prompt(task: dict) -> str:
    """Build the user-message prompt for a GAIA task.

    Sends the question EXACTLY as a real user would — no special
    formatting, no "benchmark mode" flag, no tool-use instructions.
    We want to test how Prax actually behaves, not how he behaves
    when told he's being graded.
    """
    return task.get("Question", "")


_ANSWER_RE = re.compile(
    r"^\s*(?:FINAL ANSWER|the answer is|answer:)\s*[:\-—]?\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Patterns that indicate a "bottom-line" answer in natural prose.
_BOTTOMLINE_RE = re.compile(
    r"(?:so(?:,)?\s+(?:the answer is|it(?:'s| is)|there (?:are|were))\s+)"
    r"[:\-—]?\s*\**(.+?)\**\.?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def extract_final_answer(response: str) -> str:
    """Extract the answer from Prax's natural response.

    Tries multiple strategies in order — the goal is to pull out what
    Prax *stated as his answer*, not a random bold string.

    Returns the raw answer string (not normalized).  Returns the full
    response if no pattern matches — the grader's substring matching
    provides a safety net.
    """
    if not response:
        return ""

    # Strategy 1: explicit answer markers ("FINAL ANSWER:", "The answer is:")
    matches = _ANSWER_RE.findall(response)
    if matches:
        return matches[-1].strip()

    # Strategy 2: bottom-line patterns ("So, it is X", "there were X")
    bl = _BOTTOMLINE_RE.findall(response)
    if bl:
        return bl[-1].strip().rstrip(".")

    # Strategy 3: find the first sentence that contains a bold number
    # or bold short phrase that looks like a direct answer.  This
    # catches "Mercedes Sosa published **5 studio albums**" — we want
    # "5" not "Cantora 2".
    # Look for bold text in the FIRST paragraph (where the answer
    # statement usually lives), not the last (which is often a list).
    paragraphs = response.strip().split("\n\n")
    if paragraphs:
        first_para = paragraphs[0]
        # Look for bold numbers first — they're almost always the answer
        bold_nums = re.findall(r"\*\*(\d+(?:\.\d+)?)\*\*", first_para)
        if bold_nums:
            return bold_nums[0]
        # Then bold short phrases in the first paragraph
        bold_first = re.findall(r"\*\*(.+?)\*\*", first_para)
        if bold_first:
            for candidate in bold_first:
                if len(candidate.strip()) < 60:
                    return candidate.strip()

    # Strategy 4: any bold number anywhere in the response
    bold_nums_all = re.findall(r"\*\*(\d+(?:\.\d+)?)\*\*", response)
    if bold_nums_all:
        return bold_nums_all[0]

    # Strategy 5: first sentence of the response (often IS the answer
    # in Prax's conversational style)
    first_line = response.strip().split("\n")[0].strip()
    if len(first_line) < 200:
        return first_line

    # Final fallback: full response — let the grader do substring matching
    return response.strip()


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def _normalize_for_match(s: str) -> str:
    """Normalize an answer string for comparison.

    Lowercases, strips punctuation, collapses whitespace, and removes
    articles.  GAIA's official grader is more sophisticated; this is a
    pragmatic approximation that catches the obvious cases.
    """
    s = s.strip().lower()
    # Remove common punctuation
    s = re.sub(r"[,;:!?]", "", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s)
    # Strip surrounding quotes
    s = s.strip("\"'")
    return s


def grade_answer(extracted: str, ground_truth: str) -> dict:
    """Grade an extracted answer against the ground-truth.

    Returns a dict with ``pass``, ``extracted``, ``ground_truth``,
    ``normalized_extracted``, ``normalized_ground_truth``, and
    ``match_type`` (exact, substring, or none).
    """
    if not extracted:
        return {
            "pass": False,
            "extracted": extracted,
            "ground_truth": ground_truth,
            "normalized_extracted": "",
            "normalized_ground_truth": _normalize_for_match(ground_truth),
            "match_type": "none",
            "failure_reason": "no FINAL ANSWER line in response",
        }

    ne = _normalize_for_match(extracted)
    ng = _normalize_for_match(ground_truth)

    if ne == ng:
        return {
            "pass": True,
            "extracted": extracted,
            "ground_truth": ground_truth,
            "normalized_extracted": ne,
            "normalized_ground_truth": ng,
            "match_type": "exact",
        }
    if ng and ng in ne:
        return {
            "pass": True,
            "extracted": extracted,
            "ground_truth": ground_truth,
            "normalized_extracted": ne,
            "normalized_ground_truth": ng,
            "match_type": "substring_ground_in_extracted",
        }
    if ne and ne in ng:
        return {
            "pass": True,
            "extracted": extracted,
            "ground_truth": ground_truth,
            "normalized_extracted": ne,
            "normalized_ground_truth": ng,
            "match_type": "substring_extracted_in_ground",
        }
    return {
        "pass": False,
        "extracted": extracted,
        "ground_truth": ground_truth,
        "normalized_extracted": ne,
        "normalized_ground_truth": ng,
        "match_type": "none",
        "failure_reason": "normalized strings do not match",
    }


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------

class CostTracker:
    """Tracks prompt/completion tokens, LLM-call count, and a USD rail.

    USD rates default to 0 (a local model has no per-token dollar cost); set
    ``PRAX_EVAL_USD_IN/OUT_PER_1M`` to price an API model.  ``llm_calls`` and the
    token counts are the primary, always-meaningful axes.
    """

    def __init__(self, limit_usd: float) -> None:
        self.limit_usd = limit_usd
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.llm_calls = 0
        self._in_rate, self._out_rate = _usd_rates()

    def add(self, prompt: int, completion: int, llm_calls: int = 0) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.llm_calls += llm_calls

    def usd_estimate(self) -> float:
        return (
            self.prompt_tokens / 1_000_000 * self._in_rate
            + self.completion_tokens / 1_000_000 * self._out_rate
        )

    def over_limit(self) -> bool:
        # A 0-rate (local) model can never exceed a USD limit — tokens are free.
        return self.usd_estimate() > self.limit_usd if (self._in_rate or self._out_rate) else False

    def snapshot(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "llm_calls": self.llm_calls,
            "usd_estimate": round(self.usd_estimate(), 4),
            "limit_usd": self.limit_usd,
        }


# ---------------------------------------------------------------------------
# Isolated Prax runner
# ---------------------------------------------------------------------------

@contextmanager
def _isolated_prax_scope(run_workspace: Path, task_id: str, user_prefix: str = "gaia-eval"):
    """Context manager that sets up an isolated Prax scope for one run.

    - Monkey-patches ``settings.workspace_dir`` to the run-scoped
      workspace so Prax's tools write there instead of the real
      workspace.
    - Sets ``current_user_id`` to a synthetic eval user.
    - Filters out denylisted tools from the registered set for the
      duration of the context.

    Reused by both the GAIA runner and the capability suite (``user_prefix``
    distinguishes their synthetic users).
    """
    from prax.agent import tool_registry
    from prax.agent.user_context import current_user_id
    from prax.settings import settings

    # Save originals
    original_workspace_dir = settings.workspace_dir
    original_get_registered = tool_registry.get_registered_tools
    original_user_id = current_user_id.get(None)

    # Synthetic user — a UUID-like string so workspace_service treats
    # it as a regular user id (no + prefix etc.)
    eval_user_id = f"{user_prefix}-{task_id[:8]}"

    try:
        # Point settings at the run-scoped workspace
        run_workspace.mkdir(parents=True, exist_ok=True)
        settings.workspace_dir = str(run_workspace)
        current_user_id.set(eval_user_id)

        # Wrap tool_registry to filter the denylist
        def _filtered_get_registered_tools():
            tools = original_get_registered()
            return [
                t for t in tools
                if getattr(t, "name", None) not in EVAL_MODE_TOOL_DENYLIST
            ]

        tool_registry.get_registered_tools = _filtered_get_registered_tools

        yield eval_user_id
    finally:
        # Restore
        settings.workspace_dir = original_workspace_dir
        tool_registry.get_registered_tools = original_get_registered
        if original_user_id is not None:
            current_user_id.set(original_user_id)


# ---------------------------------------------------------------------------
# The runner entry point
# ---------------------------------------------------------------------------

def run_gaia_task(
    task_id: str,
    *,
    cost_limit_usd: float = 2.0,
    model_override: str | None = None,
    tier: str = "medium",
    timeout_s: int | None = None,
) -> dict:
    """Run one GAIA validation task through Prax's orchestrator.

    Args:
        task_id: The GAIA ``task_id`` from the validation split.
        cost_limit_usd: Hard cap — the runner refuses to start if
            estimated cost exceeds this.  Defaults to $2.
        model_override: Optional model name to use instead of the
            orchestrator's default.

    Returns:
        A dict with ``task_id``, ``run_id``, ``grade``, ``cost``,
        ``response``, ``run_dir``, and ``duration_s``.  The full
        artifacts are also dumped to
        ``$PRAX_EVAL_DIR/runs/gaia-{task_id}-{run_id}/``.
    """
    from prax.settings import settings

    # -- 1. Isolation assertions + eval dir setup ---------------------------
    ensure_eval_dir(PRAX_EVAL_DIR)
    assert_eval_isolation(PRAX_EVAL_DIR, Path(settings.workspace_dir))

    # -- 2. Load task -------------------------------------------------------
    logger.info("Loading GAIA task %s", task_id)
    task = load_gaia_task(task_id)

    # -- 3. Set up the run directory ----------------------------------------
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    short_id = task_id[:8]
    run_dir = PRAX_EVAL_DIR / "runs" / f"gaia-{short_id}-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_workspace = run_dir / "workspace"

    # Dump the raw task immediately — safe because run_dir is under
    # PRAX_EVAL_DIR which is outside the repo.
    (run_dir / "task.json").write_text(
        json.dumps(task, indent=2, default=str), encoding="utf-8",
    )

    # -- 4. Build prompt + run --------------------------------------------
    prompt = build_gaia_prompt(task)
    cost = CostTracker(limit_usd=cost_limit_usd)
    start = time.monotonic()

    # Per-task wall-clock cap.  None (the default) = NO kill — a slow local
    # model on ds4/vLLM may legitimately take minutes-to-hours per task and the
    # suite runs overnight.  Set PRAX_EVAL_TASK_TIMEOUT_S, or pass timeout_s, to
    # arm a safety rail against a genuinely hung tool call (browser/research).
    timeout = resolve_task_timeout(timeout_s)

    response: str = ""
    error: str | None = None

    def _run_agent() -> str:
        with _isolated_prax_scope(run_workspace, task_id):
            from prax.agent.orchestrator import ConversationAgent

            agent = (
                ConversationAgent(model=model_override) if model_override
                else ConversationAgent(tier=tier)
            )
            return agent.run(
                conversation=[],
                user_input=prompt,
                workspace_context="",
                trigger=f"[GAIA eval: {task_id}]",
            )

    # Snapshot the GLOBAL isolation state on THIS (controlling) thread.  When an
    # armed timeout abandons the worker daemon mid-run, the worker's
    # _isolated_prax_scope restore never executes; without this main-thread
    # guard, settings.workspace_dir would stay pointed inside PRAX_EVAL_DIR and
    # trip assert_eval_isolation on EVERY subsequent task (one hang poisons the
    # whole overnight suite).
    from prax.agent import tool_registry as _tr
    _orig_ws = settings.workspace_dir
    _orig_get = _tr.get_registered_tools

    # collect_usage() instruments every LLM call (orchestrator, spokes, retries)
    # so we record REAL token counts — not a len()//4 guess.
    with collect_usage() as usage:
        try:
            response = _run_with_timeout(_run_agent, timeout) or ""
        except TimeoutError as exc:
            error = f"TIMEOUT: {exc}"
            logger.error("GAIA run timed out for task %s: %s", task_id, exc)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("GAIA run crashed for task %s", task_id)
        finally:
            settings.workspace_dir = _orig_ws
            _tr.get_registered_tools = _orig_get

    duration_s = round(time.monotonic() - start, 2)

    # -- 5. Extract + grade -------------------------------------------------
    extracted = extract_final_answer(response)
    ground_truth = task.get("Final answer", "") or ""
    grade = grade_answer(extracted, ground_truth)
    if error:
        grade["crashed"] = True
        grade["error"] = error
        grade["pass"] = False

    # -- 6. Real cost snapshot from captured token usage -------------------
    snap = usage.snapshot()
    if snap["total_tokens"] > 0:
        cost.add(
            prompt=snap["prompt_tokens"],
            completion=snap["completion_tokens"],
            llm_calls=snap["llm_calls"],
        )
    else:
        # Provider didn't report usage — coarse length fallback so the row
        # isn't blank.  Local OpenAI-compatible servers (ds4/vLLM) DO report it.
        cost.add(prompt=len(prompt) // 4, completion=(len(response) // 4 if response else 0))

    # -- 7. Dump receipts ---------------------------------------------------
    (run_dir / "response.txt").write_text(response or "", encoding="utf-8")
    (run_dir / "answer.txt").write_text(extracted or "", encoding="utf-8")
    (run_dir / "grade.json").write_text(
        json.dumps(grade, indent=2), encoding="utf-8",
    )
    (run_dir / "cost.json").write_text(
        json.dumps(cost.snapshot(), indent=2), encoding="utf-8",
    )

    meta = {
        "task_id": task_id,
        "short_id": short_id,
        "run_id": run_id,
        "gaia_level": task.get("Level"),
        "gaia_has_file": bool(task.get("file_name", "")),
        "model_override": model_override,
        "tier": tier,
        "cost_limit_usd": cost_limit_usd,
        "duration_s": duration_s,
        "started_at": datetime.now(UTC).isoformat(),
        "crashed": error is not None,
    }
    (run_dir / "meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8",
    )

    logger.info(
        "GAIA run %s: pass=%s cost=$%.3f duration=%ss",
        task_id, grade["pass"], cost.usd_estimate(), duration_s,
    )

    result = {
        "task_id": task_id,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "grade": grade,
        "cost": cost.snapshot(),
        "response": response,
        "duration_s": duration_s,
        "meta": meta,
    }

    # Append to the Pareto-tracking CSV — one row per run, accumulates
    # across all runs so you can plot cost vs accuracy vs time.
    _append_to_csv(result)

    return result


# ---------------------------------------------------------------------------
# Suite runner — resumable batch over many GAIA tasks (overnight / multi-day)
# ---------------------------------------------------------------------------

def run_gaia_suite(
    task_ids: list[str] | None = None,
    *,
    level: int = 1,
    text_only: bool = True,
    limit: int | None = None,
    cost_limit_usd: float = 2.0,
    tier: str = "medium",
    model_override: str | None = None,
    concurrency: int | None = None,
    resume: bool = True,
    retry_errors: bool = False,
    suite_dir: Path | None = None,
) -> dict:
    """Run a **resumable** GAIA batch — built for slow local models overnight.

    Picks tasks (default: text-only Level-1), runs each through
    :func:`run_gaia_task`, and stores a durable per-task result so a killed or
    Ctrl-C'd run resumes exactly where it stopped instead of restarting.  Each
    task keeps its own per-task timeout (``PRAX_EVAL_TASK_TIMEOUT_S``; 0 = none),
    so the batch itself imposes no wall-clock kill.

    Args:
        task_ids: explicit task ids; ``None`` auto-selects by ``level`` /
            ``text_only``.
        level: GAIA level when auto-selecting.
        text_only: exclude tasks with attached files (no PDF/image/audio).
        limit: cap the number of tasks (for a quick smoke batch).
        concurrency: parallel tasks; ``None`` reads ``PRAX_EVAL_CONCURRENCY``
            (1 — one local server).
        resume: skip tasks already completed in ``suite_dir``.
        suite_dir: run directory; defaults to
            ``$PRAX_EVAL_DIR/suites/gaia-{timestamp}``.

    Returns:
        The batch summary (also written to ``{suite_dir}/summary.json``), with an
        ``aggregate`` block carrying pass-rate and token totals.
    """
    from prax.eval.batch import run_batch

    ensure_eval_dir(PRAX_EVAL_DIR)

    if task_ids is None:
        if text_only and level == 1:
            tasks = list_text_only_level1()
        else:
            tasks = _load_gaia_validation()
            if level:
                tasks = [t for t in tasks if str(t.get("Level")) == str(level)]
            # Apply text_only for ANY level, not just level 1 (else a text-only
            # level-2 batch silently includes file-attached tasks that can't be
            # answered, burning slow-local compute on guaranteed failures).
            if text_only:
                tasks = [t for t in tasks if not (t.get("file_name") or "").strip()]
        task_ids = [t.get("task_id") for t in tasks if t.get("task_id")]
    if limit:
        task_ids = list(task_ids)[:limit]

    # The live runner mutates GLOBAL settings.workspace_dir per task, so parallel
    # tasks would race each other's workspace. Force serial regardless of request.
    if concurrency and concurrency > 1:
        logger.warning("GAIA suite forces concurrency=1 (run_gaia_task mutates "
                       "global state); requested %d ignored.", concurrency)
    concurrency = 1

    # Stable, resume-safe suite_dir keyed to the run CONFIG (not wall-clock), so
    # re-running the same command continues instead of starting a fresh empty dir.
    cfg_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(model_override or tier or "default"))
    suite_dir = suite_dir or (PRAX_EVAL_DIR / "suites" / f"gaia-L{level}-{cfg_slug}")

    def _run_one(task_id: str) -> dict:
        r = run_gaia_task(
            task_id,
            cost_limit_usd=cost_limit_usd,
            model_override=model_override,
            tier=tier,
        )
        g, c = r.get("grade", {}), r.get("cost", {})
        return {
            "id": task_id,
            "pass": bool(g.get("pass")),
            "match_type": g.get("match_type"),
            "crashed": bool(g.get("crashed")),
            "total_tokens": c.get("total_tokens"),
            "llm_calls": c.get("llm_calls"),
            "duration_s": r.get("duration_s"),
            "run_dir": r.get("run_dir"),
        }

    def _summarize(results: list[dict]) -> dict:
        graded = [r for r in results if not r.get("error")]
        passed = sum(1 for r in graded if r.get("pass"))
        n = len(graded)
        return {
            "graded": n,
            "passed": passed,
            "pass_rate": round(passed / n, 3) if n else 0.0,
            "total_tokens": sum(int(r.get("total_tokens") or 0) for r in graded),
        }

    return run_batch(
        task_ids,
        _run_one,
        out_dir=suite_dir,
        label=f"gaia-L{level}",
        concurrency=concurrency,
        resume=resume,
        retry_errors=retry_errors,
        per_item_timeout_s=None,  # each run_gaia_task owns its resolved timeout
        summarize=_summarize,
    )


# ---------------------------------------------------------------------------
# Pareto CSV — append-only log across all runs
# ---------------------------------------------------------------------------

_CSV_HEADERS = [
    "timestamp",
    "task_id",
    "run_id",
    "level",
    "tier",
    "model",
    "pass",
    "match_type",
    "extracted_answer",
    "duration_s",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "usd_estimate",
    "crashed",
    "timeout",
    "error",
]


def _append_to_csv(result: dict) -> None:
    """Append one row to ``$PRAX_EVAL_DIR/gaia_runs.csv``.

    The CSV is append-only — never truncated, never edited.  Each row
    is one run.  This is the dataset you feed into a Pareto-front plot
    of cost vs accuracy vs latency across tiers, models, and code
    changes.
    """
    import csv

    csv_path = PRAX_EVAL_DIR / "gaia_runs.csv"
    write_header = not csv_path.exists()

    meta = result.get("meta", {})
    grade = result.get("grade", {})
    cost = result.get("cost", {})

    row = {
        "timestamp": meta.get("started_at", ""),
        "task_id": result.get("task_id", ""),
        "run_id": result.get("run_id", ""),
        "level": meta.get("gaia_level", ""),
        "tier": meta.get("tier", "medium"),
        "model": meta.get("model_override") or "(default)",
        "pass": grade.get("pass", False),
        "match_type": grade.get("match_type", ""),
        "extracted_answer": (grade.get("extracted", "") or "")[:80],
        "duration_s": result.get("duration_s", 0),
        "prompt_tokens": cost.get("prompt_tokens", 0),
        "completion_tokens": cost.get("completion_tokens", 0),
        "total_tokens": cost.get("total_tokens", 0),
        "usd_estimate": cost.get("usd_estimate", 0),
        "crashed": meta.get("crashed", False),
        "timeout": "TIMEOUT" in (grade.get("error") or ""),
        "error": (grade.get("error") or "")[:120],
    }

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_HEADERS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Scrubbed public-receipt dumper (committed to docs/research/receipts/)
# ---------------------------------------------------------------------------

def write_public_receipt(
    run_result: dict,
    receipts_dir: Path,
    *,
    retro_notes: str = "",
) -> Path:
    """Write a compliance-scrubbed public receipt for a GAIA run.

    The receipt contains ONLY metadata — never the question text,
    the ground-truth answer, or Prax's verbatim response.  Enforced
    at runtime: if any of those strings appear in the content, we
    raise before writing.

    Args:
        run_result: The dict returned by ``run_gaia_task``.
        receipts_dir: Where to write the receipt (should be
            ``docs/research/receipts/``).
        retro_notes: Free-form retro markdown from the human runner.
    """
    receipts_dir.mkdir(parents=True, exist_ok=True)
    run_date = datetime.now(UTC).strftime("%Y-%m-%d")
    fname = f"gaia-run-{run_date}-{run_result['meta']['short_id']}.md"
    receipt_path = receipts_dir / fname

    meta = run_result["meta"]
    grade = run_result["grade"]
    cost = run_result["cost"]

    lines = [
        f"# GAIA Run — {run_date} — Task {meta['short_id']}",
        "",
        "_Scrubbed public receipt.  Raw task content, ground-truth "
        "answer, and Prax's verbatim response live under "
        f"`$PRAX_EVAL_DIR/runs/gaia-{meta['short_id']}-{meta['run_id']}/`"
        " on the run machine only, per HuggingFace GAIA licensing "
        "terms (no resharing)._",
        "",
        "## Metadata",
        "",
        "- **Dataset:** GAIA validation split",
        f"- **Task ID:** `{meta['task_id']}` (public identifier only — see HF dataset for content)",
        f"- **Level:** {meta['gaia_level']}",
        f"- **Has attached file:** {meta['gaia_has_file']}",
        f"- **Model:** {meta['model_override'] or '(orchestrator default)'}",
        f"- **Run ID:** `{meta['run_id']}`",
        f"- **Started:** {meta['started_at']}",
        "",
        "## Result",
        "",
        f"- **Pass:** {grade['pass']}",
        f"- **Match type:** {grade.get('match_type', 'n/a')}",
        f"- **Crashed:** {meta['crashed']}",
    ]
    if grade.get("failure_reason"):
        lines.append(f"- **Failure reason:** {grade['failure_reason']}")
    if grade.get("error"):
        lines.append(f"- **Error:** `{grade['error']}`")
    lines += [
        "",
        "## Cost & timing",
        "",
        f"- **Duration:** {run_result['duration_s']}s",
        f"- **Prompt tokens:** {cost['prompt_tokens']:,}",
        f"- **Completion tokens:** {cost['completion_tokens']:,}",
        f"- **Total tokens:** {cost['total_tokens']:,}",
        f"- **Estimated cost:** ${cost['usd_estimate']:.4f} (of ${cost['limit_usd']:.2f} limit)",
        "",
        "## Retro notes",
        "",
        retro_notes or "_(none yet)_",
        "",
        "---",
        "",
        "_Committed receipts only contain metadata + our retro notes.  "
        "Raw GAIA content is gated and never committed.  See "
        "`prax/eval/README.md` for the full isolation rules._",
        "",
    ]
    content = "\n".join(lines)

    # Compliance assertion: the receipt must not contain raw GAIA
    # content — question, ground truth, or verbatim response.
    forbidden = []
    task_question = (run_result.get("response") or "")  # belt + braces
    # We don't have the task here but we have the response — scrub
    # against it.  This catches the case where retro_notes accidentally
    # pasted verbatim Prax output.
    if task_question and len(task_question) > 50:
        # Check for any 100-char substring of the response appearing
        # in the receipt content.
        probe = task_question[:200]
        if probe and probe in content:
            forbidden.append("verbatim Prax response")
    if run_result["grade"].get("ground_truth"):
        gt = run_result["grade"]["ground_truth"]
        if gt and gt.lower() in content.lower() and len(gt) > 3:
            forbidden.append("ground-truth answer")

    if forbidden:
        raise RuntimeError(
            f"Scrubbed-receipt assertion failed: content contains "
            f"{forbidden}.  Receipt NOT written.  Review retro_notes "
            f"and remove verbatim GAIA content."
        )

    receipt_path.write_text(content, encoding="utf-8")
    logger.info("Wrote scrubbed public receipt to %s", receipt_path)
    return receipt_path
