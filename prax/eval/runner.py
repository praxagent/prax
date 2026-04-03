"""Eval runner — replay failure cases and score agent improvement.

Loads failure cases from the journal, replays the user input through
the current agent, and uses an LLM judge to score whether the original
failure mode has been addressed.

The runner operates in two modes:
  - **Single case**: replay one failure and return a verdict.
  - **Suite**: run all unresolved failures as a regression suite.

Each eval produces a :class:`EvalResult` with a pass/fail verdict,
a score (0.0–1.0), and the judge's reasoning. Results are persisted
alongside the failure journal so progress is tracked over time.

Usage::

    from prax.eval.runner import run_eval, run_eval_suite

    # Single case
    result = run_eval(case_id="abc123")
    print(result.passed, result.score, result.reasoning)

    # Full regression suite
    report = run_eval_suite(user_id="user1")
    print(report["passed"], report["failed"], report["score"])
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class EvalResult:
    """Result of evaluating a single failure case."""

    id: str = ""
    case_id: str = ""
    passed: bool = False
    score: float = 0.0  # 0.0 = total failure, 1.0 = perfect
    new_output: str = ""
    reasoning: str = ""
    judge_model: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _results_file() -> Path:
    try:
        from prax.settings import settings
        base = Path(settings.workspace_dir).resolve()
    except Exception:
        base = Path(".")
    d = base / ".prax" / "eval_results"
    d.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return d / f"results-{today}.jsonl"


def _append_result(result: EvalResult) -> None:
    try:
        line = json.dumps(asdict(result), default=str)
        with open(_results_file(), "a") as f:
            f.write(line + "\n")
    except Exception:
        logger.warning("Failed to persist eval result %s", result.id, exc_info=True)


def load_results(date: str | None = None, limit: int = 100) -> list[EvalResult]:
    """Load eval results, optionally filtered by date (YYYY-MM-DD)."""
    try:
        from prax.settings import settings
        base = Path(settings.workspace_dir).resolve()
    except Exception:
        base = Path(".")
    d = base / ".prax" / "eval_results"
    if not d.exists():
        return []

    results = []
    if date:
        files = [d / f"results-{date}.jsonl"]
    else:
        files = sorted(d.glob("results-*.jsonl"), reverse=True)

    for filepath in files:
        if not filepath.exists():
            continue
        try:
            for line in filepath.read_text().strip().splitlines():
                if line.strip():
                    results.append(EvalResult(**json.loads(line)))
        except Exception:
            continue
        if len(results) >= limit:
            break

    return results[:limit]


# ---------------------------------------------------------------------------
# LLM Judge
# ---------------------------------------------------------------------------

_JUDGE_PROMPT = """\
You are an eval judge for an AI agent. Your job is to determine whether a \
previously observed failure has been fixed.

## Original Failure

**User asked:** {user_input}

**Agent originally said:** {original_output}

**What went wrong (user feedback):** {feedback_comment}

**Failure category:** {failure_category}

## New Agent Output

{new_output}

## Instructions

Score the new output on a scale of 0.0 to 1.0:
- 1.0: The failure is completely fixed. The new output correctly handles what \
the user asked for, and the original failure mode is no longer present.
- 0.7-0.9: Substantially improved but minor issues remain.
- 0.4-0.6: Partially improved but the core failure is still partially present.
- 0.1-0.3: Marginal improvement at best.
- 0.0: The same failure or worse.

Respond with EXACTLY this JSON format (no other text):
{{"score": <float>, "passed": <bool>, "reasoning": "<1-2 sentences>"}}

