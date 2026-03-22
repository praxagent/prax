"""Agent checkpointing — save, inspect, and rollback execution state.

Wraps LangGraph's built-in checkpointing so the orchestrator can:

- **Retry** a failed tool-call chain from the last good state
- **Rollback** to an earlier step when the user says "undo that"
- **Inspect** the execution history for debugging

Each conversation turn gets a unique thread.  Checkpoints are in-memory
(fast, no extra infrastructure) and scoped per-user so one user's state
can't leak into another's.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

logger = logging.getLogger(__name__)

# Maximum automatic retries when the agent graph raises during execution.
DEFAULT_MAX_RETRIES = 2


@dataclass
class TurnCheckpoint:
    """Metadata for a single conversation turn's checkpoint history."""

    thread_id: str
    user_id: str
    step_count: int = 0
    retries_used: int = 0
    max_retries: int = DEFAULT_MAX_RETRIES


class CheckpointManager:
    """Manages LangGraph checkpoints for the conversation agent.

    One instance is shared across all users.  Each conversation turn
    creates a fresh ``thread_id`` so checkpoints don't leak across turns.
    """

    def __init__(self, max_retries: int = DEFAULT_MAX_RETRIES) -> None:
        self.saver = InMemorySaver()
        self.max_retries = max_retries
        # Active turn metadata keyed by user_id.
        self._turns: dict[str, TurnCheckpoint] = {}

    # ------------------------------------------------------------------
    # Turn lifecycle
    # ------------------------------------------------------------------

    def start_turn(self, user_id: str) -> TurnCheckpoint:
        """Begin a new conversation turn, returning its metadata."""
        thread_id = f"{user_id}:{uuid.uuid4().hex[:12]}"
        turn = TurnCheckpoint(
            thread_id=thread_id,
            user_id=user_id,
            max_retries=self.max_retries,
        )
        self._turns[user_id] = turn
        return turn

    def get_turn(self, user_id: str) -> TurnCheckpoint | None:
        """Return the active turn for a user, if any."""
        return self._turns.get(user_id)

    def end_turn(self, user_id: str) -> None:
        """Clean up after a turn completes (success or final failure)."""
        turn = self._turns.pop(user_id, None)
        if turn:
            # Purge checkpoints for this thread to free memory.
            try:
                self.saver.delete_thread(turn.thread_id)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def graph_config(self, turn: TurnCheckpoint) -> dict[str, Any]:
        """Return the LangGraph config dict for an invocation."""
        return {"configurable": {"thread_id": turn.thread_id}}

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def list_checkpoints(self, user_id: str, limit: int = 20) -> list[dict]:
        """Return a human-readable list of checkpoints for the active turn."""
        turn = self._turns.get(user_id)
        if not turn:
            return []
        config = self.graph_config(turn)
        results = []
        for cp_tuple in self.saver.list(config, limit=limit):
            meta = cp_tuple.metadata or {}
            results.append({
                "checkpoint_id": cp_tuple.checkpoint["id"],
                "step": meta.get("step", "?"),
                "source": meta.get("source", "?"),
                "ts": cp_tuple.checkpoint.get("ts", ""),
            })
        return results

    def can_retry(self, user_id: str) -> bool:
        """Return True if the current turn has retries remaining."""
        turn = self._turns.get(user_id)
        if not turn:
            return False
        return turn.retries_used < turn.max_retries

    def record_retry(self, user_id: str) -> None:
        """Increment the retry counter for the current turn."""
        turn = self._turns.get(user_id)
        if turn:
            turn.retries_used += 1

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def get_rollback_config(
        self, user_id: str, steps_back: int = 2,
    ) -> dict[str, Any] | None:
        """Get a config that resumes from *steps_back* checkpoints ago.

        ``steps_back=2`` skips the failed tool result and the tool call,
        landing on the last clean agent decision point.

        Returns None if there aren't enough checkpoints to roll back.
        """
        turn = self._turns.get(user_id)
        if not turn:
            return None
        config = self.graph_config(turn)
        checkpoints = list(self.saver.list(config, limit=steps_back + 1))
        if len(checkpoints) <= steps_back:
            return None
        target = checkpoints[steps_back]
        return {
            "configurable": {
                "thread_id": turn.thread_id,
                "checkpoint_id": target.checkpoint["id"],
            }
        }
