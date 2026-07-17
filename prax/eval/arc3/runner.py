"""Play an ARC-AGI-3 game with an agent, tracking counted actions for RHAE.

The agent is any callable ``agent(frame, history) -> Action`` — a scripted policy
(for tests), a random policy, or the LLM/world-model agent (`agent.py`). The
runner is game-agnostic: it works against `MockGame` or the real SDK client, as
long as they expose ``reset()``, ``step(action)``, and ``human_baseline(level)``.
"""
from __future__ import annotations

from collections.abc import Callable

from prax.eval.arc3.game import Action, Frame, GameState
from prax.eval.arc3.rhae import summarize_rhae

Agent = Callable[[Frame, list], Action]


def play_game(game, agent: Agent, *, max_actions_per_level: int = 300,
              max_resets: int = 5) -> dict:
    """Run one game to WIN/GAME_OVER or budget exhaustion.

    Returns ``{game_id, summary(rhae/levels/solved), actions, resets}``. A level
    that isn't solved within ``max_actions_per_level`` counted actions is recorded
    unsolved (RHAE 0) and the game ends. RESET is free (doesn't count) but bounded
    by ``max_resets`` to stop reset-loops.
    """
    frame = game.reset()
    history: list[Action] = []
    per_level: list[dict] = []
    level = 0
    counted = 0          # counted (non-RESET) actions in the current level
    resets = 0           # resets in the CURRENT level (bounds reset-loops)
    total_resets = 0     # resets across the whole game (reported)
    total_actions = 0
    hb = getattr(game, "human_baseline", lambda _l: 1)

    while frame.state in (GameState.NOT_FINISHED, GameState.NOT_STARTED):
        action = agent(frame, history)
        history.append(action)
        total_actions += 1
        prev_completed = frame.levels_completed

        frame = game.step(action)

        if action is Action.RESET:
            resets += 1
            total_resets += 1
            counted = 0
            if resets > max_resets:
                per_level.append({"level": level, "human": hb(level),
                                  "ai": counted, "solved": False})
                break
            continue

        counted += 1

        if frame.levels_completed > prev_completed:
            # The level we were on is solved; record its counted action total.
            per_level.append({"level": level, "human": hb(level),
                              "ai": counted, "solved": True})
            level += 1
            counted = 0
            resets = 0
            if frame.state is GameState.WIN:
                break
            continue

        if frame.state is GameState.GAME_OVER:
            per_level.append({"level": level, "human": hb(level),
                              "ai": counted, "solved": False})
            break

        if counted >= max_actions_per_level:
            per_level.append({"level": level, "human": hb(level),
                              "ai": counted, "solved": False})
            break

    return {
        "game_id": getattr(game, "game_id", "unknown"),
        "summary": summarize_rhae(per_level),
        "actions": total_actions,
        "resets": total_resets,
    }
