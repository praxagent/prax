"""Tests for short-term memory (scratchpad) store."""
import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _mock_workspace(tmp_path):
    """Point workspace_root to per-user temp directories."""

    def _user_root(user_id: str) -> str:
        path = os.path.join(str(tmp_path), user_id)
        os.makedirs(path, exist_ok=True)
        return path

    with patch("prax.services.workspace_service.workspace_root", side_effect=_user_root):
        yield tmp_path


class TestSTMWrite:
    def test_write_creates_entry(self):
        from prax.services.memory.stm import stm_write

        entry = stm_write("user1", "timezone", "America/New_York")
        assert entry.key == "timezone"
        assert entry.content == "America/New_York"
        assert entry.importance == 0.5

    def test_write_upserts_existing_key(self):
        from prax.services.memory.stm import stm_read, stm_write

        stm_write("user1", "pref", "dark mode")
        stm_write("user1", "pref", "light mode")
        entries = stm_read("user1", key="pref")
        assert len(entries) == 1
        assert entries[0].content == "light mode"
        assert entries[0].access_count == 1  # incremented on upsert

    def test_write_with_tags_and_importance(self):
        from prax.services.memory.stm import stm_write

        entry = stm_write("user1", "project", "Alpha", tags=["work", "priority"], importance=0.9)
        assert entry.tags == ["work", "priority"]
        assert entry.importance == 0.9


class TestSTMRead:
    def test_read_empty(self):
        from prax.services.memory.stm import stm_read

        assert stm_read("user1") == []

    def test_read_all(self):
        from prax.services.memory.stm import stm_read, stm_write

        stm_write("user1", "a", "alpha")
        stm_write("user1", "b", "beta")
        entries = stm_read("user1")
        assert len(entries) == 2

    def test_read_by_key(self):
        from prax.services.memory.stm import stm_read, stm_write

        stm_write("user1", "a", "alpha")
        stm_write("user1", "b", "beta")
        entries = stm_read("user1", key="b")
        assert len(entries) == 1
        assert entries[0].content == "beta"

    def test_read_missing_key(self):
        from prax.services.memory.stm import stm_read, stm_write

        stm_write("user1", "a", "alpha")
        entries = stm_read("user1", key="missing")
        assert entries == []


class TestSTMDelete:
    def test_delete_existing(self):
        from prax.services.memory.stm import stm_delete, stm_read, stm_write

        stm_write("user1", "x", "value")
        assert stm_delete("user1", "x") is True
        assert stm_read("user1") == []

    def test_delete_missing(self):
        from prax.services.memory.stm import stm_delete

        assert stm_delete("user1", "nope") is False


class TestSTMCompact:
    @patch("prax.services.memory.stm.settings")
    def test_compact_skips_when_below_limit(self, mock_settings):
        from prax.services.memory.stm import stm_compact, stm_write

        mock_settings.memory_stm_max_entries = 50
        stm_write("user1", "a", "alpha")
        result = stm_compact("user1")
        assert "no compaction" in result

    @patch("prax.services.memory.stm.settings")
    def test_compact_reduces_entries(self, mock_settings, _mock_workspace):
        from prax.services.memory.stm import _load, stm_compact, stm_write

        mock_settings.memory_stm_max_entries = 5
        for i in range(10):
            stm_write("user1", f"key{i}", f"value {i}")

        # Mock the LLM call (deferred import inside stm_compact)
        with patch("prax.agent.llm_factory.build_llm") as mock_llm:
            mock_response = type("R", (), {"content": "Compacted summary of values 0-4"})()
            mock_llm.return_value.invoke.return_value = mock_response
            stm_compact("user1")

        entries = _load("user1")
        # Should have summary + kept entries (fewer than original 10)
        assert len(entries) < 10
        assert any(e["key"] == "_compacted_summary" for e in entries)


class TestSTMIsolation:
    def test_users_are_isolated(self):
        from prax.services.memory.stm import stm_read, stm_write

        stm_write("user1", "secret", "user1 data")
        stm_write("user2", "secret", "user2 data")
        u1 = stm_read("user1", key="secret")
        u2 = stm_read("user2", key="secret")
        assert u1[0].content == "user1 data"
        assert u2[0].content == "user2 data"
