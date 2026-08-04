"""A plan step must record what actually happened, not just that it ended.

`agent_step_done` used to mark every step "done" regardless of outcome, so a
plan whose steps all failed still read as fully completed — the agent lying to
whoever (or whatever) read the plan next. Same family as the Terminal-Bench
finding that 85% of failures declared success.
"""
import pytest

from prax.services import workspace_service


@pytest.fixture
def planned(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_service.settings, "workspace_dir", str(tmp_path))
    uid = "usr_test"
    workspace_service.create_plan(uid, "goal", ["first", "second"])
    return uid


def test_default_outcome_is_done_so_old_callers_are_unchanged(planned):
    r = workspace_service.complete_plan_step(planned, 1)
    assert r["status"] == "done"
    assert r["step"]["outcome"] == "done"
    assert r["step"]["done"] is True


def test_failure_is_recorded_as_failure(planned):
    r = workspace_service.complete_plan_step(planned, 1, outcome="failed")
    assert r["status"] == "failed"
    assert r["step"]["outcome"] == "failed"
    # still 'done' in the resolved sense, so progress counting keeps working
    assert r["step"]["done"] is True


def test_skipped_is_distinct_from_both(planned):
    r = workspace_service.complete_plan_step(planned, 2, outcome="skipped")
    assert r["step"]["outcome"] == "skipped"


def test_bogus_outcome_is_rejected(planned):
    r = workspace_service.complete_plan_step(planned, 1, outcome="probably?")
    assert "error" in r and "done/failed/skipped" in r["error"]


def test_outcome_survives_a_read(planned):
    workspace_service.complete_plan_step(planned, 1, outcome="failed")
    plan = workspace_service.read_plan(planned)
    assert plan["steps"][0]["outcome"] == "failed"


def test_unknown_step_still_errors(planned):
    assert "error" in workspace_service.complete_plan_step(planned, 99)
