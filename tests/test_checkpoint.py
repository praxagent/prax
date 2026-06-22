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


# ---------------------------------------------------------------------------
# Durable backend + resume (M3)
# ---------------------------------------------------------------------------

class TestPluggableSaver:
    def test_default_is_in_memory(self, monkeypatch):
        from langgraph.checkpoint.memory import InMemorySaver

        from prax.agent import checkpoint as cp
        from prax.settings import settings
        monkeypatch.setattr(settings, "checkpoint_backend", "memory")
        assert isinstance(cp._build_saver(), InMemorySaver)

    def test_sqlite_falls_back_gracefully(self, monkeypatch):
        """An unconstructable durable backend degrades to in-memory, never raises."""
        from langgraph.checkpoint.memory import InMemorySaver

        from prax.agent import checkpoint as cp
        from prax.settings import settings
        monkeypatch.setattr(settings, "checkpoint_backend", "sqlite")
        # Force the durable import to fail.
        import builtins
        real_import = builtins.__import__

        def boom(name, *a, **k):
            if "sqlite" in name:
                raise ImportError("no sqlite saver")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", boom)
        assert isinstance(cp._build_saver(), InMemorySaver)


class TestResume:
    def test_failed_turn_kept_and_resumable(self, monkeypatch):
        from prax.agent.checkpoint import CheckpointManager
        from prax.settings import settings
        monkeypatch.setattr(settings, "checkpoint_resume_ttl_seconds", 3600)

        mgr = CheckpointManager()
        turn = mgr.start_turn("u1")
        tid = turn.thread_id
        mgr.end_turn("u1", keep_for_resume=True)

        assert mgr.has_resumable("u1") is True
        assert mgr.get_turn("u1") is None  # no longer the active turn

        resumed = mgr.resume_turn("u1")
        assert resumed is not None
        assert resumed.thread_id == tid          # same thread → skips completed steps
        assert resumed.retries_used == 0          # fresh retry budget
        assert mgr.get_turn("u1") is resumed      # re-activated
        assert mgr.has_resumable("u1") is False   # consumed

    def test_success_turn_not_resumable(self):
        from prax.agent.checkpoint import CheckpointManager
        mgr = CheckpointManager()
        mgr.start_turn("u1")
        mgr.end_turn("u1")  # default: purge
        assert mgr.has_resumable("u1") is False
        assert mgr.resume_turn("u1") is None

    def test_expired_resumable_is_dropped(self, monkeypatch):
        from prax.agent.checkpoint import CheckpointManager
        from prax.settings import settings
        monkeypatch.setattr(settings, "checkpoint_resume_ttl_seconds", 0)  # instant expiry
        mgr = CheckpointManager()
        mgr.start_turn("u1")
        mgr.end_turn("u1", keep_for_resume=True)
        assert mgr.has_resumable("u1") is False
        assert mgr.resume_turn("u1") is None


class TestResumePersistence:
    def test_disabled_writes_no_file(self, tmp_path, monkeypatch):
        from prax.agent.checkpoint import CheckpointManager
        from prax.settings import settings
        state = tmp_path / ".prax" / "resumable.json"
        monkeypatch.setattr(settings, "checkpoint_resume_enabled", False)
        monkeypatch.setattr(settings, "checkpoint_resume_state_path", str(state))
        mgr = CheckpointManager()
        mgr.start_turn("u1")
        mgr.end_turn("u1", keep_for_resume=True)
        assert not state.exists()  # persistence off → in-memory only

    def test_survives_restart(self, tmp_path, monkeypatch):
        from prax.agent.checkpoint import CheckpointManager
        from prax.settings import settings
        state = tmp_path / ".prax" / "resumable.json"
        monkeypatch.setattr(settings, "checkpoint_resume_enabled", True)
        monkeypatch.setattr(settings, "checkpoint_resume_state_path", str(state))
        monkeypatch.setattr(settings, "checkpoint_resume_ttl_seconds", 3600)

        mgr = CheckpointManager()
        turn = mgr.start_turn("u1")
        tid = turn.thread_id
        mgr.end_turn("u1", keep_for_resume=True)
        assert state.exists()

        # Simulate a process restart — a fresh manager loads the pointer.
        mgr2 = CheckpointManager()
        assert mgr2.has_resumable("u1") is True
        resumed = mgr2.resume_turn("u1")
        assert resumed is not None and resumed.thread_id == tid
        # Consuming the resume clears it from the persisted state.
        assert CheckpointManager().has_resumable("u1") is False

    def test_expired_pointer_not_loaded(self, tmp_path, monkeypatch):
        import json

        from prax.agent.checkpoint import CheckpointManager
        from prax.settings import settings
        state = tmp_path / ".prax" / "resumable.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({
            "u1": {"thread_id": "u1:dead", "user_id": "u1", "max_retries": 2, "expiry": 1.0},
        }))
        monkeypatch.setattr(settings, "checkpoint_resume_enabled", True)
        monkeypatch.setattr(settings, "checkpoint_resume_state_path", str(state))
        assert CheckpointManager().has_resumable("u1") is False

    def test_clear_resumable(self, tmp_path, monkeypatch):
        from prax.agent.checkpoint import CheckpointManager
        from prax.settings import settings
        state = tmp_path / ".prax" / "resumable.json"
        monkeypatch.setattr(settings, "checkpoint_resume_enabled", True)
        monkeypatch.setattr(settings, "checkpoint_resume_state_path", str(state))
        mgr = CheckpointManager()
        mgr.start_turn("u1")
        mgr.end_turn("u1", keep_for_resume=True)
        assert mgr.clear_resumable("u1") == 1
        assert mgr.has_resumable("u1") is False
        assert CheckpointManager().has_resumable("u1") is False
