"""Tests for codegen_service — staging clone + verify + deploy."""
import importlib
import os

import pytest


@pytest.fixture()
def cg_mod(monkeypatch, tmp_path):
    """Reload codegen_service with self-improvement enabled.

    Creates a fake git repo as the "live" repo.  The staging clone and
    worktrees are created under tmp_path so nothing touches the real FS.
    """
    live_repo = tmp_path / "live"
    staging = tmp_path / "staging"

    monkeypatch.setenv("SELF_IMPROVE_ENABLED", "true")
    monkeypatch.setenv("SELF_IMPROVE_REPO_PATH", str(live_repo))

    import prax.settings as settings_mod
    importlib.reload(settings_mod)

    module = importlib.reload(
        importlib.import_module("prax.services.codegen_service")
    )
    monkeypatch.setattr(module.settings, "self_improve_enabled", True)
    monkeypatch.setattr(module.settings, "self_improve_repo_path", str(live_repo))

    # Reset module state.
    module._worktrees.clear()
    module._staging_repo = None

    # Point the staging path to our tmp location.
    monkeypatch.setattr(module, "_staging_path", lambda: str(staging))

    # Create a fake "live" git repo.
    live_repo.mkdir()
    os.system(f"git init {live_repo} --quiet")
    os.system(f"git -C {live_repo} config user.email test@test.com")
    os.system(f"git -C {live_repo} config user.name Test")
    (live_repo / "README.md").write_text("# Test")
    os.system(f"git -C {live_repo} add -A && git -C {live_repo} commit -m 'init' --quiet")

    return module


@pytest.fixture()
def cg_disabled(monkeypatch):
    """Reload with self-improvement disabled."""
    monkeypatch.setenv("SELF_IMPROVE_ENABLED", "false")
    import prax.settings as settings_mod
    importlib.reload(settings_mod)

    module = importlib.reload(
        importlib.import_module("prax.services.codegen_service")
    )
    monkeypatch.setattr(module.settings, "self_improve_enabled", False)
    return module


# ---------- Feature flag ---------------------------------------------------

class TestFeatureFlag:
    def test_disabled_blocks_all(self, cg_disabled):
        assert "error" in cg_disabled.start_branch("test")
        assert "error" in cg_disabled.read_file("test", "f")
        assert "error" in cg_disabled.write_file("test", "f", "c")
        assert "error" in cg_disabled.run_tests("test")
        assert "error" in cg_disabled.run_lint("test")
        assert "error" in cg_disabled.verify_startup("test")
        assert "error" in cg_disabled.verify_and_deploy("test")
        assert "error" in cg_disabled.submit_pr("test", "t")
        assert "error" in cg_disabled.cleanup_branch("test")
        assert "error" in cg_disabled.list_branches()


# ---------- Staging clone -------------------------------------------------

class TestStagingClone:
    def test_ensure_staging_creates_clone(self, cg_mod, tmp_path):
        staging = cg_mod._ensure_staging()
        assert os.path.isdir(staging)
        assert os.path.isdir(os.path.join(staging, ".git"))
        # README should be present in clone.
        assert os.path.isfile(os.path.join(staging, "README.md"))

    def test_ensure_staging_idempotent(self, cg_mod):
        s1 = cg_mod._ensure_staging()
        s2 = cg_mod._ensure_staging()
        assert s1 == s2

    def test_start_branch_uses_staging(self, cg_mod, tmp_path):
        result = cg_mod.start_branch("feature-a")
        assert result["status"] == "created"
        wt = result["worktree_path"]
        # Worktree should NOT be under the live repo.
        live = str(tmp_path / "live")
        assert not wt.startswith(live)
        # But it should have the repo contents.
        assert os.path.isfile(os.path.join(wt, "README.md"))


# ---------- Worktree lifecycle ---------------------------------------------

class TestWorktreeLifecycle:
    def test_start_creates_worktree(self, cg_mod):
        result = cg_mod.start_branch("fix-typo", "Fix a typo in README")
        assert result["status"] == "created"
        assert "self-improve/fix-typo" in result["branch"]
        assert os.path.isdir(result["worktree_path"])

    def test_duplicate_branch_rejected(self, cg_mod):
        cg_mod.start_branch("dupe")
        result = cg_mod.start_branch("dupe")
        assert "error" in result
        assert "already" in result["error"]

    def test_cleanup_removes_worktree(self, cg_mod):
        cg_mod.start_branch("cleanup-test")
        result = cg_mod.cleanup_branch("cleanup-test")
        assert result["status"] == "cleaned_up"

    def test_cleanup_nonexistent(self, cg_mod):
        result = cg_mod.cleanup_branch("nonexistent")
        assert "error" in result


# ---------- File operations ------------------------------------------------

class TestFileOps:
    def test_read_file(self, cg_mod):
        cg_mod.start_branch("read-test")
        result = cg_mod.read_file("read-test", "README.md")
        assert "content" in result
        assert "Test" in result["content"]

    def test_read_nonexistent(self, cg_mod):
        cg_mod.start_branch("read-missing")
        result = cg_mod.read_file("read-missing", "nonexistent.py")
        assert "error" in result

    def test_write_file(self, cg_mod):
        cg_mod.start_branch("write-test")
        result = cg_mod.write_file("write-test", "new_file.py", "print('hello')")
        assert result["status"] == "written"
        assert result["size"] == len("print('hello')")

        # Verify it was actually written.
        read = cg_mod.read_file("write-test", "new_file.py")
        assert "hello" in read["content"]

    def test_write_creates_dirs(self, cg_mod):
        cg_mod.start_branch("mkdir-test")
        result = cg_mod.write_file("mkdir-test", "deep/nested/file.txt", "content")
        assert result["status"] == "written"

    def test_no_worktree(self, cg_mod):
        result = cg_mod.read_file("nonexistent", "f.py")
        assert "error" in result


