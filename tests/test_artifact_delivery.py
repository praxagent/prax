"""The delivery-path contract, pinned after a live failure.

The trace (2026-08-06, Discord): a sandbox spoke wrote a valid MP3 to
``/workspace/active/`` — which, under the real mount (``workspaces/`` →
``/workspace``), is a directory belonging to NO user — and the orchestrator
then answered "link please" twice with a filename in backticks. Three
components each held a piece of the failure:

* the spoke prompt called ``/workspace/active/`` "the app's shared workspace";
* ``workspace_send_file`` could not resolve a container-absolute path under
  the user's own directory;
* nothing told the orchestrator, deterministically, that a deliverable
  artifact existed.

These tests pin the fixed contract for all three.
"""

from unittest.mock import patch

import pytest

from prax.agent.spokes import _runner
from prax.agent.spokes.sandbox import agent as sandbox_agent
from prax.agent.user_context import current_turn_captures, current_user_id


@pytest.fixture
def user_root(tmp_path, monkeypatch):
    """A fake workspaces/<user-dir> with active/ inside, wired into services."""
    root = tmp_path / "workspaces" / "usr_test1"
    (root / "active").mkdir(parents=True)

    from prax.services import workspace_service

    monkeypatch.setattr(workspace_service, "workspace_root", lambda _uid: str(root))
    monkeypatch.setattr(workspace_service, "_workspace_root", lambda _uid: str(root))
    return root


class TestSendFileResolvesContainerPaths:
    def _resolve(self, filename, root):
        """Run just the resolution logic of workspace_send_file by calling it
        with delivery stubbed to capture the resolved path."""
        from prax.agent import workspace_tools

        captured = {}

        def fake_send(uid, file_path, message=""):
            captured["path"] = file_path

        with patch("prax.services.discord_service.send_file", fake_send), \
             patch.object(workspace_tools, "_get_user_id", lambda: "usr_test1"):
            result = workspace_tools.workspace_send_file.func(filename)
        return result, captured.get("path")

    def test_container_absolute_path_under_own_dir_resolves(self, user_root):
        """The exact live shape: /workspace/<own-dir>/active/<file>."""
        target = user_root / "active" / "narration.mp3"
        target.write_bytes(b"ID3fake")
        result, path = self._resolve("/workspace/usr_test1/active/narration.mp3",
                                     user_root)
        assert path == str(target)
        assert "Sent" in result

    def test_bare_filename_still_resolves_from_active(self, user_root):
        target = user_root / "active" / "report.pdf"
        target.write_bytes(b"%PDF")
        result, path = self._resolve("report.pdf", user_root)
        assert path == str(target)

    def test_another_users_directory_is_not_reachable(self, user_root):
        """Stripping applies ONLY to the caller's own dir — a path under a
        different user's directory must not resolve."""
        other = user_root.parent / "usr_other" / "active"
        other.mkdir(parents=True)
        (other / "secret.txt").write_text("theirs")
        result, path = self._resolve("/workspace/usr_other/active/secret.txt",
                                     user_root)
        assert path is None
        assert "not found" in result

    def test_the_no_user_scratch_is_not_reachable(self, user_root):
        """/workspace/active (the live failure's landing spot) belongs to no
        user; send_file must not silently serve it."""
        scratch = user_root.parent / "active"
        scratch.mkdir()
        (scratch / "stranded.mp3").write_bytes(b"ID3")
        result, path = self._resolve("/workspace/active/stranded.mp3", user_root)
        assert path is None


class TestPinnedInputs:
    def _task(self, captures):
        tok_c = current_turn_captures.set(captures)
        tok_u = current_user_id.set("usr_test1")
        try:
            return _runner._apply_pinned_inputs("Narrate the report as MP3")
        finally:
            current_turn_captures.reset(tok_c)
            current_user_id.reset(tok_u)

    def test_nothing_captured_leaves_the_task_untouched(self, user_root):
        """The common case — pinning must not perturb ordinary delegations."""
        assert self._task(()) == "Narrate the report as MP3"

    def test_capture_is_pinned_with_container_path(self, user_root):
        out = self._task(("20260806-220342-cdn-discordapp",))
        assert "PINNED INPUTS" in out
        assert "library/raw/20260806-220342-cdn-discordapp.md" in out
        # The container path names the USER's directory, not /workspace/active.
        assert "/workspace/usr_test1/library/raw/" in out
        assert "do not search the workspace" in out


class TestDeliveryHint:
    def _hint(self, result):
        return sandbox_agent._append_delivery_hint(result, "usr_test1")

    def test_existing_artifact_gets_a_verified_hint(self, user_root):
        (user_root / "active" / "a.mp3").write_bytes(b"x")
        out = self._hint("Made /workspace/usr_test1/active/a.mp3 for you.")
        assert "workspace_send_file('active/a.mp3')" in out
        assert "NOT a delivery" in out

    def test_a_path_that_does_not_exist_gets_no_hint(self, user_root):
        """The hint is disk-verified — it must never assert a deliverable the
        spoke merely claimed."""
        out = self._hint("Made /workspace/usr_test1/active/ghost.mp3")
        assert "workspace_send_file" not in out

    def test_paths_outside_the_users_dir_get_no_hint(self, user_root):
        scratch = user_root.parent / "active"
        scratch.mkdir(exist_ok=True)
        (scratch / "stray.mp3").write_bytes(b"x")
        out = self._hint("Made /workspace/active/stray.mp3")
        assert "workspace_send_file" not in out


class TestSpokePromptUsesPerUserPath:
    def test_prompt_formats_the_users_container_dir(self, user_root):
        ws = sandbox_agent._container_user_workspace("usr_test1")
        assert ws == "/workspace/usr_test1"
        prompt = sandbox_agent.SYSTEM_PROMPT.format(
            agent_name="Prax", user_workspace=ws)
        assert "/workspace/usr_test1/active/" in prompt
        # The old no-user path survives only as an explicit warning.
        assert "Do NOT write user artifacts to /workspace/active/" in prompt
