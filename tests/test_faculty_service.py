"""Tests for the Faculty service — professor roster + adaptive course state.

LLM and scheduler boundaries are monkeypatched; the real Library learning-space
machinery and the calibration logic are exercised for real.
"""
from __future__ import annotations

import pytest

from prax.services import faculty_service as fs
from prax.settings import settings

USER = "faculty_test_user"


@pytest.fixture(autouse=True)
def _tmp_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    # Skip best-effort AI cover generation (no network / no API key in CI).
    monkeypatch.setenv("AUTO_GENERATE_COVER", "false")
    yield


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch):
    """Replace the LLM-backed helpers with deterministic stubs."""
    monkeypatch.setattr(fs, "_generate_outline", lambda prof, subject, level_idx, goal="": [
        {"title": "Lesson One", "description": "intro"},
        {"title": "Lesson Two", "description": "more"},
        {"title": "Lesson Three", "description": "even more"},
    ])
    monkeypatch.setattr(
        fs, "_draft_lesson",
        lambda prof, **kw: f"# body for {kw['lesson_title']} @ {kw['target_level'][:6]}",
    )
    monkeypatch.setattr(fs, "_generate_quiz", lambda prof, subject, topics, level: {
        "question": "What is 2+2?", "answer_key": "4", "topic": "arithmetic",
    })
    monkeypatch.setattr(fs, "_grade_answer", lambda prof, q, key, ans: {
        "verdict": "correct", "signal": "ok", "feedback": "Nicely done.",
    })
    yield


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

class TestRoster:
    def test_builtin_roster(self):
        roster = fs.list_professors(USER)
        ids = {p["id"] for p in roster}
        assert {"athena", "ramos", "okonkwo", "rivera", "whitfield", "tanaka"} <= ids
        for p in roster:
            # every professor has real character + a teaching method
            assert p["personality"] and p["teaching_style"] and p["pedagogy"]

    def test_get_professor(self):
        assert fs.get_professor(USER, "athena")["name"].startswith("Professor Athena")
        assert fs.get_professor(USER, "nope") is None

    def test_create_custom_professor(self):
        prof = fs.create_professor(
            USER, "Doc Sardonicus", personality="dryly sarcastic", pedagogy="Socratic",
        )
        assert prof["custom"] is True
        # persists and shows up in the roster + resolves by id
        assert fs.get_professor(USER, prof["id"])["name"] == "Doc Sardonicus"
        assert any(p["id"] == prof["id"] for p in fs.list_professors(USER))


# ---------------------------------------------------------------------------
# Calibration (pure logic)
# ---------------------------------------------------------------------------

class TestCalibration:
    def test_too_easy_raises_level(self):
        enr = {"level_index": 1, "calibration": fs._new_calibration()}
        fs._recalibrate(enr, "too_easy")
        assert enr["level_index"] == 2

    def test_two_easies_jump_two(self):
        enr = {"level_index": 0, "calibration": fs._new_calibration()}
        fs._recalibrate(enr, "too_easy")  # -> 1, streak 1
        fs._recalibrate(enr, "too_easy")  # streak 2 -> +2 -> 3
        assert enr["level_index"] == 3

    def test_struggled_lowers_and_says_smaller_iteration(self):
        enr = {"level_index": 3, "calibration": fs._new_calibration()}
        directive = fs._recalibrate(enr, "struggled")
        assert enr["level_index"] == 2
        assert "smaller iteration" in directive.lower()

    def test_level_clamped(self):
        enr = {"level_index": fs._MAX_LEVEL, "calibration": fs._new_calibration()}
        fs._recalibrate(enr, "too_easy")
        assert enr["level_index"] == fs._MAX_LEVEL
        enr2 = {"level_index": 0, "calibration": fs._new_calibration()}
        fs._recalibrate(enr2, "struggled")
        assert enr2["level_index"] == 0

    def test_level_name_clamps(self):
        assert fs.level_name(-5) == fs.DIFFICULTY_LADDER[0]
        assert fs.level_name(999) == fs.DIFFICULTY_LADDER[-1]


# ---------------------------------------------------------------------------
# Enrollment + the adaptive loop
# ---------------------------------------------------------------------------

