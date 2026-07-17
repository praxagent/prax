"""Key-free tests for the ARC-AGI-3 harness core (mock game + runner + RHAE)."""
from __future__ import annotations

from prax.eval.arc3 import Action, MockGame, play_game
from prax.eval.arc3.rhae import level_rhae


def _perfect_agent(frame, history):
    """Move the cursor (colour 4) toward the target (colour 3) optimally."""
    cur = target = None
    for r, row in enumerate(frame.grid):
        for c, v in enumerate(row):
            if v == 4:
                cur = (r, c)
            elif v == 3:
                target = (r, c)
    if cur is None or target is None:
        return Action.ACTION1
    (cr, cc), (tr, tc) = cur, target
    if cr < tr:
        return Action.ACTION2  # down
    if cr > tr:
        return Action.ACTION1  # up
    if cc < tc:
        return Action.ACTION4  # right
    return Action.ACTION3      # left


def test_rhae_formula():
    assert level_rhae(4, 4) == 1.0            # optimal → 1.0
    assert level_rhae(4, 8) == 0.25           # 2× actions → (1/2)² = 0.25
    assert level_rhae(4, 2) == 1.0            # better-than-human capped at 1.0
    assert level_rhae(4, 0) == 0.0            # guard


def test_perfect_play_scores_rhae_1():
    res = play_game(MockGame(), _perfect_agent)
    assert res["summary"]["solved"] == 2      # both levels
    assert res["summary"]["rhae"] == 1.0      # optimal on every level


def test_wasteful_play_scores_below_1():
    # One wasted (clamped) up-move at the very start, then perfect.
    state = {"wasted": False}

    def wasteful(frame, history):
        if not state["wasted"]:
            state["wasted"] = True
            return Action.ACTION1  # up from row 0 → no-op but counts
        return _perfect_agent(frame, history)

    res = play_game(MockGame(), wasteful)
    assert res["summary"]["solved"] == 2
    assert 0.0 < res["summary"]["rhae"] < 1.0  # efficiency lost to the waste


def test_reset_is_free():
    # Reset once at the start, then play perfectly — RHAE should stay 1.0.
    state = {"reset": False}

    def reset_then_perfect(frame, history):
        if not state["reset"]:
            state["reset"] = True
            return Action.RESET
        return _perfect_agent(frame, history)

    res = play_game(MockGame(), reset_then_perfect)
    assert res["resets"] == 1
    assert res["summary"]["rhae"] == 1.0       # the reset didn't cost efficiency
