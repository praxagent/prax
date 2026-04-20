"""Tests for prax.services.progress_service.

Cover the bounded-growth contract: compaction triggers, file stays
under the size cap, recent entries stay at <= MAX_RECENT_ENTRIES,
detail files live in the sidecar directory, and compactor failure
falls back gracefully.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prax.services import library_service, progress_service


@pytest.fixture
def user_with_space(tmp_path, monkeypatch):
    user_id = "test_user"
    ws = tmp_path / user_id
    ws.mkdir()
    monkeypatch.setattr(library_service, "workspace_root", lambda _uid: str(ws))
    library_service.create_space(user_id, "Demo Project")
    return user_id, "demo-project"


def _fake_compactor(current_archive, folded):
    """Deterministic compactor that concatenates into a labelled summary."""
    existing = f"{current_archive} " if current_archive else ""
    return f"{existing}[compacted: {len(folded)} entries]"


class TestReadProgress:
    def test_read_empty_space_returns_placeholder(self, user_with_space):
        user_id, slug = user_with_space
        result = progress_service.read_progress(user_id, slug)
        assert "No progress recorded yet" in result
        assert slug in result

    def test_read_missing_space_returns_error(self, user_with_space):
        user_id, _ = user_with_space
        result = progress_service.read_progress(user_id, "does-not-exist")
        assert "does not exist" in result


class TestAppendProgress:
    def test_append_creates_file(self, user_with_space):
        user_id, slug = user_with_space
        result = progress_service.append_progress(
            user_id, slug, outcome="shipped login form",
        )
        assert "Appended" in result
        content = progress_service.read_progress(user_id, slug)
        assert "shipped login form" in content
        assert "## Archive" in content
        assert "## Recent sessions" in content
        assert "## Open threads" in content

    def test_append_rejects_missing_space(self, user_with_space):
        user_id, _ = user_with_space
        result = progress_service.append_progress(
            user_id, "nope", outcome="anything",
        )
        assert "does not exist" in result

    def test_open_threads_overwritten_when_supplied(self, user_with_space):
        user_id, slug = user_with_space
        progress_service.append_progress(
            user_id, slug, outcome="A", open_threads=["fix bug 1", "review PR"],
        )
        first = progress_service.read_progress(user_id, slug)
        assert "fix bug 1" in first
        assert "review PR" in first
        progress_service.append_progress(
            user_id, slug, outcome="B", open_threads=["only this one"],
        )
        second = progress_service.read_progress(user_id, slug)
        assert "only this one" in second
        assert "fix bug 1" not in second

    def test_open_threads_untouched_when_omitted(self, user_with_space):
        user_id, slug = user_with_space
        progress_service.append_progress(
            user_id, slug, outcome="first", open_threads=["keep me"],
        )
        progress_service.append_progress(user_id, slug, outcome="second")
        content = progress_service.read_progress(user_id, slug)
        assert "keep me" in content

    def test_outcome_sanitized_to_single_line(self, user_with_space):
        user_id, slug = user_with_space
        progress_service.append_progress(
            user_id, slug, outcome="line one\nline two  has  spaces",
        )
        content = progress_service.read_progress(user_id, slug)
        assert "line one line two has spaces" in content


class TestCompaction:
    def test_compaction_triggers_past_max_recent(self, user_with_space):
        user_id, slug = user_with_space
        # Exactly one past the threshold triggers compaction.
        for i in range(progress_service.MAX_RECENT_ENTRIES + 1):
            progress_service.append_progress(
                user_id,
                slug,
                outcome=f"session {i}",
                compactor=_fake_compactor,
                now=datetime(2026, 4, 1 + (i % 28), tzinfo=UTC),
            )
        content = progress_service.read_progress(user_id, slug)
        assert "[compacted:" in content
        recent_lines = [
            line for line in content.splitlines()
            if line.startswith("- ") and "session" in line
        ]
        assert len(recent_lines) == progress_service.COMPACT_KEEP_RECENT

    def test_file_stays_under_cap_over_many_writes(self, user_with_space):
        user_id, slug = user_with_space
        for i in range(60):
            progress_service.append_progress(
                user_id,
                slug,
                outcome=f"session {i} with a moderately long outcome description that mimics real use",
                compactor=_fake_compactor,
            )
        content = progress_service.read_progress(user_id, slug)
        assert len(content) <= progress_service.MAX_FILE_CHARS + 200, (
            f"Progress file grew to {len(content)} chars (cap {progress_service.MAX_FILE_CHARS})"
        )

    def test_compactor_exception_falls_back(self, user_with_space):
        user_id, slug = user_with_space

        def broken(_archive, _folded):
            raise RuntimeError("LLM is down")

        for i in range(progress_service.MAX_RECENT_ENTRIES + 1):
            progress_service.append_progress(
                user_id, slug, outcome=f"entry {i}", compactor=broken,
            )
        content = progress_service.read_progress(user_id, slug)
        assert "## Archive" in content
        # Recent kept to COMPACT_KEEP_RECENT even on compactor failure.
        recent_lines = [
            line for line in content.splitlines()
            if line.startswith("- ") and "entry " in line
        ]
        assert len(recent_lines) == progress_service.COMPACT_KEEP_RECENT


class TestSessionDetail:
    def test_detail_written_and_read_back(self, user_with_space):
        user_id, slug = user_with_space
        fixed = datetime(2026, 4, 19, 10, 30, 45, tzinfo=UTC)
        progress_service.append_progress(
            user_id,
            slug,
            outcome="debug session",
            detail="Full notes: tried X, then Y, landed on Z.",
            now=fixed,
        )
        detail = progress_service.read_session_detail(user_id, slug, "2026-04-19")
        assert "tried X" in detail
        assert "debug session" in detail

    def test_detail_missing_date_returns_placeholder(self, user_with_space):
        user_id, slug = user_with_space
        progress_service.append_progress(user_id, slug, outcome="x", detail="y")
        result = progress_service.read_session_detail(user_id, slug, "2020-01-01")
        assert "No session details" in result

    def test_detail_bad_date_format_rejected(self, user_with_space):
        user_id, slug = user_with_space
        result = progress_service.read_session_detail(user_id, slug, "not-a-date")
        assert "YYYY-MM-DD" in result

    def test_detail_not_loaded_unless_requested(self, user_with_space):
        user_id, slug = user_with_space
        progress_service.append_progress(
            user_id, slug, outcome="one-liner only", detail="secret long detail",
        )
        main = progress_service.read_progress(user_id, slug)
        # Progressive disclosure — detail text lives in a sidecar file only.
        assert "secret long detail" not in main


class TestProgressTools:
    """Agent-tool wrappers over progress_service."""

    def test_progress_read_tool(self, user_with_space):
        import importlib

        from prax.agent.user_context import current_user_id
        user_id, slug = user_with_space
        current_user_id.set(user_id)
        module = importlib.reload(importlib.import_module("prax.agent.workspace_tools"))
        progress_service.append_progress(user_id, slug, outcome="done A")
        result = module.progress_read.invoke({"space_slug": slug})
        assert "done A" in result

    def test_progress_append_tool(self, user_with_space):
        import importlib

        from prax.agent.user_context import current_user_id
        user_id, slug = user_with_space
        current_user_id.set(user_id)
        module = importlib.reload(importlib.import_module("prax.agent.workspace_tools"))
        result = module.progress_append.invoke({
            "space_slug": slug, "outcome": "wired tool", "open_threads": ["next: tests"],
        })
        assert "Appended" in result
        content = progress_service.read_progress(user_id, slug)
        assert "wired tool" in content
        assert "next: tests" in content
