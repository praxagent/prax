"""RHAE — Relative Human Action Efficiency, the ARC-AGI-3 metric.

Per level: ``score = min(1.0, (human_baseline_actions / ai_actions)²)``. The
square makes it a power law — wasted actions cost quadratically, so only genuinely
efficient play scores high. A game's score is the mean across its levels; the run
score is the mean across games. RESET actions are NOT counted (they clear the
level's action sequence) — callers pass the *counted* action totals.
"""
from __future__ import annotations


def level_rhae(human_baseline_actions: int, ai_actions: int) -> float:
    """RHAE for a single level. ``ai_actions`` must exclude free RESETs."""
    if ai_actions <= 0:
        return 0.0
    ratio = human_baseline_actions / ai_actions
    return min(1.0, ratio * ratio)


def summarize_rhae(per_level: list[dict]) -> dict:
    """Aggregate per-level records into a game/run summary.

    Each record: ``{"level": i, "human": h, "ai": a, "solved": bool}``. Unsolved
    levels score 0. Returns mean RHAE + counts.
    """
    if not per_level:
        return {"rhae": 0.0, "levels": 0, "solved": 0}
    scores = []
    solved = 0
    for r in per_level:
        if r.get("solved"):
            solved += 1
            scores.append(level_rhae(r["human"], r["ai"]))
        else:
            scores.append(0.0)
    return {
        "rhae": sum(scores) / len(scores),
        "levels": len(per_level),
        "solved": solved,
        "per_level": scores,
    }