class TestEnrollAndTeach:
    def test_enroll_drafts_only_first_lesson(self):
        res = fs.enroll(USER, "arithmetic", "ramos", goal="get fast at mental math")
        assert "error" not in res
        slug = res["space_slug"]
        notebook = res["enrollment"]["notebook"]
        assert res["outline"] == ["Lesson One", "Lesson Two", "Lesson Three"]

        # First lesson body is drafted; the rest remain stubs.
        from prax.services import library_service
        notes = sorted(
            library_service.list_notes(USER, project=slug, notebook=notebook),
            key=lambda n: n.get("lesson_order", 0),
        )
        assert len(notes) == 3
        body1 = library_service.get_note(USER, slug, notebook, notes[0]["slug"])["content"]
        assert body1.startswith("# body for Lesson One")
        # enrollment persisted with the chosen professor
        st = fs.status(USER, slug)
        assert st["professor_id"] == "ramos"
        assert st["progress"]["total"] == 3

    def test_record_progress_advances_and_recalibrates(self):
        res = fs.enroll(USER, "arithmetic", "athena")
        slug = res["space_slug"]

        prog = fs.record_progress(USER, slug, "too_easy")
        assert prog["recorded"] == "too_easy"
        assert prog["next_lesson_title"] == "Lesson Two"
        # difficulty went up
        assert "advanced" in prog["target_level"] or "intermediate" in prog["target_level"]

        nxt = fs.next_lesson(USER, slug)
        assert nxt["lesson_title"] == "Lesson Two"
        assert nxt["body"].startswith("# body for Lesson Two")

    def test_course_completes(self):
        res = fs.enroll(USER, "x", "tanaka")
        slug = res["space_slug"]
        fs.record_progress(USER, slug, "ok")   # -> lesson 2
        fs.record_progress(USER, slug, "ok")   # -> lesson 3
        done = fs.record_progress(USER, slug, "ok")  # -> complete
        assert done["completed"] is True
        assert fs.status(USER, slug)["status"] == "completed"

    def test_resolve_active_when_slug_blank(self):
        res = fs.enroll(USER, "blank-resolve", "okonkwo")
        slug = res["space_slug"]
        # blank slug resolves to the most recent active enrollment
        nxt = fs.next_lesson(USER, "")
        assert nxt["space_slug"] == slug


# ---------------------------------------------------------------------------
# Pop quizzes
# ---------------------------------------------------------------------------

class TestQuizzes:
    def test_pop_quiz_sets_pending_and_grade_clears(self):
        res = fs.enroll(USER, "arithmetic", "tanaka")
        slug = res["space_slug"]

        quiz = fs.pop_quiz(USER, slug)
        assert quiz["question"] == "What is 2+2?"

        pending = fs.has_pending_quiz(USER, slug)
        assert pending and pending["slug"] == slug and pending["question"] == "What is 2+2?"

        graded = fs.grade_quiz(USER, slug, "4")
        assert graded["verdict"] == "correct"
        assert "done" in graded["feedback"].lower()
        # pending cleared after grading
        assert fs.has_pending_quiz(USER, slug) is None

    def test_grade_without_pending(self):
        res = fs.enroll(USER, "x", "rivera")
        assert "error" in fs.grade_quiz(USER, res["space_slug"], "whatever")

    def test_grade_persists_recalibration(self, monkeypatch):
        # A clearly-missed quiz should LOWER the persisted difficulty (regression
        # for the lost-write bug where _recalibrate ran on a stale object).
        monkeypatch.setattr(fs, "_grade_answer", lambda prof, q, key, ans: {
            "verdict": "incorrect", "signal": "struggled", "feedback": "Let's revisit that.",
        })
        res = fs.enroll(USER, "calc", "tanaka", starting_level=3)
        slug = res["space_slug"]
        before = fs.status(USER, slug)["target_level"]
        fs.pop_quiz(USER, slug)
        fs.grade_quiz(USER, slug, "no idea")
        after = fs.status(USER, slug)["target_level"]
        assert before != after  # the level change actually persisted to disk

    def test_optout_clears_pending(self, monkeypatch):
        monkeypatch.setattr(fs, "_linked_channels", lambda uid: {"discord"})
        from prax.services import scheduler_service
        monkeypatch.setattr(scheduler_service, "create_schedule",
                            lambda *a, **k: {"schedule": {"id": "s1"}})
        monkeypatch.setattr(scheduler_service, "delete_schedule", lambda *a, **k: {})
        res = fs.enroll(USER, "x", "rivera")
        slug = res["space_slug"]
        fs.set_quiz_optin(USER, slug, enabled=True)
        fs.pop_quiz(USER, slug)
        assert fs.has_pending_quiz(USER, slug) is not None
        fs.set_quiz_optin(USER, slug, enabled=False)
        assert fs.has_pending_quiz(USER, slug) is None  # opt-out stops the nag


