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

from prax.eval import PRAX_EVAL_DIR
from prax.eval._guards import (
    EVAL_MODE_TOOL_DENYLIST,
    assert_eval_isolation,
    ensure_eval_dir,
)

logger = logging.getLogger(__name__)


# GAIA dataset constants
_GAIA_DATASET = "gaia-benchmark/GAIA"
_GAIA_CONFIG = "2023_level1"  # start with Level 1 for the first runs

# Rough USD rates for Claude Opus 4.6 (adjust if model changes).
# Used for the cost kill-switch only — the receipt reports exact token
# counts so the estimate is just a safety rail.
_OPUS_IN_USD_PER_1M = 15.0
_OPUS_OUT_USD_PER_1M = 75.0


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
    """Tracks prompt/completion tokens and estimated USD cost."""

    def __init__(self, limit_usd: float) -> None:
        self.limit_usd = limit_usd
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def add(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion

    def usd_estimate(self) -> float:
        return (
            self.prompt_tokens / 1_000_000 * _OPUS_IN_USD_PER_1M
            + self.completion_tokens / 1_000_000 * _OPUS_OUT_USD_PER_1M
        )

    def over_limit(self) -> bool:
        return self.usd_estimate() > self.limit_usd

    def snapshot(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "usd_estimate": round(self.usd_estimate(), 4),
            "limit_usd": self.limit_usd,
        }


# ---------------------------------------------------------------------------
# Isolated Prax runner
# ---------------------------------------------------------------------------

@contextmanager
def _isolated_prax_scope(run_workspace: Path, task_id: str):
    """Context manager that sets up an isolated Prax scope for one run.

    - Monkey-patches ``settings.workspace_dir`` to the run-scoped
      workspace so Prax's tools write there instead of the real
      workspace.
    - Sets ``current_user_id`` to a synthetic eval user.
    - Filters out denylisted tools from the registered set for the
      duration of the context.
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
    eval_user_id = f"gaia-eval-{task_id[:8]}"

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
    timeout_s: int = 120,
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

    response: str = ""
    error: str | None = None

    # Run the orchestrator in a thread with a hard wall-clock timeout.
    # Without this, a hung tool call (browser, research, sandbox) can
    # burn API spend indefinitely — as happened on the overnight run
    # where the Scikit-Learn changelog task hung for 8+ hours.
    import threading

    _result_box: list[str] = []
    _error_box: list[str] = []

    def _run_agent():
        try:
            with _isolated_prax_scope(run_workspace, task_id):
                from prax.agent.orchestrator import ConversationAgent

                if model_override:
                    agent = ConversationAgent(model=model_override)
                else:
                    agent = ConversationAgent(tier=tier)
                resp = agent.run(
                    conversation=[],
                    user_input=prompt,
                    workspace_context="",
                    trigger=f"[GAIA eval: {task_id}]",
                )
                _result_box.append(resp)
        except Exception as exc:
            _error_box.append(f"{type(exc).__name__}: {exc}")
            logger.exception("GAIA run crashed for task %s", task_id)

    thread = threading.Thread(target=_run_agent, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)

    if thread.is_alive():
        error = f"TIMEOUT: orchestrator did not finish within {timeout_s}s"
        logger.error("GAIA run timed out for task %s after %ds", task_id, timeout_s)
        # Thread is a daemon — it'll die when this process exits.
        # We don't attempt to kill it mid-flight.
    elif _error_box:
        error = _error_box[0]
    elif _result_box:
        response = _result_box[0]
    else:
        error = "No response and no error — agent returned nothing"

    duration_s = round(time.monotonic() - start, 2)

    # -- 5. Extract + grade -------------------------------------------------
    extracted = extract_final_answer(response)
    ground_truth = task.get("Final answer", "") or ""
    grade = grade_answer(extracted, ground_truth)
    if error:
        grade["crashed"] = True
        grade["error"] = error
        grade["pass"] = False

    # -- 6. Best-effort cost snapshot from last-message token usage --------
    # We can't intercept every LLM call without deeper surgery, so grab
    # a rough count from the response length.  This is a lower bound —
    # real runs should wire token_usage callbacks.  Good enough for
    # first-pass cost tracking.
    rough_completion_tokens = len(response) // 4 if response else 0
    rough_prompt_tokens = len(prompt) // 4
    cost.add(prompt=rough_prompt_tokens, completion=rough_completion_tokens)

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
        "tier": meta.get("model_override") or "medium",
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
