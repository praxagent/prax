import os
import subprocess

import pytest

from prax.services import workspace_service


@pytest.fixture()
def ws_dir(tmp_path, monkeypatch):
    """Point workspace_dir at a fresh temp directory."""
    monkeypatch.setattr(workspace_service.settings, "workspace_dir", str(tmp_path))
    return tmp_path


USER = "+10000000000"


class TestEnsureWorkspace:
    def test_creates_dirs_and_git(self, ws_dir):
        root = workspace_service._ensure_workspace(USER)
        assert os.path.isdir(os.path.join(root, "active"))
        assert os.path.isdir(os.path.join(root, "archive"))
        assert os.path.isdir(os.path.join(root, ".git"))

    def test_idempotent(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        workspace_service._ensure_workspace(USER)  # no error


class TestSaveAndRead:
    def test_save_and_read(self, ws_dir):
        workspace_service.save_file(USER, "notes.md", "# Hello")
        content = workspace_service.read_file(USER, "notes.md")
        assert content == "# Hello"

    def test_read_not_found(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        with pytest.raises(FileNotFoundError):
            workspace_service.read_file(USER, "nope.md")

    def test_save_creates_git_commit(self, ws_dir):
        workspace_service.save_file(USER, "paper.md", "content")
        root = workspace_service._workspace_root(USER)
        result = subprocess.run(
            ["git", "log", "--oneline"], cwd=root, capture_output=True, text=True,
        )
        assert "paper.md" in result.stdout


class TestSaveBinary:
    def test_copies_to_archive(self, ws_dir, tmp_path):
        src = tmp_path / "test.pdf"
        src.write_bytes(b"%PDF-fake")
        dest = workspace_service.save_binary(USER, "test.pdf", str(src))
        assert os.path.exists(dest)
        assert "archive" in dest
        with open(dest, "rb") as f:
            assert f.read() == b"%PDF-fake"


class TestListActive:
    def test_empty_workspace(self, ws_dir):
        assert workspace_service.list_active(USER) == []

    def test_after_save(self, ws_dir):
        workspace_service.save_file(USER, "a.md", "a")
        workspace_service.save_file(USER, "b.md", "b")
        assert workspace_service.list_active(USER) == ["a.md", "b.md"]

    def test_nonexistent_user(self, ws_dir):
        assert workspace_service.list_active("+19999999999") == []


class TestArchive:
    def test_moves_file(self, ws_dir):
        workspace_service.save_file(USER, "paper.md", "content")
        workspace_service.archive_file(USER, "paper.md")
        assert workspace_service.list_active(USER) == []
        root = workspace_service._workspace_root(USER)
        assert os.path.exists(os.path.join(root, "archive", "paper.md"))

    def test_not_found(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        with pytest.raises(FileNotFoundError):
            workspace_service.archive_file(USER, "nope.md")


class TestSearchArchive:
    def test_finds_match(self, ws_dir):
        workspace_service.save_file(USER, "paper.md", "quantum computing breakthrough")
        workspace_service.archive_file(USER, "paper.md")
        results = workspace_service.search_archive(USER, "quantum")
        assert len(results) == 1
        assert results[0]["filename"] == "paper.md"
        assert "quantum" in results[0]["snippet"].lower()

    def test_no_match(self, ws_dir):
        workspace_service.save_file(USER, "paper.md", "hello world")
        workspace_service.archive_file(USER, "paper.md")
        assert workspace_service.search_archive(USER, "xyznonexistent") == []

    def test_empty_archive(self, ws_dir):
        assert workspace_service.search_archive(USER, "anything") == []


class TestRestore:
    def test_moves_back(self, ws_dir):
        workspace_service.save_file(USER, "paper.md", "content")
        workspace_service.archive_file(USER, "paper.md")
        assert workspace_service.list_active(USER) == []
        workspace_service.restore_file(USER, "paper.md")
        assert workspace_service.list_active(USER) == ["paper.md"]

    def test_not_found(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        with pytest.raises(FileNotFoundError):
            workspace_service.restore_file(USER, "nope.md")


class TestGetWorkspaceContext:
    def test_empty(self, ws_dir):
        assert workspace_service.get_workspace_context(USER) == ""

    def test_with_files(self, ws_dir):
        workspace_service.save_file(USER, "paper.md", "content")
        ctx = workspace_service.get_workspace_context(USER)
        assert "1 file(s)" in ctx
        assert "workspace_list" in ctx

    def test_user_notes_not_injected_without_relevance(self, ws_dir):
        workspace_service.save_user_notes(
            USER,
            "\n".join([
                "preferences:",
                "- Interpret NPR as NPR News Now.",
                "- User prefers concise answers.",
            ]),
        )

        ctx = workspace_service.get_workspace_context(USER, "what is the weather?")

        assert "Interpret NPR" not in ctx
        assert "concise answers" not in ctx

    def test_user_notes_injects_relevant_alias_only(self, ws_dir):
        workspace_service.save_user_notes(
            USER,
            "\n".join([
                "preferences:",
                "- Interpret NPR as NPR News Now.",
                "- User prefers concise answers.",
                "interests:",
                "- gardening",
            ]),
        )

        ctx = workspace_service.get_workspace_context(USER, "NPR")

        assert "Relevant User Notes" in ctx
        assert "Interpret NPR" in ctx
        assert "gardening" not in ctx

    def test_user_notes_injects_timezone_for_reminders(self, ws_dir):
        workspace_service.save_user_notes(
            USER,
            "\n".join([
                "timezone: America/Los_Angeles",
                "preferences:",
                "- Interpret NPR as NPR News Now.",
            ]),
        )

        ctx = workspace_service.get_workspace_context(USER, "remind me tomorrow")

        assert "America/Los_Angeles" in ctx
        assert "Interpret NPR" not in ctx


class TestUserNotesCompaction:
    def test_small_clean_user_notes_remain_verbatim(self, ws_dir):
        content = "\n".join([
            "timezone: America/Los_Angeles",
            "preferences:",
            "- Interpret NPR as NPR News Now.",
        ]) + "\n"

        workspace_service.save_user_notes(USER, content)

        assert workspace_service.read_user_notes(USER) == content

    def test_duplicate_user_notes_trigger_compaction(self, ws_dir):
        content = "\n".join([
            "timezone: UTC",
            "preferences:",
            "- Interpret NPR as NPR News Now.",
            "- Interpret NPR as NPR News Now.",
            "timezone: America/Los_Angeles",
        ]) + "\n"

        workspace_service.save_user_notes(USER, content)
        compacted = workspace_service.read_user_notes(USER)

        assert "timezone: America/Los_Angeles" in compacted
        assert "timezone: UTC" not in compacted
        assert compacted.count("Interpret NPR as NPR News Now") == 1

    def test_large_user_notes_are_capped_to_recent_section_items(self, ws_dir):
        content = "preferences:\n" + "\n".join(
            f"- preference-item-{i:03d}" for i in range(100)
        ) + "\n"

        workspace_service.save_user_notes(USER, content)
        compacted = workspace_service.read_user_notes(USER)

        assert "preference-item-000" not in compacted
        assert "preference-item-083" not in compacted
        assert "preference-item-084" in compacted
        assert "preference-item-099" in compacted

    def test_compaction_preserves_raw_update_in_git_history(self, ws_dir):
        content = "\n".join([
            "preferences:",
            "- duplicate",
            "- duplicate",
        ]) + "\n"

        workspace_service.save_user_notes(USER, content)
        root = workspace_service._workspace_root(USER)
        result = subprocess.run(
            ["git", "log", "--oneline"], cwd=root, capture_output=True, text=True,
        )

        assert "Update user notes" in result.stdout
        assert "Compact user notes" in result.stdout


class TestGitHistory:
    def test_full_lifecycle_commits(self, ws_dir):
        workspace_service.save_file(USER, "doc.md", "v1")
        workspace_service.archive_file(USER, "doc.md")
        workspace_service.restore_file(USER, "doc.md")
        root = workspace_service._workspace_root(USER)
        result = subprocess.run(
            ["git", "log", "--oneline"], cwd=root, capture_output=True, text=True,
        )
        lines = result.stdout.strip().splitlines()
        # save + archive + restore = 3 commits (init is a no-op when empty)
        assert len(lines) >= 3


class TestGitignore:
    def test_gitignore_created_on_init(self, ws_dir):
        root = workspace_service._ensure_workspace(USER)
        gitignore_path = os.path.join(root, ".gitignore")
        assert os.path.isfile(gitignore_path)
        content = open(gitignore_path).read()
        # Should block media and latex artifacts
        assert "*.mp3" in content
        assert "*.aux" in content
        assert "__pycache__" in content

    def test_gitignore_blocks_mp3_commits(self, ws_dir):
        root = workspace_service._ensure_workspace(USER)
        # Create an mp3 file in the workspace
        mp3_path = os.path.join(root, "active", "test.mp3")
        with open(mp3_path, "wb") as f:
            f.write(b"\xff\xfb\x90\x00" * 100)  # fake mp3 bytes
        # Git add -A should not stage it
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True,
        )
        # The mp3 should not appear in staged files
        assert "test.mp3" not in result.stdout

    def test_gitignore_allows_pdf(self, ws_dir):
        root = workspace_service._ensure_workspace(USER)
        pdf_path = os.path.join(root, "active", "paper.pdf")
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4 fake content")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True,
        )
        assert "paper.pdf" in result.stdout

    def test_gitignore_added_to_existing_workspace(self, ws_dir):
        """Workspaces created before the gitignore feature get one retroactively."""
        root = workspace_service._workspace_root(USER)
        # Create a workspace manually without gitignore (simulating old workspace).
        os.makedirs(os.path.join(root, "active"), exist_ok=True)
        os.makedirs(os.path.join(root, "archive"), exist_ok=True)
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=root, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=root, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=root, check=True, capture_output=True,
        )
        # No gitignore yet.
        assert not os.path.isfile(os.path.join(root, ".gitignore"))
        # Calling _ensure_workspace should add it.
        workspace_service._ensure_workspace(USER)
        assert os.path.isfile(os.path.join(root, ".gitignore"))


class TestPluginDirs:
    def test_plugin_dirs_created(self, ws_dir):
        root = workspace_service._ensure_workspace(USER)
        assert os.path.isdir(os.path.join(root, "plugins", "custom"))
        assert os.path.isdir(os.path.join(root, "plugins", "shared"))

    def test_get_workspace_plugins_dir(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        plugins_dir = workspace_service.get_workspace_plugins_dir(USER)
        assert plugins_dir is not None
        assert plugins_dir.endswith("plugins")


class TestSetRemote:
    def test_refuses_unparseable_url(self, ws_dir):
        result = workspace_service.set_remote(USER, "not-a-url")
        assert "error" in result

    def test_refuses_public_repo(self, ws_dir, monkeypatch):
        """Should refuse to set remote to a public GitHub repo."""
        import json
        import urllib.request

        class FakeResp:
            def read(self):
                return json.dumps({"private": False}).encode()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: FakeResp())
        result = workspace_service.set_remote(USER, "git@github.com:user/public-repo.git")
        assert "error" in result
        assert "public" in result["error"].lower()


class TestPush:
    def test_push_requires_ssh_key(self, ws_dir, monkeypatch):
        monkeypatch.setattr(workspace_service.settings, "prax_ssh_key_b64", None)
        monkeypatch.setattr(workspace_service.settings, "plugin_repo_ssh_key_b64", None)
        workspace_service._ensure_workspace(USER)
        result = workspace_service.push(USER)
        assert "error" in result
        assert "SSH key" in result["error"]

    def test_push_requires_remote(self, ws_dir, monkeypatch):
        import base64
        key = base64.b64encode(b"fake-key").decode()
        monkeypatch.setattr(workspace_service.settings, "prax_ssh_key_b64", key)
        # Clear cached SSH key file.
        workspace_service._ssh_key_file = None
        workspace_service._ensure_workspace(USER)
        result = workspace_service.push(USER)
        assert "error" in result
        assert "remote" in result["error"].lower()


class TestTraceLog:
    def test_append_and_read_trace(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        workspace_service.append_trace(USER, [
            {"type": "user", "content": "hello world"},
            {"type": "assistant", "content": "hi there"},
        ])
        tail = workspace_service.read_trace_tail(USER, 10)
        assert "hello world" in tail
        assert "hi there" in tail

    def test_search_trace_finds_match(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        workspace_service.append_trace(USER, [
            {"type": "user", "content": "tell me about quantum computing"},
        ])
        results = workspace_service.search_trace(USER, "quantum")
        assert len(results) == 1
        assert "quantum" in results[0]["excerpt"]

    def test_search_trace_no_match(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        workspace_service.append_trace(USER, [
            {"type": "user", "content": "hello"},
        ])
        results = workspace_service.search_trace(USER, "nonexistent")
        assert results == []

    def test_rotation_threshold_is_half_mb(self):
        assert workspace_service._TRACE_MAX_BYTES == 512 * 1024

    def test_rotation_creates_plain_text_archive(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        root = workspace_service._workspace_root(USER)
        trace_path = os.path.join(root, "trace.log")

        # Write more than 0.5 MB to trigger rotation.
        with open(trace_path, "w") as f:
            f.write("x" * (600 * 1024))

        workspace_service._rotate_trace(trace_path)

        archive_dir = os.path.join(root, "archive", "trace_logs")
        assert os.path.isdir(archive_dir)
        archives = [f for f in os.listdir(archive_dir) if f.endswith(".log")]
        assert len(archives) == 1
        # Should be plain text, not gzip.
        with open(os.path.join(archive_dir, archives[0])) as f:
            content = f.read()
        assert "x" * 100 in content

    def test_rotation_truncates_current(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        root = workspace_service._workspace_root(USER)
        trace_path = os.path.join(root, "trace.log")

        with open(trace_path, "w") as f:
            f.write("x" * (600 * 1024))

        workspace_service._rotate_trace(trace_path)

        with open(trace_path) as f:
            content = f.read()
        assert "rotated" in content.lower()
        assert len(content) < 1000

    def test_search_trace_type_filter_audit(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        workspace_service.append_trace(USER, [
            {"type": "user", "content": "please audit my quantum code"},
            {"type": "tool_call", "content": "run_audit(quantum)"},
            {"type": "audit", "content": "quantum audit passed"},
        ])
        results = workspace_service.search_trace(USER, "quantum", type_filter="audit")
        assert len(results) == 1
        excerpt = results[0]["excerpt"]
        assert "[AUDIT]" in excerpt
        assert "[USER]" not in excerpt
        assert "[TOOL_CALL]" not in excerpt

    def test_search_trace_type_filter_tool_call(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        workspace_service.append_trace(USER, [
            {"type": "user", "content": "call the quantum tool"},
            {"type": "tool_call", "content": "run_quantum_tool()"},
            {"type": "audit", "content": "quantum audit note"},
        ])
        results = workspace_service.search_trace(USER, "quantum", type_filter="tool_call")
        assert len(results) == 1
        excerpt = results[0]["excerpt"]
        assert "[TOOL_CALL]" in excerpt
        assert "[USER]" not in excerpt
        assert "[AUDIT]" not in excerpt

    def test_search_trace_no_filter_returns_all(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        workspace_service.append_trace(USER, [
            {"type": "user", "content": "quantum question"},
            {"type": "assistant", "content": "quantum answer"},
            {"type": "audit", "content": "quantum audit log"},
        ])
        results = workspace_service.search_trace(USER, "quantum")
        assert len(results) == 1
        excerpt = results[0]["excerpt"]
        # All three types should appear in the excerpt since no filter.
        assert "[USER]" in excerpt
        assert "[ASSISTANT]" in excerpt
        assert "[AUDIT]" in excerpt

    def test_search_trace_type_filter_case_insensitive(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        workspace_service.append_trace(USER, [
            {"type": "user", "content": "quantum question"},
            {"type": "audit", "content": "quantum audit entry"},
        ])
        results_lower = workspace_service.search_trace(USER, "quantum", type_filter="audit")
        results_upper = workspace_service.search_trace(USER, "quantum", type_filter="AUDIT")
        assert len(results_lower) == 1
        assert len(results_upper) == 1
        assert results_lower[0]["excerpt"] == results_upper[0]["excerpt"]

    def test_search_includes_archived_files(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        root = workspace_service._workspace_root(USER)

        # Write an archived file directly.
        archive_dir = os.path.join(root, "archive", "trace_logs")
        os.makedirs(archive_dir, exist_ok=True)
        with open(os.path.join(archive_dir, "trace.20250101-000000.log"), "w") as f:
            f.write("\n=== 2025-01-01T00:00:00Z ===\n[USER] old quantum discussion\n")

        # Write something different in current trace.
        trace_path = os.path.join(root, "trace.log")
        with open(trace_path, "w") as f:
            f.write("\n=== 2025-03-01T00:00:00Z ===\n[USER] recent topic\n")

        # Search for "quantum" should find the archived entry.
        results = workspace_service.search_trace(USER, "quantum")
        assert len(results) == 1
        assert "quantum" in results[0]["excerpt"]

    def test_rotation_prunes_old_archives(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        root = workspace_service._workspace_root(USER)
        archive_dir = os.path.join(root, "archive", "trace_logs")
        os.makedirs(archive_dir, exist_ok=True)

        # Create 5 old archive files (beyond _TRACE_KEEP_ROTATED=3).
        for i in range(5):
            with open(os.path.join(archive_dir, f"trace.2025010{i}-000000.log"), "w") as f:
                f.write(f"old log {i}")

        # Trigger rotation.
        trace_path = os.path.join(root, "trace.log")
        with open(trace_path, "w") as f:
            f.write("x" * (600 * 1024))

        workspace_service._rotate_trace(trace_path)

        archives = [f for f in os.listdir(archive_dir) if f.endswith(".log")]
        # Should keep only _TRACE_KEEP_ROTATED (3) archives.
        assert len(archives) <= workspace_service._TRACE_KEEP_ROTATED


class TestPluginImport:
    def test_derive_name_from_url(self, ws_dir):
        import re
        url = "https://github.com/someone/cool-tools.git"
        m = re.search(r"/([^/]+?)(?:\.git)?$", url.strip())
        assert m.group(1) == "cool-tools"

    def test_import_nonexistent_repo_fails(self, ws_dir):
        """Importing a bogus URL should fail gracefully."""
        result = workspace_service.import_plugin_repo(
            USER, "https://example.com/nonexistent/repo.git", "test-plugin"
        )
        assert "error" in result

    def test_list_shared_empty(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        assert workspace_service.list_shared_plugins(USER) == []

    def test_remove_nonexistent(self, ws_dir):
        workspace_service._ensure_workspace(USER)
        result = workspace_service.remove_plugin_repo(USER, "nope")
        assert "error" in result