A score >= 0.7 counts as passed."""


def _judge_output(
    user_input: str,
    original_output: str,
    new_output: str,
    feedback_comment: str,
    failure_category: str,
    tier: str = "low",
) -> tuple[float, bool, str, str]:
    """Use an LLM to judge whether the failure has been addressed.

    Returns (score, passed, reasoning, model_name).
    """
    from prax.agent.llm_factory import build_llm

    llm = build_llm(tier=tier, config_key="eval_judge")
    model_name = getattr(llm, "model_name", "") or getattr(llm, "model", "unknown")

    prompt = _JUDGE_PROMPT.format(
        user_input=user_input[:1000],
        original_output=original_output[:1000],
        new_output=new_output[:1000],
        feedback_comment=feedback_comment[:500],
        failure_category=failure_category or "unclassified",
    )

    try:
        response = llm.invoke(prompt)
        text = response.content if hasattr(response, "content") else str(response)

        # Parse JSON from response
        # Handle cases where LLM wraps JSON in markdown code blocks
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()

        data = json.loads(clean)
        score = float(data.get("score", 0.0))
        passed = bool(data.get("passed", score >= 0.7))
        reasoning = str(data.get("reasoning", ""))
        return score, passed, reasoning, model_name
    except Exception as e:
        logger.warning("LLM judge failed: %s", e)
        return 0.0, False, f"Judge error: {e}", model_name


# ---------------------------------------------------------------------------
# Replay — run the user's input through the current agent
# ---------------------------------------------------------------------------

def _replay_input(user_id: str, user_input: str) -> str:
    """Replay a user input through the conversation service.

    Returns the agent's new output. This is the expensive part — it
    makes real LLM API calls.
    """
    try:
        from prax.services.conversation_service import conversation_service

        # Use a dedicated eval conversation key to avoid polluting real history
        eval_key = hash(f"eval-{user_input[:100]}-{datetime.now(UTC).isoformat()}")
        response = conversation_service.reply(
            user_id,
            user_input,
            conversation_key=eval_key,
        )
        return response
    except Exception as e:
        logger.error("Replay failed: %s", e)
        return f"[REPLAY ERROR: {e}]"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_eval(
    case_id: str,
    replay: bool = True,
    judge_tier: str = "low",
) -> EvalResult:
    """Evaluate a single failure case.

    Args:
        case_id: The failure case ID to evaluate.
        replay: If True, replay the input through the agent. If False,
            only judge based on available data (cheaper but less accurate).
        judge_tier: LLM tier for the judge ("low", "medium", "high").

    Returns:
        An EvalResult with the verdict.
    """
    from prax.services.memory.failure_journal import get_failures

    # Find the failure case
    all_cases = get_failures()
    case = next((c for c in all_cases if c.id == case_id), None)
    if not case:
        return EvalResult(
            case_id=case_id,
            passed=False,
            score=0.0,
            reasoning=f"Failure case {case_id} not found",
        )

    # Replay the input through the current agent
    if replay and case.user_input:
        new_output = _replay_input(case.user_id, case.user_input)
    else:
        new_output = "(replay skipped — no user input or replay disabled)"

    # Judge the new output
    score, passed, reasoning, model = _judge_output(
        user_input=case.user_input,
        original_output=case.agent_output,
        new_output=new_output,
        feedback_comment=case.feedback_comment,
        failure_category=case.failure_category,
        tier=judge_tier,
    )

    result = EvalResult(
        case_id=case_id,
        passed=passed,
        score=score,
        new_output=new_output[:2000],
        reasoning=reasoning,
        judge_model=model,
    )
    _append_result(result)

    logger.info(
        "Eval %s: case=%s passed=%s score=%.2f — %s",
        result.id, case_id, passed, score, reasoning[:100],
    )
    return result


def run_eval_suite(
    user_id: str | None = None,
    unresolved_only: bool = True,
    replay: bool = True,
    judge_tier: str = "low",
    max_cases: int = 20,
) -> dict:
    """Run the full regression eval suite.

    Replays all (or filtered) failure cases and returns an aggregate report.

    Args:
        user_id: Filter cases by user. None runs all.
        unresolved_only: If True, only run unresolved cases.
        replay: Whether to replay inputs through the agent.
        judge_tier: LLM tier for the judge.
        max_cases: Maximum cases to evaluate (controls API cost).

    Returns:
        A summary dict with pass/fail counts, aggregate score, and per-case results.
    """
    from prax.services.memory.failure_journal import get_failures

    cases = get_failures(
        user_id=user_id,
        resolved=False if unresolved_only else None,
        limit=max_cases,
    )

    if not cases:
        return {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "score": 1.0,
            "results": [],
            "message": "No failure cases to evaluate.",
        }

    results: list[EvalResult] = []
    for case in cases:
        result = run_eval(
            case_id=case.id,
            replay=replay,
            judge_tier=judge_tier,
        )
        results.append(result)

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    avg_score = sum(r.score for r in results) / len(results) if results else 0.0

    report = {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "score": round(avg_score, 3),
        "pass_rate": round(passed / len(results), 3) if results else 0.0,
        "results": [asdict(r) for r in results],
    }

    logger.info(
        "Eval suite complete: %d/%d passed (%.1f%%), avg score=%.2f",
        passed, len(results), report["pass_rate"] * 100, avg_score,
    )
    return report
