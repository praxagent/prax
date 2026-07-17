"""Real ARC-AGI-3 baseline: an LLM-driven agent over the live game API.

Lazy-imports the ``arc-agi-3`` SDK (so keyless CI + the mock harness never need
it). ``run_arc3_baseline`` opens a scorecard, plays each game with a Prax agent
whose ``choose_action`` asks an OpenRouter model for the next move, then closes the
scorecard and returns per-game RHAE.

This is the *floor* baseline — direct action selection, no world-model loop yet
(that's the executable-world-models upgrade). It mirrors ARC Prize's own
frontier-model baseline shape. Model is chosen via ``model=`` or ``LOW_MODEL`` env
(so ``CHEAP`` / ``OPENROUTER_EVAL_MODEL`` select it), matching the small-open-model
Kaggle regime.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

_HEX = "0123456789abcdef"
_SIMPLE_ACTIONS = {"RESET", "ACTION1", "ACTION2", "ACTION3", "ACTION4",
                   "ACTION5", "ACTION7"}


def _grid_to_hex(grid: object) -> str:
    """Render a 2D int grid as compact hex rows (0–15 → 0–f)."""
    if not isinstance(grid, list) or not grid:
        return "(empty)"
    # ARC-3 frames can be a stack of grids; take the last 2D grid.
    if grid and isinstance(grid[0], list) and grid[0] and isinstance(grid[0][0], list):
        grid = grid[-1]
    rows = []
    for row in grid:
        if not isinstance(row, list):
            return "(non-grid)"
        rows.append("".join(_HEX[v] if isinstance(v, int) and 0 <= v < 16 else "?"
                            for v in row))
    return "\n".join(rows)


def _llm_choose_action(grid_hex: str, available: list[str], history: list[str],
                       model: str, api_key: str, base_url: str) -> tuple[str, tuple[int, int] | None]:
    """Ask the model for the next action name (+ coords for ACTION6). Returns
    ``(action_name, coords_or_None)``; caller falls back to random on ('', None)."""
    from langchain_openai import ChatOpenAI

    sys = (
        "You are playing an ARC-AGI-3 game. The grid is colours as hex digits "
        "(0–f). You do NOT know the rules — discover them by acting and observing "
        "how the grid changes. Make progress toward completing the level "
        "efficiently (wasted actions are penalised). Reply with EXACTLY ONE action "
        "name from the available list on the last line, e.g. 'ACTION2'. For ACTION6 "
        "(a click) reply 'ACTION6 <x> <y>' with grid coordinates (0–63)."
    )
    recent = ", ".join(history[-8:]) or "(none yet)"
    user = (
        f"Available actions: {', '.join(available)}\n"
        f"Recent actions: {recent}\n\n"
        f"Current grid:\n{grid_hex}\n\n"
        "Your single next action:"
    )
    llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url,
                     temperature=0.3, timeout=60, max_retries=1)
    text = (llm.invoke([("system", sys), ("human", user)]).content or "").upper()
    m = re.search(r"\b(RESET|ACTION[1-7])\b(?:\s+(\d{1,2})\s+(\d{1,2}))?", text)
    if not m:
        return "", None
    name = m.group(1)
    if name == "ACTION6" and m.group(2) and m.group(3):
        return name, (int(m.group(2)) % 64, int(m.group(3)) % 64)
    return name, None


def run_arc3_baseline(game_ids: list[str], *, model: str | None = None,
                      agent_name: str = "prax-baseline", max_actions: int = 80,
                      root_url: str = "https://three.arcprize.org") -> dict:
    """Play each game with the LLM agent; return per-game RHAE + a summary.

    Needs ``ARC_API_KEY`` (live game API) and ``OPENROUTER_API_KEY`` (the model).
    """
    import random

    import requests
    from arc_agi_3 import Agent, GameAction, GameState

    from prax.settings import settings

    api_key = os.getenv("ARC_API_KEY", "")
    model = model or os.getenv("LOW_MODEL") or getattr(settings, "low_model", "")
    or_key = getattr(settings, "openrouter_api_key", None) or os.getenv("OPENROUTER_API_KEY", "")
    or_base = "https://openrouter.ai/api/v1"
    headers = {"X-API-Key": api_key, "Accept": "application/json"}

    class PraxARC3Agent(Agent):
        MAX_ACTIONS = max_actions

        def is_done(self, frames, latest_frame) -> bool:
            return latest_frame.state in (GameState.WIN, GameState.GAME_OVER)

        def choose_action(self, frames, latest_frame):
            # The session must be started with RESET first (it carries card_id and
            # yields the guid subsequent actions need); until then, always RESET.
            if not getattr(self, "guid", None):
                action = GameAction.RESET
                action.set_data({"game_id": self.game_id})
                return action

            avail = [a.name if hasattr(a, "name") else str(a)
                     for a in (latest_frame.available_actions or [])]
            if not avail:
                avail = ["RESET"]
            hist = [f.action_input.id if getattr(f, "action_input", None) else "?"
                    for f in frames[-8:]]
            name, coords = "", None
            try:
                name, coords = _llm_choose_action(
                    _grid_to_hex(latest_frame.frame), avail,
                    [str(h) for h in hist], model, or_key, or_base)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ARC3 LLM choose failed: %s", exc)
            if name not in avail:
                name = random.choice(avail)          # fallback keeps the game moving
                coords = None
            action = GameAction[name]
            data = {"game_id": self.game_id}
            if name == "ACTION6":
                cx, cy = coords or (32, 32)
                data.update({"x": cx, "y": cy})
            action.set_data(data)
            return action

    # Shared session — its cookies (set at scorecard/open) associate the game
    # with this run; agents MUST reuse them or actions get "game not found".
    session = requests.Session()
    session.headers.update(headers)
    r = session.post(f"{root_url}/api/scorecard/open",
                     json={"tags": [agent_name, "baseline"]}, timeout=30)
    r.raise_for_status()
    card_id = str(r.json()["card_id"])

    results = []
    try:
        for gid in game_ids:
            try:
                agent = PraxARC3Agent(card_id=card_id, game_id=gid, agent_name=agent_name,
                                      ROOT_URL=root_url, record=False,
                                      cookies=session.cookies)
                agent.main()
                sc = session.get(f"{root_url}/api/scorecard/{card_id}/{gid}", timeout=30)
                data = sc.json() if sc.ok else {}
                results.append({"game_id": gid, "scorecard": data})
                logger.info("ARC3 %s done: %s", gid, data)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ARC3 game %s failed: %s", gid, exc)
                results.append({"game_id": gid, "error": str(exc)})
    finally:
        session.post(f"{root_url}/api/scorecard/close",
                     json={"card_id": card_id}, timeout=30)

    return {"card_id": card_id, "model": model, "games": results}
