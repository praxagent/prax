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
