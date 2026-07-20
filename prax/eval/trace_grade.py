"""Grade a run's TRACE — the process — not just its final answer.

Most benchmarks score only the output: right letter, right number. But two runs
can reach the same answer by very different processes — one verifies a
load-bearing claim with a tool, the other hand-waves it; one commits within
budget, the other spirals to a timeout and never answers; one verifies in a
single call, the other burns six. Scoring only the answer is blind to all of
that (the ProofJudge "tests pass, nobody wants the PR" problem — correctness is
not the whole of value).

This grades the process against a small, GENERAL rubric — three quality axes that
matter on *any* task, so a benchmark case just attaches a ``trace_rubric``:

  - **committed**  — did the run produce a real, non-empty answer at all? (the
    GPQA non-commitment failure: reason for 13k tokens, emit nothing.)
  - **verification** — for a claim it *could* check with a tool, did it? (the
    "compute, don't estimate" axis — reaching for the CAS on a load-bearing fact
    instead of asserting it by hand.)
  - **efficiency** — did it stay within a token / tool-call budget, or verify the
    same thing six times? (the tool-economy axis — verifying is good, but once.)

Fully deterministic + keyless: it reads the run's captured metadata (answer,
tools used, tokens) — no judge model — so a trace grade is as un-gameable and
reproducible as the deterministic answer scorers.
"""
from __future__ import annotations

from typing import Any

# Default weights across the applicable axes. `verification` drops out (and the
# weights renormalise) when a case's rubric names no expected tools — i.e. when
# there's nothing tool-checkable to verify.
_WEIGHTS = {"committed": 0.4, "verification": 0.35, "efficiency": 0.25}

# Short strings a provider/harness returns *as the answer* when the call actually
# failed — such an "answer" is not a committed answer.
_NON_ANSWER_MARKERS = (
    "connect timeout", "try again later", "rate limit", "temporarily unavailable",
    "service unavailable", "too many requests", "(no answer)",
)


def _is_committed(answer: object, error: str) -> bool:
    """A committed answer is non-empty, not an error, not a transient-failure string."""
    if error:
        return False
    text = answer if isinstance(answer, str) else ("" if answer is None else str(answer))
    stripped = text.strip()
    if not stripped:
        return False
    if len(stripped) <= 400:  # only short blobs can be a bare error message
        low = stripped.lower()
        if any(m in low for m in _NON_ANSWER_MARKERS):
            return False
    return True


def _ratio_score(actual: float, budget: float) -> float:
    """1.0 when actual <= budget, decaying as budget/actual past it (never below 0)."""
    if budget <= 0 or actual <= budget:
        return 1.0
    return round(budget / actual, 3)


def grade_trace(run: dict[str, Any], rubric: dict[str, Any] | None = None) -> dict[str, Any]:
    """Grade the process captured in *run* against *rubric*.

    ``run`` carries the captured trace metadata (as from an eval ``CaseRun``):
        answer: str, tools: list[str], tokens: int, error: str
    ``rubric`` (all optional):
        expected_tools: list[str]  — tools that verify a load-bearing claim here;
            verification credit requires using at least one. Omit if nothing is
            tool-checkable (verification axis then doesn't apply).
        token_budget: int          — the "efficient" token ceiling (default 20000).
        ideal_max_tool_calls: int  — redundancy penalty past this (default 3).

    Returns ``{"trace_score", "criteria": {axis: {score, note}}, "summary"}`` with
    ``trace_score`` a weighted 0..1 over the applicable axes.
    """
    rubric = rubric or {}
    answer = run.get("answer", "")
    tools = list(run.get("tools") or [])
    tokens = int(run.get("tokens") or 0)
    error = str(run.get("error") or "")

    expected = [t for t in (rubric.get("expected_tools") or [])]
    token_budget = int(rubric.get("token_budget", 20000))
    ideal_calls = int(rubric.get("ideal_max_tool_calls", 3))

    criteria: dict[str, dict[str, Any]] = {}

    # 1. committed --------------------------------------------------------------
    committed = _is_committed(answer, error)
    criteria["committed"] = {
        "score": 1.0 if committed else 0.0,
        "note": "answer committed" if committed
        else "NO committed answer (empty / error / transient failure)",
    }

    # 2. verification (only if the rubric names tool-checkable claims) -----------
    if expected:
        used = sorted({t for t in tools if t in expected})
        ok = bool(used)
        criteria["verification"] = {
            "score": 1.0 if ok else 0.0,
            "note": (f"used expected verification tool(s): {', '.join(used)}" if ok
                     else f"did NOT verify with a tool — expected one of {expected} "
                          f"(hand-asserted a checkable claim)"),
        }

    # 3. efficiency -------------------------------------------------------------
    tok_score = _ratio_score(tokens, token_budget)
    call_score = _ratio_score(len(tools), ideal_calls)
    eff = round((tok_score + call_score) / 2, 3)
    criteria["efficiency"] = {
        "score": eff,
        "note": (f"{tokens} tokens (budget {token_budget}); "
                 f"{len(tools)} tool call(s) (ideal <= {ideal_calls})"),
    }

    # Weighted overall over the axes that apply ---------------------------------
    weights = {k: w for k, w in _WEIGHTS.items() if k in criteria}
    total_w = sum(weights.values()) or 1.0
    trace_score = round(
        sum(criteria[k]["score"] * w for k, w in weights.items()) / total_w, 3)

    weakest = min(criteria.items(), key=lambda kv: kv[1]["score"])
    summary = (f"trace_score={trace_score}; weakest axis: {weakest[0]} "
               f"({weakest[1]['note']})")
    return {"trace_score": trace_score, "criteria": criteria, "summary": summary}


def grade_run(run: dict[str, Any], *, answer_grade: dict | None = None,
              trace_rubric: dict | None = None) -> dict[str, Any]:
    """Combine an ANSWER grade with a TRACE grade into one dual-axis result.

    ``answer_grade`` is whatever the answer scorer produced (``{"passed", "score",
    ...}`` — deterministic or judge). This just attaches the trace grade alongside
    it, so a benchmark reports *both* "was it right" and "was the process good".
    Kept separate on purpose: a run can be right-but-sloppy or well-reasoned-but-
    wrong, and collapsing them hides exactly the signal we want.
    """
    trace = grade_trace(run, trace_rubric)
    return {
        "answer": answer_grade or {},
        "trace": trace,
        "answer_score": float((answer_grade or {}).get("score", 0.0)),
        "trace_score": trace["trace_score"],
    }
