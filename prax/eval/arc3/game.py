"""ARC-AGI-3 game types + a keyless mock game.

The real games come through the `arc-agi-3` SDK (see `client.py`); `MockGame`
mirrors the same observe/step interface with known-optimal solutions so the
harness + RHAE scoring are testable with no key and no network.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Action(enum.Enum):
    """The 7 standardized ARC-AGI-3 actions. Meaning is game-specific.

    In the real API these map to ACTION1..6 + RESET (+ ACTION7/Undo). ``ACTION6``
    is a click carrying (x, y) coordinates; the rest are parameterless.
    """
    RESET = "RESET"
    ACTION1 = "ACTION1"
    ACTION2 = "ACTION2"
    ACTION3 = "ACTION3"
    ACTION4 = "ACTION4"
    ACTION5 = "ACTION5"
    ACTION6 = "ACTION6"  # click (x, y)
    ACTION7 = "ACTION7"  # undo


class GameState(enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    NOT_FINISHED = "NOT_FINISHED"
    WIN = "WIN"
    GAME_OVER = "GAME_OVER"


@dataclass
class Frame:
    """One observation. Mirrors the SDK's ``FrameData`` fields we use."""
    grid: list[list[int]]                       # the current 64x64 (or NxM) frame
    available_actions: list[Action]             # legal moves right now
    state: GameState
    levels_completed: int = 0
    score: int = 0


@dataclass
class MockGame:
    """A tiny deterministic navigation game for testing the harness + RHAE.

    A cursor starts at (0,0); reach the target with ACTION1..4 (up/down/left/
    right). Solving a level = cursor on target. The **optimal** action count is
    the Manhattan distance — so a perfect agent scores RHAE 1.0 and a wasteful one
    scores less. RESET returns to start (and, per ARC-AGI-3 rules, does not count
    toward the scored action sequence). Multiple levels increase distance.
    """
    size: int = 8
    targets: list[tuple[int, int]] = field(default_factory=lambda: [(1, 2), (3, 4)])
    _cur: tuple[int, int] = (0, 0)
    _level: int = 0
    _actions_this_level: int = 0
    game_id: str = "mock_navigate"

    # Optimal (human-baseline) action count per level = Manhattan distance from
    # start to that level's target.
    def human_baseline(self, level: int) -> int:
        r, c = self.targets[level]
        return r + c

    def reset(self) -> Frame:
        self._cur = (0, 0)
        self._level = 0
        self._actions_this_level = 0
        return self._frame(GameState.NOT_FINISHED)

    def step(self, action: Action) -> Frame:
        if action is Action.RESET:
            # Restart the current level; resets are free (don't count).
            self._cur = (0, 0)
            self._actions_this_level = 0
            return self._frame(GameState.NOT_FINISHED)

        self._actions_this_level += 1
        r, c = self._cur
        if action is Action.ACTION1:      # up
            r = max(0, r - 1)
        elif action is Action.ACTION2:    # down
            r = min(self.size - 1, r + 1)
        elif action is Action.ACTION3:    # left
            c = max(0, c - 1)
        elif action is Action.ACTION4:    # right
            c = min(self.size - 1, c + 1)
        self._cur = (r, c)

        if self._cur == self.targets[self._level]:
            self._level += 1
            self._actions_this_level = 0
            if self._level >= len(self.targets):
                return self._frame(GameState.WIN)
            self._cur = (0, 0)  # next level starts fresh
        return self._frame(GameState.NOT_FINISHED)

    def _frame(self, state: GameState) -> Frame:
        grid = [[0] * self.size for _ in range(self.size)]
        if self._level < len(self.targets):
            tr, tc = self.targets[self._level]
            grid[tr][tc] = 3                    # target colour
        cr, cc = self._cur
        grid[cr][cc] = 4                        # cursor colour
        return Frame(
            grid=grid,
            available_actions=[Action.ACTION1, Action.ACTION2, Action.ACTION3,
                               Action.ACTION4, Action.RESET],
            state=state,
            levels_completed=self._level,
        )
