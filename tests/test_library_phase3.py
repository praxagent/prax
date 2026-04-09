"""Phase 3 tests: project metadata, notebook sequencing, Kanban tasks
with activity log + reminder integration."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from prax.services import library_service, library_tasks


@pytest.fixture
def user(tmp_path, monkeypatch):
    """Isolated workspace + patched workspace_root."""
    user_id = "test_user"
    ws = tmp_path / user_id
    ws.mkdir()
    monkeypatch.setattr(
        library_service, "workspace_root", lambda _uid: str(ws),
    )
    return user_id


# ---------------------------------------------------------------------------
# Project metadata
# ---------------------------------------------------------------------------

class TestProjectMetadata:
    def test_create_with_all_fields(self, user):
        result = library_service.create_space(
            user, "Learn French",
            description="A6 months to fluency",
            kind="learning",
            target_date="2026-10-01",
            pinned=True,
            reminder_channel="sms",
        )
        assert result["project"]["kind"] == "learning"
        assert result["project"]["pinned"] is True
        assert result["project"]["reminder_channel"] == "sms"
        assert result["project"]["status"] == "active"

    def test_update_status(self, user):
        library_service.create_space(user, "Test")
        result = library_service.update_space(user, "test", status="paused")
        assert result["project"]["status"] == "paused"

    def test_invalid_status_rejected(self, user):
        library_service.create_space(user, "Test")
        result = library_service.update_space(user, "test", status="weird")
        assert "error" in result

    def test_invalid_channel_rejected(self, user):
        library_service.create_space(user, "Test")
        result = library_service.update_space(user, "test", reminder_channel="email")
        assert "error" in result

    def test_pinned_projects_sort_first(self, user):
        library_service.create_space(user, "Zebra", pinned=False)
        library_service.create_space(user, "Alpha", pinned=True)
        projects = library_service.list_spaces(user)
        assert projects[0]["slug"] == "alpha"  # pinned first even though later alphabetically... wait
        # Actually: pinned first, then by name. Alpha is pinned, zebra is not.
        assert projects[0]["pinned"] is True
        assert projects[1]["pinned"] is False

    def test_get_project_with_progress(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health", sequenced=True)
        library_service.create_note(user, "Note A", "body", "personal", "health", status="done")
        library_service.create_note(user, "Note B", "body", "personal", "health", status="todo")
        detail = library_service.get_space(user, "personal")
        assert detail is not None
        assert detail["note_count"] == 2
        assert detail["progress_percent"] == 50


# ---------------------------------------------------------------------------
# Sequenced notebooks
# ---------------------------------------------------------------------------

class TestSequencedNotebooks:
    def test_create_sequenced_notebook(self, user):
        library_service.create_space(user, "School")
        result = library_service.create_notebook(user, "school", "Linear Algebra", sequenced=True)
        assert result["notebook"]["sequenced"] is True

    def test_create_note_auto_assigns_order(self, user):
        library_service.create_space(user, "School")
        library_service.create_notebook(user, "school", "Algebra", sequenced=True)
        library_service.create_note(user, "Intro", "a", "school", "algebra")
        library_service.create_note(user, "Basics", "b", "school", "algebra")
        notes = library_service.list_notes(user, "school", "algebra")
        assert [n["lesson_order"] for n in notes] == [0, 1]

    def test_sequenced_list_sorts_by_order(self, user):
        library_service.create_space(user, "School")
        library_service.create_notebook(user, "school", "Algebra", sequenced=True)
        library_service.create_note(user, "Z first written", "", "school", "algebra", lesson_order=2)
        library_service.create_note(user, "A second", "", "school", "algebra", lesson_order=0)
        library_service.create_note(user, "M third", "", "school", "algebra", lesson_order=1)
        notes = library_service.list_notes(user, "school", "algebra")
        assert [n["slug"] for n in notes] == ["a-second", "m-third", "z-first-written"]

    def test_reorder_notes(self, user):
        library_service.create_space(user, "School")
        library_service.create_notebook(user, "school", "Algebra", sequenced=True)
        library_service.create_note(user, "One", "", "school", "algebra")
        library_service.create_note(user, "Two", "", "school", "algebra")
        library_service.create_note(user, "Three", "", "school", "algebra")
        library_service.reorder_notes(user, "school", "algebra", ["three", "one", "two"])
        notes = library_service.list_notes(user, "school", "algebra")
        assert [n["slug"] for n in notes] == ["three", "one", "two"]

    def test_mark_note_status(self, user):
        library_service.create_space(user, "School")
        library_service.create_notebook(user, "school", "Algebra", sequenced=True)
        library_service.create_note(user, "Intro", "", "school", "algebra")
        result = library_service.set_note_status(user, "school", "algebra", "intro", "done")
        assert result["status"] == "updated"
        note = library_service.get_note(user, "school", "algebra", "intro")
        assert note["meta"]["status"] == "done"

    def test_mark_done_advances_current(self, user):
        library_service.create_space(user, "School")
        library_service.create_notebook(user, "school", "Algebra", sequenced=True)
        library_service.create_note(user, "Lesson 1", "", "school", "algebra")
        library_service.create_note(user, "Lesson 2", "", "school", "algebra")
        library_service.update_notebook(user, "school", "algebra", current_slug="lesson-1")
        library_service.set_note_status(user, "school", "algebra", "lesson-1", "done")
        nb = library_service.get_notebook(user, "school", "algebra")
        assert nb["current_slug"] == "lesson-2"

    def test_toggling_sequenced_on_backfills_order(self, user):
        library_service.create_space(user, "School")
        library_service.create_notebook(user, "school", "Free", sequenced=False)
        # Create a few notes in non-sequenced mode (they still get lesson_order)
        library_service.create_note(user, "Alpha", "", "school", "free")
        library_service.create_note(user, "Beta", "", "school", "free")
        result = library_service.update_notebook(user, "school", "free", sequenced=True)
        assert result["notebook"]["sequenced"] is True
        notes = library_service.list_notes(user, "school", "free")
        assert all(isinstance(n.get("lesson_order"), int) for n in notes)


# ---------------------------------------------------------------------------
# Kanban tasks — CRUD + columns + activity log
# ---------------------------------------------------------------------------

class TestKanbanTasks:
    def _setup(self, user):
        library_service.create_space(user, "Business")

    def test_default_columns_seeded(self, user):
        self._setup(user)
        cols = library_tasks.list_columns(user, "business")
        assert isinstance(cols, list)
        assert [c["id"] for c in cols] == ["todo", "doing", "done"]

    def test_create_task_defaults(self, user):
        self._setup(user)
        result = library_tasks.create_task(user, "business", title="Write spec")
        assert result["status"] == "created"
        t = result["task"]
        assert t["column"] == "todo"
        assert t["author"] == "human"
        assert t["assignees"] == []
        assert len(t["activity"]) == 1
        assert t["activity"][0]["action"] == "created"

    def test_move_task_appends_activity(self, user):
        self._setup(user)
        result = library_tasks.create_task(user, "business", title="Task")
        task_id = result["task"]["id"]
        library_tasks.move_task(user, "business", task_id, "doing", editor="prax")
        task = library_tasks.get_task(user, "business", task_id)
        assert task["column"] == "doing"
        assert len(task["activity"]) == 2
        assert task["activity"][1]["action"] == "moved"
        assert task["activity"][1]["from"] == "todo"
        assert task["activity"][1]["to"] == "doing"
        assert task["activity"][1]["actor"] == "prax"

    def test_update_task_tracks_changed_fields(self, user):
        self._setup(user)
        result = library_tasks.create_task(user, "business", title="Task")
        task_id = result["task"]["id"]
        update = library_tasks.update_task(
            user, "business", task_id,
            title="Task v2",
            description="new desc",
            editor="human",
        )
        assert update["status"] == "updated"
        assert "title" in update["changed"]
        assert "description" in update["changed"]
        task = library_tasks.get_task(user, "business", task_id)
        assert task["activity"][-1]["action"] == "updated"

    def test_delete_task(self, user):
        self._setup(user)
        result = library_tasks.create_task(user, "business", title="Task")
        task_id = result["task"]["id"]
        library_tasks.delete_task(user, "business", task_id)
        assert library_tasks.get_task(user, "business", task_id) is None

    def test_comment_appends_to_activity_and_comments(self, user):
        self._setup(user)
        result = library_tasks.create_task(user, "business", title="Task")
        task_id = result["task"]["id"]
        library_tasks.add_comment(user, "business", task_id, "This is blocked", actor="human")
        task = library_tasks.get_task(user, "business", task_id)
        assert len(task["comments"]) == 1
        assert task["comments"][0]["text"] == "This is blocked"
        assert task["activity"][-1]["action"] == "commented"

    def test_assignees(self, user):
        self._setup(user)
        result = library_tasks.create_task(
            user, "business", title="Task",
            assignees=["prax", "human"],
        )
        assert result["task"]["assignees"] == ["prax", "human"]

    def test_invalid_column_on_create(self, user):
        self._setup(user)
        result = library_tasks.create_task(user, "business", title="Task", column="ghost")
        assert "error" in result

    def test_missing_project(self, user):
        result = library_tasks.create_task(user, "ghost", title="Task")
        assert "error" in result


# ---------------------------------------------------------------------------
# Columns — CRUD
# ---------------------------------------------------------------------------

class TestColumns:
    def _setup(self, user):
        library_service.create_space(user, "Business")

    def test_add_column(self, user):
        self._setup(user)
        result = library_tasks.add_column(user, "business", "Blocked")
        assert result["status"] == "added"
        assert result["column"]["id"] == "blocked"

    def test_rename_column(self, user):
        self._setup(user)
        result = library_tasks.rename_column(user, "business", "todo", "Backlog")
        assert result["status"] == "renamed"
        cols = library_tasks.list_columns(user, "business")
        assert any(c["id"] == "todo" and c["name"] == "Backlog" for c in cols)

    def test_remove_empty_column(self, user):
        self._setup(user)
        library_tasks.add_column(user, "business", "Temp")
        result = library_tasks.remove_column(user, "business", "temp")
        assert result["status"] == "removed"

    def test_remove_nonempty_column_refused(self, user):
        self._setup(user)
        library_tasks.add_column(user, "business", "Temp")
        library_tasks.create_task(user, "business", title="Task", column="temp")
        result = library_tasks.remove_column(user, "business", "temp")
        assert "error" in result

    def test_duplicate_column_rejected(self, user):
        self._setup(user)
        library_tasks.add_column(user, "business", "Blocked")
        result = library_tasks.add_column(user, "business", "Blocked")
        assert "error" in result


# ---------------------------------------------------------------------------
# Reminder integration (mocked scheduler)
# ---------------------------------------------------------------------------

class TestReminderIntegration:
    def _setup(self, user):
        library_service.create_space(user, "Business", reminder_channel="sms")

    def test_task_with_due_date_creates_reminder(self, user):
        self._setup(user)
        with patch("prax.services.scheduler_service.create_reminder") as mock_create:
            mock_create.return_value = {"reminder": {"id": "rem-123"}}
            result = library_tasks.create_task(
                user, "business",
                title="Ship feature",
                due_date="2027-01-01T17:00:00+00:00",
            )
        assert mock_create.called
        # Verify channel came from project default
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["channel"] == "sms"
        assert result["task"]["reminder_id"] == "rem-123"

    def test_task_without_due_date_no_reminder(self, user):
        self._setup(user)
        with patch("prax.services.scheduler_service.create_reminder") as mock_create:
            library_tasks.create_task(user, "business", title="No date")
        assert not mock_create.called

    def test_disabling_reminder_skips_scheduling(self, user):
        self._setup(user)
        with patch("prax.services.scheduler_service.create_reminder") as mock_create:
            library_tasks.create_task(
                user, "business",
                title="Silent",
                due_date="2027-01-01T17:00:00+00:00",
                reminder_enabled=False,
            )
        assert not mock_create.called

    def test_moving_to_done_cancels_reminder(self, user):
        self._setup(user)
        with patch("prax.services.scheduler_service.create_reminder") as mock_create, \
             patch("prax.services.scheduler_service.delete_reminder") as mock_del:
            mock_create.return_value = {"reminder": {"id": "rem-123"}}
            result = library_tasks.create_task(
                user, "business",
                title="Task",
                due_date="2027-01-01T17:00:00+00:00",
            )
            task_id = result["task"]["id"]
            library_tasks.move_task(user, "business", task_id, "done", editor="human")
        assert mock_del.called

    def test_deleting_task_cancels_reminder(self, user):
        self._setup(user)
        with patch("prax.services.scheduler_service.create_reminder") as mock_create, \
             patch("prax.services.scheduler_service.delete_reminder") as mock_del:
            mock_create.return_value = {"reminder": {"id": "rem-123"}}
            result = library_tasks.create_task(
                user, "business",
                title="Task",
                due_date="2027-01-01T17:00:00+00:00",
            )
            library_tasks.delete_task(user, "business", result["task"]["id"])
        assert mock_del.called

    def test_changing_due_date_reschedules(self, user):
        self._setup(user)
        with patch("prax.services.scheduler_service.create_reminder") as mock_create, \
             patch("prax.services.scheduler_service.delete_reminder") as mock_del:
            mock_create.return_value = {"reminder": {"id": "rem-123"}}
            result = library_tasks.create_task(
                user, "business",
                title="Task",
                due_date="2027-01-01T17:00:00+00:00",
            )
            task_id = result["task"]["id"]
            library_tasks.update_task(
                user, "business", task_id,
                due_date="2027-02-01T17:00:00+00:00",
            )
        assert mock_del.called
        assert mock_create.call_count == 2  # once on create, once on reschedule

    def test_per_task_channel_override(self, user):
        self._setup(user)  # project default is sms
        with patch("prax.services.scheduler_service.create_reminder") as mock_create:
            mock_create.return_value = {"reminder": {"id": "rem-x"}}
            library_tasks.create_task(
                user, "business",
                title="Task",
                due_date="2027-01-01T17:00:00+00:00",
                reminder_channel="discord",
            )
        assert mock_create.call_args.kwargs["channel"] == "discord"


# ---------------------------------------------------------------------------
# Smoke: news is gone
# ---------------------------------------------------------------------------

class TestProactiveEngagement:
    def _human_note(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Journal")
        library_service.create_note(
            user, "My thoughts", "body", "personal", "journal",
            author="human", prax_may_edit=False,
        )

    def test_unlocking_human_note_queues_engagement(self, user):
        self._human_note(user)
        library_service.set_prax_may_edit(user, "personal", "journal", "my-thoughts", True)
        pending = library_service.peek_pending_engagements(user)
        assert len(pending) == 1
        assert pending[0]["slug"] == "my-thoughts"

    def test_locking_back_drains_engagement(self, user):
        self._human_note(user)
        library_service.set_prax_may_edit(user, "personal", "journal", "my-thoughts", True)
        library_service.set_prax_may_edit(user, "personal", "journal", "my-thoughts", False)
        assert library_service.peek_pending_engagements(user) == []

    def test_unlocking_prax_note_does_not_queue(self, user):
        library_service.create_space(user, "Personal")
        library_service.create_notebook(user, "personal", "Health")
        library_service.create_note(
            user, "Sleep tips", "body", "personal", "health",
            author="prax", prax_may_edit=True,
        )
        library_service.set_prax_may_edit(user, "personal", "health", "sleep-tips", True)
        assert library_service.peek_pending_engagements(user) == []

    def test_pop_drains_queue(self, user):
        self._human_note(user)
        library_service.set_prax_may_edit(user, "personal", "journal", "my-thoughts", True)
        drained = library_service.pop_pending_engagements(user)
        assert len(drained) == 1
        assert library_service.peek_pending_engagements(user) == []

    def test_duplicate_unlocks_dedupe(self, user):
        self._human_note(user)
        library_service.set_prax_may_edit(user, "personal", "journal", "my-thoughts", True)
        # Toggle off and back on — the queue should still have only one entry.
        library_service.set_prax_may_edit(user, "personal", "journal", "my-thoughts", False)
        library_service.set_prax_may_edit(user, "personal", "journal", "my-thoughts", True)
        pending = library_service.peek_pending_engagements(user)
        assert len(pending) == 1


class TestTagNormalization:
    def _setup(self, user):
        library_service.create_space(user, "School")
        library_service.create_notebook(user, "school", "Math")

    def test_strips_leading_hash(self, user):
        self._setup(user)
        library_service.create_note(
            user, "A", "body", "school", "math", tags=["#math", "#algebra"],
        )
        note = library_service.get_note(user, "school", "math", "a")
        assert note["meta"]["tags"] == ["math", "algebra"]

    def test_lowercases(self, user):
        self._setup(user)
        library_service.create_note(
            user, "B", "body", "school", "math", tags=["Math", "ALGEBRA"],
        )
        note = library_service.get_note(user, "school", "math", "b")
        assert note["meta"]["tags"] == ["math", "algebra"]

    def test_trims_whitespace(self, user):
        self._setup(user)
        library_service.create_note(
            user, "C", "body", "school", "math", tags=["  math ", "algebra  "],
        )
        note = library_service.get_note(user, "school", "math", "c")
        assert note["meta"]["tags"] == ["math", "algebra"]

    def test_collapses_double_slash(self, user):
        self._setup(user)
        library_service.create_note(
            user, "D", "body", "school", "math", tags=["math//algebra//linear"],
        )
        note = library_service.get_note(user, "school", "math", "d")
        assert note["meta"]["tags"] == ["math/algebra/linear"]

    def test_dedupes(self, user):
        self._setup(user)
        library_service.create_note(
            user, "E", "body", "school", "math", tags=["math", "MATH", "#math"],
        )
        note = library_service.get_note(user, "school", "math", "e")
        assert note["meta"]["tags"] == ["math"]

    def test_update_normalizes(self, user):
        self._setup(user)
        library_service.create_note(user, "F", "body", "school", "math", tags=["x"])
        library_service.update_note(
            user, "school", "math", "f", tags=["#y", "#Y"],
        )
        note = library_service.get_note(user, "school", "math", "f")
        assert note["meta"]["tags"] == ["y"]

    def test_empty_string_dropped(self, user):
        self._setup(user)
        library_service.create_note(
            user, "G", "body", "school", "math", tags=["math", "", "  ", "#"],
        )
        note = library_service.get_note(user, "school", "math", "g")
        assert note["meta"]["tags"] == ["math"]

    def test_tag_tree_uses_normalized_form(self, user):
        self._setup(user)
        library_service.create_note(
            user, "H", "body", "school", "math", tags=["#Math/Algebra"],
        )
        tree = library_service.list_tag_tree(user)
        assert "math" in tree["children"]
        assert "algebra" in tree["children"]["math"]["children"]


class TestScheduleHealthCheck:
    def test_creates_schedule(self, user, monkeypatch):
        from prax.services import scheduler_service

        captured = {}

        def fake_create_schedule(**kwargs):
            captured.update(kwargs)
            return {"status": "created", "schedule": {"id": "sched-123", **kwargs}}

        monkeypatch.setattr(scheduler_service, "create_schedule", fake_create_schedule)

        result = library_service.schedule_health_check(
            user, cron_expr="0 9 * * 1", channel="sms",
        )
        assert result["status"] == "created"
        assert captured["cron_expr"] == "0 9 * * 1"
        assert captured["channel"] == "sms"
        assert "library_health_check" in captured["prompt"]

    def test_default_cron_and_channel(self, user, monkeypatch):
        from prax.services import scheduler_service

        captured = {}

        def fake(**kwargs):
            captured.update(kwargs)
            return {"status": "created", "schedule": {"id": "s", **kwargs}}

        monkeypatch.setattr(scheduler_service, "create_schedule", fake)

        library_service.schedule_health_check(user)
        assert captured["cron_expr"] == "0 9 * * 1"
        assert captured["channel"] == "all"


class TestHugoPublishingRefactor:
    def test_reexports_from_course_service_still_work(self):
        """course_service re-exports the Hugo primitives for back-compat."""
        from prax.services import course_service
        assert hasattr(course_service, "hugo_site_dir")
        assert hasattr(course_service, "ensure_hugo_site")
        assert hasattr(course_service, "run_hugo")
        assert hasattr(course_service, "courses_dir")
        assert hasattr(course_service, "find_course_site_public_dir")
        assert hasattr(course_service, "get_course_site_public_dir")
        assert hasattr(course_service, "KATEX_HEAD")
        assert hasattr(course_service, "THEME_CSS")

    def test_hugo_publishing_module_has_canonical_homes(self):
        from prax.services import hugo_publishing
        assert callable(hugo_publishing.hugo_site_dir)
        assert callable(hugo_publishing.ensure_hugo_site)
        assert callable(hugo_publishing.run_hugo)
        assert callable(hugo_publishing.courses_dir)
        assert callable(hugo_publishing.find_course_site_public_dir)
        assert callable(hugo_publishing.get_course_site_public_dir)

    def test_note_service_imports_from_hugo_publishing(self):
        """publish_notes should reach hugo_publishing directly now, not course_service."""
        import inspect

        from prax.services import note_service
        src = inspect.getsource(note_service.publish_notes)
        assert "from prax.services.hugo_publishing import" in src


class TestLearningProject:
    def test_create_with_modules(self, user):
        result = library_service.create_learning_space(
            user,
            subject="Linear Algebra",
            title="LA for ML",
            modules=[
                {"title": "Vectors and spaces"},
                {"title": "Matrices", "description": "Linear maps"},
                {"title": "Eigenvalues", "topics": ["eigen", "spectral"]},
            ],
        )
        assert result["status"] == "created"
        assert result["project"]["kind"] == "learning"
        assert result["notebook"]["sequenced"] is True
        assert len(result["lessons"]) == 3
        assert result["lessons"][0]["lesson_order"] == 0
        assert result["lessons"][1]["lesson_order"] == 1
        assert result["lessons"][2]["lesson_order"] == 2
        assert all(n["status"] == "todo" for n in result["lessons"])

    def test_create_with_no_modules(self, user):
        result = library_service.create_learning_space(
            user, subject="Rust",
        )
        assert result["status"] == "created"
        assert result["project"]["kind"] == "learning"
        assert result["notebook"]["sequenced"] is True
        assert result["lessons"] == []

    def test_first_lesson_becomes_current(self, user):
        result = library_service.create_learning_space(
            user,
            subject="French",
            modules=[{"title": "Bonjour"}, {"title": "Numbers"}],
        )
        nb = library_service.get_notebook(
            user,
            result["project"]["slug"],
            result["notebook"]["slug"],
        )
        assert nb["current_slug"] == result["lessons"][0]["slug"]

    def test_custom_notebook_name(self, user):
        result = library_service.create_learning_space(
            user,
            subject="Knitting",
            notebook_name="Patterns",
            modules=[{"title": "Cast on"}],
        )
        assert result["notebook"]["slug"] == "patterns"

    def test_topics_rendered_in_lesson_body(self, user):
        result = library_service.create_learning_space(
            user,
            subject="ML",
            modules=[{"title": "Intro", "topics": ["supervised", "unsupervised"]}],
        )
        note = library_service.get_note(
            user,
            result["project"]["slug"],
            result["notebook"]["slug"],
            result["lessons"][0]["slug"],
        )
        assert "supervised" in note["content"]
        assert "unsupervised" in note["content"]


class TestNestedTags:
    def _setup(self, user):
        library_service.create_space(user, "School")
        library_service.create_notebook(user, "school", "Math")
        library_service.create_note(
            user, "Linear Algebra",
            "body",
            "school", "math",
            tags=["math/algebra/linear"],
        )
        library_service.create_note(
            user, "Abstract Algebra",
            "body",
            "school", "math",
            tags=["math/algebra/abstract"],
        )
        library_service.create_note(
            user, "Calculus",
            "body",
            "school", "math",
            tags=["math/calculus"],
        )
        library_service.create_note(
            user, "Python",
            "body",
            "school", "math",
            tags=["programming/python"],
        )

    def test_tag_tree_nesting(self, user):
        self._setup(user)
        tree = library_service.list_tag_tree(user)
        # math has 3 descendants (linear, abstract, calculus)
        assert "math" in tree["children"]
        assert tree["children"]["math"]["total"] == 3
        # math/algebra has 2 leaves
        assert "algebra" in tree["children"]["math"]["children"]
        assert tree["children"]["math"]["children"]["algebra"]["total"] == 2
        # math/calculus is a leaf
        assert tree["children"]["math"]["children"]["calculus"]["count"] == 1

    def test_filter_by_tag_prefix(self, user):
        self._setup(user)
        math_notes = library_service.list_notes_by_tag_prefix(user, "math")
        assert len(math_notes) == 3
        algebra_notes = library_service.list_notes_by_tag_prefix(user, "math/algebra")
        assert len(algebra_notes) == 2
        linear_notes = library_service.list_notes_by_tag_prefix(user, "math/algebra/linear")
        assert len(linear_notes) == 1
        assert linear_notes[0]["slug"] == "linear-algebra"

    def test_empty_prefix_returns_all(self, user):
        self._setup(user)
        all_notes = library_service.list_notes_by_tag_prefix(user, "")
        assert len(all_notes) == 4


class TestAutoCaptureRaw:
    def test_captures_url_message(self, user):
        from prax.services import sms_service
        text = "save this for later: https://example.com/paper/123"
        slug = sms_service._maybe_auto_capture_raw(user, text)
        assert slug is not None
        raw = library_service.list_raw(user)
        assert len(raw) == 1
        assert raw[0]["source_url"] == "https://example.com/paper/123"

    def test_ignores_messages_without_urls(self, user):
        from prax.services import sms_service
        slug = sms_service._maybe_auto_capture_raw(user, "just a text message")
        assert slug is None

    def test_skips_pdf_urls(self, user):
        from prax.services import sms_service
        slug = sms_service._maybe_auto_capture_raw(
            user, "https://arxiv.org/pdf/2410.12345.pdf"
        )
        # PDF handling has its own flow; auto-capture should NOT fire.
        assert slug is None


class TestAgentPlanEndpointShape:
    """Smoke tests for the /teamwork/agent-plan widget's denormalization
    of workspace_service.read_plan output."""

    def test_denorm_when_no_plan(self, user, monkeypatch):
        """When read_plan returns None, the response is simply None."""
        from prax.services import workspace_service

        monkeypatch.setattr(workspace_service, "read_plan", lambda _uid: None)

        # Re-implement the endpoint's denormalization so we don't need a
        # Flask test client — we just verify the logic.
        plan = workspace_service.read_plan(user)
        result = None if plan is None else {
            "id": plan.get("id"),
            "goal": plan.get("goal", ""),
            "steps": plan.get("steps", []),
        }
        assert result is None

    def test_denorm_with_active_plan(self, user, monkeypatch):
        """With an active plan, response includes done_count, total, current_step."""
        from prax.services import workspace_service

        fake_plan = {
            "id": "plan-abc",
            "goal": "Ship feature X",
            "steps": [
                {"step": 1, "description": "Write spec", "done": True},
                {"step": 2, "description": "Implement", "done": False},
                {"step": 3, "description": "Ship it", "done": False},
            ],
            "created_at": "2026-04-08T10:00:00Z",
        }
        monkeypatch.setattr(workspace_service, "read_plan", lambda _uid: fake_plan)

        plan = workspace_service.read_plan(user)
        steps = plan.get("steps", [])
        done_count = sum(1 for s in steps if s.get("done"))
        current = next((s for s in steps if not s.get("done")), None)
        result = {
            "id": plan.get("id"),
            "goal": plan.get("goal", ""),
            "steps": steps,
            "done_count": done_count,
            "total": len(steps),
            "current_step": current,
            "created_at": plan.get("created_at"),
        }
        assert result["done_count"] == 1
        assert result["total"] == 3
        assert result["current_step"]["step"] == 2
        assert result["current_step"]["description"] == "Implement"

    def test_denorm_all_done(self, user, monkeypatch):
        """When every step is done, current_step is None."""
        from prax.services import workspace_service

        fake_plan = {
            "id": "plan-abc",
            "goal": "Done",
            "steps": [
                {"step": 1, "description": "Finished", "done": True},
            ],
            "created_at": "2026-04-08T10:00:00Z",
        }
        monkeypatch.setattr(workspace_service, "read_plan", lambda _uid: fake_plan)

        plan = workspace_service.read_plan(user)
        steps = plan.get("steps", [])
        current = next((s for s in steps if not s.get("done")), None)
        assert current is None


class TestNewsRemoved:
    def test_no_news_functions_in_note_service(self):
        from prax.services import note_service
        assert not hasattr(note_service, "list_news")
        assert not hasattr(note_service, "create_news_briefing")
        assert not hasattr(note_service, "publish_news")
        assert not hasattr(note_service, "search_news")

    def test_content_routes_gone(self):
        # /teamwork/content/* endpoints should be removed from the Flask app
        import importlib
        teamwork_routes = importlib.import_module("prax.blueprints.teamwork_routes")
        # The helpers that handled content (list_content, etc.) are gone
        for sym in ("list_content", "get_content_item", "search_content"):
            assert not hasattr(teamwork_routes, sym), f"{sym} should have been removed"
