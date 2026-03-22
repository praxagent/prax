"""Tests for the agent checkpointing system."""
from __future__ import annotations

from prax.agent.checkpoint import DEFAULT_MAX_RETRIES, CheckpointManager, TurnCheckpoint


class TestTurnCheckpoint:
    def test_defaults(self):
        tc = TurnCheckpoint(thread_id="t1", user_id="u1")
        assert tc.step_count == 0
        assert tc.retries_used == 0
        assert tc.max_retries == DEFAULT_MAX_RETRIES


class TestCheckpointManager:
    def setup_method(self):
        self.mgr = CheckpointManager(max_retries=3)

    # -- Turn lifecycle --

    def test_start_turn_creates_unique_thread_ids(self):
        t1 = self.mgr.start_turn("user_a")
        t2 = self.mgr.start_turn("user_b")
        assert t1.thread_id != t2.thread_id
        assert t1.thread_id.startswith("user_a:")
        assert t2.thread_id.startswith("user_b:")

    def test_start_turn_replaces_previous_for_same_user(self):
        t1 = self.mgr.start_turn("user_a")
        t2 = self.mgr.start_turn("user_a")
        assert t1.thread_id != t2.thread_id
        assert self.mgr.get_turn("user_a") is t2

    def test_get_turn_returns_none_for_unknown_user(self):
        assert self.mgr.get_turn("nobody") is None

    def test_end_turn_removes_turn(self):
        self.mgr.start_turn("user_a")
        self.mgr.end_turn("user_a")
        assert self.mgr.get_turn("user_a") is None

    def test_end_turn_noop_for_unknown_user(self):
        # Should not raise.
        self.mgr.end_turn("nobody")

    # -- Config helpers --

    def test_graph_config_contains_thread_id(self):
        turn = self.mgr.start_turn("user_a")
        cfg = self.mgr.graph_config(turn)
        assert cfg == {"configurable": {"thread_id": turn.thread_id}}

    # -- Retry tracking --

    def test_can_retry_initially_true(self):
        self.mgr.start_turn("user_a")
        assert self.mgr.can_retry("user_a") is True

    def test_can_retry_false_after_max(self):
        self.mgr.start_turn("user_a")
        for _ in range(3):
            self.mgr.record_retry("user_a")
        assert self.mgr.can_retry("user_a") is False

    def test_can_retry_false_for_unknown_user(self):
        assert self.mgr.can_retry("nobody") is False

    def test_record_retry_increments_counter(self):
        self.mgr.start_turn("user_a")
        self.mgr.record_retry("user_a")
        turn = self.mgr.get_turn("user_a")
        assert turn.retries_used == 1

    def test_record_retry_noop_for_unknown_user(self):
        # Should not raise.
        self.mgr.record_retry("nobody")

    # -- Inspection --

    def test_list_checkpoints_empty_for_new_turn(self):
        self.mgr.start_turn("user_a")
        assert self.mgr.list_checkpoints("user_a") == []

    def test_list_checkpoints_empty_for_unknown_user(self):
        assert self.mgr.list_checkpoints("nobody") == []

    # -- Rollback --

    def test_rollback_config_none_for_unknown_user(self):
        assert self.mgr.get_rollback_config("nobody") is None

    def test_rollback_config_none_when_not_enough_checkpoints(self):
        self.mgr.start_turn("user_a")
        # No checkpoints saved yet, so rollback is impossible.
        assert self.mgr.get_rollback_config("user_a") is None

    # -- Max retries config --

    def test_custom_max_retries(self):
        mgr = CheckpointManager(max_retries=5)
        turn = mgr.start_turn("user_a")
        assert turn.max_retries == 5

    def test_default_max_retries(self):
        mgr = CheckpointManager()
        turn = mgr.start_turn("user_a")
        assert turn.max_retries == DEFAULT_MAX_RETRIES

    # -- Isolation between users --

    def test_users_have_independent_retry_counters(self):
        self.mgr.start_turn("user_a")
        self.mgr.start_turn("user_b")
        self.mgr.record_retry("user_a")
        self.mgr.record_retry("user_a")
        assert self.mgr.get_turn("user_a").retries_used == 2
        assert self.mgr.get_turn("user_b").retries_used == 0

    def test_end_turn_does_not_affect_other_users(self):
        self.mgr.start_turn("user_a")
        self.mgr.start_turn("user_b")
        self.mgr.end_turn("user_a")
        assert self.mgr.get_turn("user_a") is None
        assert self.mgr.get_turn("user_b") is not None
