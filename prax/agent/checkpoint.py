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

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

logger = logging.getLogger(__name__)

# Maximum automatic retries when the agent graph raises during execution.
DEFAULT_MAX_RETRIES = 2


def _build_saver():
    """Build the checkpointer backend from settings.

    ``memory`` (default) → in-memory (fast, ephemeral).  ``sqlite`` → a durable
    on-disk store so checkpoint DATA survives a process restart.  Any failure
    constructing the durable backend degrades gracefully to in-memory.
    """
    try:
        from prax.settings import settings
        backend = (settings.checkpoint_backend or "memory").lower()
    except Exception:
        backend = "memory"

    if backend == "sqlite":
        try:
            import sqlite3

            from langgraph.checkpoint.sqlite import SqliteSaver

            from prax.settings import settings
            path = settings.checkpoint_db_path or ".prax/checkpoints.sqlite"
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, check_same_thread=False)
            logger.info("Checkpointer: durable SQLite backend at %s", path)
            return SqliteSaver(conn)
        except Exception:
            logger.warning(
                "SQLite checkpointer unavailable (is langgraph-checkpoint-sqlite "
                "installed?) — falling back to in-memory", exc_info=True,
            )
    return InMemorySaver()


def _resume_ttl() -> float:
    """Seconds a failed turn is kept resumable (from settings, default 3600)."""
    try:
        from prax.settings import settings
        return float(settings.checkpoint_resume_ttl_seconds)
    except Exception:
        return 3600.0


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
        self.saver = _build_saver()
        self.max_retries = max_retries
        # Active turn metadata keyed by user_id.
        self._turns: dict[str, TurnCheckpoint] = {}
        # Turns kept after a failed/timed-out turn so the user can resume from
        # the failure point.  Value = (turn, wall_clock_expiry).  Wall-clock
        # (not monotonic) so it can be persisted and survive a restart.
        self._resume_lock = threading.Lock()
        self._resumable: dict[str, tuple[TurnCheckpoint, float]] = self._load_resumable()

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

    def end_turn(self, user_id: str, *, keep_for_resume: bool = False) -> None:
        """Clean up after a turn completes.

        By default the thread's checkpoints are purged (success path).  When
        *keep_for_resume* is set (a failed/timed-out turn), the thread is
        retained for a TTL so the user can resume from the failure point via
        :meth:`resume_turn`, skipping completed steps.
        """
        turn = self._turns.pop(user_id, None)
        if not turn:
            return
        if keep_for_resume:
            self._resumable[user_id] = (turn, time.time() + _resume_ttl())
            self._save_resumable()
            logger.info("Kept turn for resume (user=%s, thread=%s)", user_id, turn.thread_id)
            return
        # Drop any prior resumable for this user and purge its checkpoints.
        self._drop_resumable(user_id)
        try:
            self.saver.delete_thread(turn.thread_id)
        except Exception:
            pass

    def _drop_resumable(self, user_id: str) -> None:
        entry = self._resumable.pop(user_id, None)
        if entry:
            self._save_resumable()
            try:
                self.saver.delete_thread(entry[0].thread_id)
            except Exception:
                pass

    def _purge_expired_resumables(self) -> None:
        now = time.time()
        changed = False
        for uid, (turn, expiry) in list(self._resumable.items()):
            if now >= expiry:
                self._resumable.pop(uid, None)
                changed = True
                try:
                    self.saver.delete_thread(turn.thread_id)
                except Exception:
                    pass
        if changed:
            self._save_resumable()

    def has_resumable(self, user_id: str) -> bool:
        """True if the user has a non-expired resumable (failed) turn."""
        self._purge_expired_resumables()
        return user_id in self._resumable

    def resume_turn(self, user_id: str) -> TurnCheckpoint | None:
        """Re-activate a kept-for-resume turn with a fresh retry budget.

        Returns the re-activated turn (now the active turn for the user) or
        None if there is nothing resumable (or it expired).
        """
        self._purge_expired_resumables()
        entry = self._resumable.pop(user_id, None)
        if not entry:
            return None
        self._save_resumable()
        turn, _ = entry
        turn.retries_used = 0  # fresh retry budget for the resumed attempt
        self._turns[user_id] = turn
        logger.info("Resuming turn (user=%s, thread=%s)", user_id, turn.thread_id)
        return turn

    def clear_resumable(self, user_id: str | None = None) -> int:
        """Discard pending resumes — one user's, or all when *user_id* is None.

        Returns the number of resumable turns dropped.  Use this (or delete the
        state file / set CHECKPOINT_RESUME_ENABLED=false) when you do NOT want a
        failed turn to be resumed.
        """
        targets = [user_id] if user_id is not None else list(self._resumable)
        dropped = 0
        for uid in targets:
            entry = self._resumable.pop(uid, None)
            if entry:
                dropped += 1
                try:
                    self.saver.delete_thread(entry[0].thread_id)
                except Exception:
                    pass
        if dropped:
            self._save_resumable()
        return dropped

    # ------------------------------------------------------------------
    # Resumable-pointer persistence (survives a restart with a durable saver)
    # ------------------------------------------------------------------

    @staticmethod
    def _resume_state_path() -> Path | None:
        """State-file path, or None when resume persistence is off."""
        try:
            from prax.settings import settings
            if not settings.checkpoint_resume_enabled:
                return None
            return Path(settings.checkpoint_resume_state_path).expanduser()
        except Exception:
            return None

    def _save_resumable(self) -> None:
        path = self._resume_state_path()
        if path is None:
            return  # persistence off → purely in-memory
        with self._resume_lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                data = {
                    uid: {
                        "thread_id": turn.thread_id,
                        "user_id": turn.user_id,
                        "max_retries": turn.max_retries,
                        "expiry": expiry,
                    }
                    for uid, (turn, expiry) in self._resumable.items()
                }
                path.write_text(json.dumps(data))
            except Exception:
                logger.debug("Could not persist resumable state", exc_info=True)

    def _load_resumable(self) -> dict[str, tuple[TurnCheckpoint, float]]:
        path = self._resume_state_path()
        if path is None or not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text())
        except Exception:
            return {}
        now = time.time()
        out: dict[str, tuple[TurnCheckpoint, float]] = {}
        for uid, rec in raw.items():
            expiry = float(rec.get("expiry", 0))
            if expiry <= now:
                continue  # already expired
            out[uid] = (
                TurnCheckpoint(
                    thread_id=rec["thread_id"],
                    user_id=rec.get("user_id", uid),
                    max_retries=int(rec.get("max_retries", self.max_retries)),
                ),
                expiry,
            )
        if out:
            logger.info("Loaded %d resumable turn(s) from %s", len(out), path)
        return out

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