class TestAdaptiveGuards:
    def test_next_lesson_does_not_redraft_current(self, monkeypatch):
        calls = {"n": 0}
        orig = fs._draft_lesson
        monkeypatch.setattr(fs, "_draft_lesson", lambda prof, **kw: (calls.__setitem__("n", calls["n"] + 1) or orig(prof, **kw)))
        res = fs.enroll(USER, "x", "athena")  # drafts lesson 1 (1 call)
        slug = res["space_slug"]
        assert calls["n"] == 1
        # Re-asking before recording progress must NOT redraft lesson 1.
        nxt = fs.next_lesson(USER, slug)
        assert nxt["already_drafted"] is True
        assert calls["n"] == 1  # no second draft of the same lesson

    def test_record_progress_no_current_lesson(self):
        res = fs.enroll(USER, "x", "athena")
        slug = res["space_slug"]
        for _ in range(3):
            fs.record_progress(USER, slug, "ok")  # completes the 3-lesson course
        out = fs.record_progress(USER, slug, "ok")  # nothing left to record
        assert "error" in out

    def test_session_prompt_is_in_character(self):
        res = fs.enroll(USER, "geometry", "okonkwo")
        sess = fs.professor_session_prompt(USER, res["space_slug"])
        assert "error" not in sess
        assert "Maya Okonkwo" in sess["system_prompt"]
        assert res["space_slug"] in sess["system_prompt"]  # course slug threaded in
        assert sess["professor"]["id"] == "okonkwo"


class TestQuizOptIn:
    def _patch_scheduler(self, monkeypatch, linked=("discord",)):
        monkeypatch.setattr(fs, "_linked_channels", lambda uid: set(linked))
        from prax.services import scheduler_service
        created = {}
        monkeypatch.setattr(
            scheduler_service, "create_schedule",
            lambda uid, description, prompt, cron_expr, channel=None, **k: (
                created.update({"channel": channel, "cron": cron_expr, "desc": description})
                or {"schedule": {"id": "sched_1"}}
            ),
        )
        monkeypatch.setattr(scheduler_service, "delete_schedule", lambda uid, sid: {"deleted": sid})
        return created

    def test_optin_creates_schedule(self, monkeypatch):
        created = self._patch_scheduler(monkeypatch)
        res = fs.enroll(USER, "spanish", "okonkwo")
        slug = res["space_slug"]

        out = fs.set_quiz_optin(USER, slug, enabled=True, frequency="weekdays")
        assert out["enabled"] is True
        assert out["channel"] == "discord"
        assert created["channel"] == "discord"
        assert created["cron"] == fs._FREQUENCY_CRON["weekdays"]
        # persisted
        st = fs.status(USER, slug)
        assert st["quiz"]["enabled"] is True

    def test_optin_rejects_unlinked_channel(self, monkeypatch):
        self._patch_scheduler(monkeypatch, linked=("discord",))
        res = fs.enroll(USER, "spanish", "okonkwo")
        out = fs.set_quiz_optin(USER, res["space_slug"], enabled=True, channel="sms")
        assert "error" in out  # sms not linked -> consent gate blocks it

    def test_optin_no_channel_linked(self, monkeypatch):
        self._patch_scheduler(monkeypatch, linked=())
        res = fs.enroll(USER, "spanish", "okonkwo")
        out = fs.set_quiz_optin(USER, res["space_slug"], enabled=True)
        assert "error" in out

    def test_optout_disables(self, monkeypatch):
        self._patch_scheduler(monkeypatch)
        res = fs.enroll(USER, "spanish", "okonkwo")
        slug = res["space_slug"]
        fs.set_quiz_optin(USER, slug, enabled=True)
        out = fs.set_quiz_optin(USER, slug, enabled=False)
        assert out["enabled"] is False
        assert fs.status(USER, slug)["quiz"]["enabled"] is False
