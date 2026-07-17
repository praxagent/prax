"""ARC-AGI-3 interactive harness — play the games, score by RHAE.

Unlike the static benchmark adapters (`prax/eval/benchmarks/`), ARC-AGI-3 is
*interactive*: an agent loops observe→decide-action→step through a live game until
it solves each level, scored by **RHAE** (Relative Human Action Efficiency). This
package is the harness; a real run needs `ARC_API_KEY` (from three.arcprize.org)
and the `arc-agi-3` SDK. Everything here is keyless-testable via `MockGame`.

Design maps 1:1 onto the "executable world-models" capability
(`docs/research/executable-world-models.md`): the agent induces a model of the
game, verifies it against observed transitions, and plans efficient actions.
"""
from prax.eval.arc3.game import Action, Frame, GameState, MockGame
from prax.eval.arc3.rhae import level_rhae, summarize_rhae
from prax.eval.arc3.runner import play_game

__all__ = [
    "Action", "Frame", "GameState", "MockGame",
    "level_rhae", "summarize_rhae", "play_game",
]
