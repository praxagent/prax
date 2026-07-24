"""Branch channels — a git branch's work gets one place to live.

Keyless: the naming is pure, and the network paths are exercised against a stub
so no TeamWork instance is required.
"""
from __future__ import annotations

from prax.services.teamwork_service import TeamWorkClient

name_for = TeamWorkClient.branch_channel_name


# ── Naming ───────────────────────────────────────────────────────────────────

def test_a_branch_name_becomes_a_valid_channel_name():
    assert name_for("feat/agent-cli") == "branch-feat-agent-cli"
    assert name_for("main") == "branch-main"


def test_branches_sharing_a_leaf_do_not_collide():
    # The bug a naive "take the last path segment" would have: feat/foo and
    # fix/foo are different branches and must be different channels.
    assert name_for("feat/foo") != name_for("fix/foo")


def test_naming_is_stable_and_case_insensitive():
    assert name_for("Feat/Foo") == name_for("feat/foo")
    assert name_for("  feat/foo  ") == name_for("feat/foo")


def test_awkward_characters_are_normalised():
    assert name_for("release/v1.2.3") == "branch-release-v1-2-3"
    assert name_for("fix/#123_bug!") == "branch-fix-123-bug"


def test_a_degenerate_branch_name_still_yields_a_channel():
    assert name_for("///") == "branch-unnamed"
    assert name_for("") == "branch-unnamed"


def test_the_name_is_bounded():
    assert len(name_for("x" * 500)) <= 100


# ── Behaviour ────────────────────────────────────────────────────────────────

class _Stub(TeamWorkClient):
    """A service with the network replaced, so ensure/post logic is testable."""

    def __init__(self, enabled=True, project="p1"):
        self._enabled = enabled
        self._project_id = project
        self._channels: dict[str, str] = {}
        self.ensured: list[list[dict]] = []
        self.sent: list[tuple[str, str]] = []

    def ensure_channels(self, channels):
        self.ensured.append(channels)
        for ch in channels:
            self._channels[ch["name"]] = f"id-{ch['name']}"

    def send_message(self, content, channel_id=None, agent_name=None, **kw):
        self.sent.append((channel_id, content))


def test_ensuring_a_branch_channel_creates_it_once():
    svc = _Stub()
    first = svc.ensure_branch_channel("feat/foo")
    second = svc.ensure_branch_channel("feat/foo")
    assert first == second == "id-branch-feat-foo"
    # Idempotent: safe to call at the start of every turn touching the branch.
    assert len(svc.ensured) == 1


def test_a_disabled_teamwork_is_a_no_op_not_an_error():
    # A branch channel is a convenience and must never block the actual work.
    svc = _Stub(enabled=False)
    assert svc.ensure_branch_channel("feat/foo") is None
    assert svc.ensured == []


def test_no_project_is_a_no_op():
    assert _Stub(project=None).ensure_branch_channel("feat/foo") is None


def test_posting_an_update_creates_the_channel_and_sends():
    svc = _Stub()
    assert svc.post_branch_update("feat/foo", "CI passed") is True
    assert svc.sent == [("id-branch-feat-foo", "CI passed")]


def test_posting_when_disabled_reports_failure_rather_than_raising():
    assert _Stub(enabled=False).post_branch_update("feat/foo", "hi") is False


def test_a_send_failure_is_swallowed_not_raised():
    svc = _Stub()

    def boom(*a, **k):
        raise RuntimeError("teamwork down")
    svc.send_message = boom
    assert svc.post_branch_update("feat/foo", "hi") is False


def test_different_branches_get_different_channels():
    svc = _Stub()
    a = svc.ensure_branch_channel("feat/foo")
    b = svc.ensure_branch_channel("fix/foo")
    assert a != b
    assert len(svc.ensured) == 2