# ---------- Tests and lint ------------------------------------------------

class TestRunTests:
    def test_runs_in_worktree(self, cg_mod):
        cg_mod.start_branch("test-runner")
        # This will likely fail since the worktree doesn't have deps,
        # but it should not crash the service itself.
        result = cg_mod.run_tests("test-runner")
        assert "status" in result
        assert result["status"] in ("passed", "failed")

    def test_lint_in_worktree(self, cg_mod):
        cg_mod.start_branch("lint-test")
        result = cg_mod.run_lint("lint-test")
        assert "status" in result


# ---------- Verify startup ------------------------------------------------

class TestVerifyStartup:
    def test_verify_no_worktree(self, cg_mod):
        result = cg_mod.verify_startup("nonexistent")
        assert "error" in result

    def test_verify_runs(self, cg_mod):
        cg_mod.start_branch("startup-test")
        result = cg_mod.verify_startup("startup-test")
        # May fail (no deps in worktree), but should return valid structure.
        assert result["status"] in ("passed", "failed")
        assert "return_code" in result


# ---------- Verify and deploy ---------------------------------------------

class TestVerifyAndDeploy:
    def test_no_changes(self, cg_mod):
        cg_mod.start_branch("no-changes")
        result = cg_mod.verify_and_deploy("no-changes")
        assert "error" in result
        assert "No changes" in result["error"]

    def test_deploy_copies_files(self, cg_mod, monkeypatch, tmp_path):
        """Test the hot-swap with mocked verification steps."""
        cg_mod.start_branch("deploy-test")
        cg_mod.write_file("deploy-test", "new_feature.py", "# new feature\n")

        # Mock verification to always pass.
        monkeypatch.setattr(cg_mod, "run_tests", lambda b: {"status": "passed"})
        monkeypatch.setattr(cg_mod, "run_lint", lambda b: {"status": "clean"})
        monkeypatch.setattr(cg_mod, "verify_startup", lambda b: {"status": "passed"})

        result = cg_mod.verify_and_deploy("deploy-test", "add new feature")
        assert result["status"] == "deployed"
        assert "new_feature.py" in result["files_changed"]

        # Verify the file was copied to the live repo.
        live = tmp_path / "live"
        assert (live / "new_feature.py").exists()
        assert "new feature" in (live / "new_feature.py").read_text()

    def test_deploy_fails_on_tests(self, cg_mod, monkeypatch):
        cg_mod.start_branch("fail-test")
        cg_mod.write_file("fail-test", "bad.py", "# bad code\n")

        monkeypatch.setattr(
            cg_mod, "run_tests",
            lambda b: {"status": "failed", "stdout": "FAIL", "stderr": ""},
        )

        result = cg_mod.verify_and_deploy("fail-test")
        assert "error" in result
        assert result["stage"] == "tests"

    def test_deploy_fails_on_lint(self, cg_mod, monkeypatch):
        cg_mod.start_branch("fail-lint")
        cg_mod.write_file("fail-lint", "bad.py", "# lint issues\n")

        monkeypatch.setattr(cg_mod, "run_tests", lambda b: {"status": "passed"})
        monkeypatch.setattr(
            cg_mod, "run_lint",
            lambda b: {"status": "issues_found", "output": "E501"},
        )

        result = cg_mod.verify_and_deploy("fail-lint")
        assert "error" in result
        assert result["stage"] == "lint"

    def test_deploy_fails_on_startup(self, cg_mod, monkeypatch):
        cg_mod.start_branch("fail-startup")
        cg_mod.write_file("fail-startup", "bad.py", "# breaks import\n")

        monkeypatch.setattr(cg_mod, "run_tests", lambda b: {"status": "passed"})
        monkeypatch.setattr(cg_mod, "run_lint", lambda b: {"status": "clean"})
        monkeypatch.setattr(
            cg_mod, "verify_startup",
            lambda b: {"status": "failed", "stderr": "ImportError"},
        )

        result = cg_mod.verify_and_deploy("fail-startup")
        assert "error" in result
        assert result["stage"] == "startup"

    def test_deploy_handles_deleted_files(self, cg_mod, monkeypatch, tmp_path):
        """Deleted files should be removed from the live repo."""
        live = tmp_path / "live"
        # Add a file to live repo that we'll "delete" in the worktree.
        (live / "obsolete.py").write_text("# to be removed")
        os.system(f"git -C {live} add -A && git -C {live} commit -m 'add obsolete' --quiet")

        # Re-sync staging clone to pick up the new file.
        cg_mod._staging_repo = None
        cg_mod.start_branch("del-test")
        wt = cg_mod._worktrees["del-test"]

        # Remove the file in worktree.
        obsolete = os.path.join(wt, "obsolete.py")
        if os.path.exists(obsolete):
            os.remove(obsolete)

        monkeypatch.setattr(cg_mod, "run_tests", lambda b: {"status": "passed"})
        monkeypatch.setattr(cg_mod, "run_lint", lambda b: {"status": "clean"})
        monkeypatch.setattr(cg_mod, "verify_startup", lambda b: {"status": "passed"})

        result = cg_mod.verify_and_deploy("del-test", "remove obsolete file")
        assert result["status"] == "deployed"
        assert "obsolete.py" in result["files_deleted"]
        assert not (live / "obsolete.py").exists()
